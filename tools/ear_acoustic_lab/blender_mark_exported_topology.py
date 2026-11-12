"""Blender-only: mark exact NumCalc problematic edges in saved 3d Model.blend.

Run with: blender --background "3d Model.blend" --python THIS_SCRIPT --
          --project PATH_TO_RUN

Unlike importing a standalone OBJ, this reads *exact* Reference polygon and
vertex indices from the saved .blend, verifies their coordinates against the
NumCalc export, then adds native Blender curve overlays. This has no STL/OBJ
axis conversion ambiguity. Original .blend is never overwritten.
"""
import argparse
import json
from pathlib import Path
import sys


def args_from_blender():
    params = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(params)


def run():
    import bpy
    from mathutils import Vector

    args = args_from_blender()
    project = Path(args.project).expanduser().resolve()
    saved_blend = project / "3d Model.blend"
    if not saved_blend.is_file():
        raise FileNotFoundError("Saved Blender export not found: " + str(saved_blend))
    if Path(bpy.data.filepath).resolve() != saved_blend:
        raise RuntimeError(
            "Open the run's saved '3d Model.blend' first. Current Blender file: "
            + bpy.data.filepath)
    report_path = project / "geometry_topology.json"
    report = json.loads(report_path.read_text())
    if not report["issue_edges"]:
        print("[BLENDER TOPOLOGY] No exceptional edges to highlight.")
        return

    import os
    # Uses the normal Python import machinery when called from Blender.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from topology_audit import _load_reference

    nodes, triangles = _load_reference(project)
    mesh_object = bpy.data.objects.get("Reference")
    if mesh_object is None or mesh_object.type != 'MESH':
        raise ValueError("Saved Blender scene has no 'Reference' mesh")
    mesh = mesh_object.data
    if len(mesh.vertices) != len(nodes) or len(mesh.polygons) != len(triangles):
        raise ValueError(
            "Saved Blender mesh vertex/face count differs from NumCalc export; "
            "cannot mark faces reliably.")
    cfg = json.loads((project / "earlab_config.json").read_text())
    factor = 0.001 if cfg["units"] == "mm" else 1.0

    max_error = 0.0
    for vertex_id, position in nodes.items():
        world = mesh_object.matrix_world @ mesh.vertices[vertex_id].co
        discrepancy = max(abs(float(world[k]) * factor - position[k])
                          for k in range(3))
        if discrepancy > max_error:
            max_error = discrepancy
    # Blender stores mesh vertices as float32; allow 0.01 mm coordinate
    # roundoff, but reject transformations that would misplace the overlay.
    if max_error > 0.00001:
        raise RuntimeError(
            f"Saved Blender coordinates disagree with NumCalc by up to "
            f"{max_error*1000:.4f} mm. Refusing to draw misaligned markers.")

    affected = set()
    for issue in report["issue_edges"]:
        for tid in issue["triangle_ids"]:
            if tid not in triangles or tid >= len(mesh.polygons):
                raise ValueError(f"Invalid NumCalc triangle id {tid}")
            if set(mesh.polygons[tid].vertices) != set(triangles[tid]):
                raise RuntimeError(
                    f"NumCalc triangle {tid} does not map to the same "
                    "polygon in the saved Blender scene.")
            affected.add(tid)

    colors = {
        "nonmanifold": (1.0, 0.08, 0.06, 1.0),
        "open_boundary": (1.0, 0.55, 0.04, 1.0),
        "winding_conflict": (0.06, 0.35, 1.0, 1.0),
    }
    objects = []
    for kind, color in colors.items():
        problematic = [edge for edge in report["issue_edges"]
                       if edge["kind"] == kind]
        if not problematic:
            continue
        curve = bpy.data.curves.new("TOPOLOGY_" + kind, type='CURVE')
        curve.dimensions = '3D'
        curve.resolution_u = 1
        curve.bevel_depth = 0.16 if cfg["units"] == "mm" else 0.00016
        curve.bevel_resolution = 2
        for edge in problematic:
            spline = curve.splines.new('POLY')
            spline.points.add(1)
            for i, vid in enumerate(edge["vertex_ids"]):
                pt = mesh.vertices[vid].co
                spline.points[i].co = (float(pt.x), float(pt.y),
                                       float(pt.z), 1.0)
        material = bpy.data.materials.new("TOPOLOGY_" + kind)
        material.diffuse_color = color
        material.use_nodes = True
        nodes_mat = material.node_tree.nodes
        nodes_mat.clear()
        em = nodes_mat.new('ShaderNodeEmission')
        em.inputs['Color'].default_value = color
        em.inputs['Strength'].default_value = 2.0
        out = nodes_mat.new('ShaderNodeOutputMaterial')
        material.node_tree.links.new(em.outputs['Emission'], out.inputs['Surface'])
        curve.materials.append(material)
        new_object = bpy.data.objects.new("TOPOLOGY_" + kind.upper(), curve)
        bpy.context.scene.collection.objects.link(new_object)
        new_object.matrix_world = mesh_object.matrix_world.copy()
        new_object.show_in_front = True
        new_object.color = color
        objects.append(new_object)

    # Frame the diagnostics when the new file is opened.
    bpy.ops.object.select_all(action='DESELECT')
    for object in objects:
        object.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.shading.color_type = 'OBJECT'

    target = project / "analysis" / "topology_inspection.blend"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.overwrite:
        raise FileExistsError(
            str(target) + " already exists; pass --overwrite to replace")
    bpy.ops.wm.save_as_mainfile(filepath=str(target), check_existing=False,
                               compress=True)
    print("[BLENDER TOPOLOGY] Native axis-correct markers added.")
    print(f"[BLENDER TOPOLOGY] {len(report['issue_edges'])} edges; "
          f"{len(affected)} affected triangles.")
    print("[BLENDER TOPOLOGY] Max coordinate discrepancy: "
          + f"{max_error*1000:.6f} mm")
    print("[BLENDER TOPOLOGY] Open " + str(target))
    print("[BLENDER TOPOLOGY] Original .blend and STL were not changed.")


if __name__ == "__main__":
    run()
