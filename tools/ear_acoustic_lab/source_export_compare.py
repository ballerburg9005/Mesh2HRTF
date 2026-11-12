"""Compare original STL face geometry with NumCalc's exported Reference mesh.

Unlike trimesh.is_watertight with process=False, topology is computed after
welding coincident coordinates at a caller-chosen tolerance. Does not change
any STL, Blender, Reference or solver files.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np
import trimesh

from topology_audit import _load_reference


def _triangle_key(points, scale, tolerance_m):
    vertices = tuple(
        tuple(int(round(float(coord) * scale / tolerance_m))
              for coord in vertex)
        for vertex in points)
    return tuple(sorted(vertices))


def _welded_topology(face_keys):
    """Diagnose shared-vertex edge incidence irrespective of STL vertex IDs."""
    edge_owners = defaultdict(list)
    degenerate = 0
    for idx, tri in enumerate(face_keys):
        if len(set(tri)) < 3:
            degenerate += 1
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            edge = tuple(sorted((a, b)))
            edge_owners[edge].append(idx)
    # _triangle_key sorts the coordinates for face equivalence only; calling
    # this on sorted keys cannot establish winding. Edge incidence is valid.
    boundary = {edge for edge, owners in edge_owners.items()
                if len(owners) == 1}
    nonmanifold = {edge for edge, owners in edge_owners.items()
                   if len(owners) > 2}
    return {
        "open_boundary_edges": len(boundary),
        "nonmanifold_edges": len(nonmanifold),
        "degenerate_triangles": degenerate,
    }, boundary | nonmanifold


def compare_source_export(project, stl=None, tolerance_mm=0.0001):
    """Return and save a face-by-face geometrical comparison, without a solve."""
    project = Path(project).expanduser().resolve()
    cfg = json.loads((project / "earlab_config.json").read_text())
    source_path = Path(stl or cfg["stl"]).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(
            "Original STL not present: " + str(source_path) +
            " (supply --stl to use another file)")
    if not (0 < tolerance_mm <= 0.1):
        raise ValueError("tolerance_mm must be in (0, 0.1]")
    tolerance_m = tolerance_mm * 0.001
    units = cfg.get("units", "mm")
    if units not in ("mm", "m"):
        raise ValueError("Unknown STL units: " + str(units))
    scale = 0.001 if units == "mm" else 1.0

    mesh = trimesh.load_mesh(str(source_path), process=False)
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    if not hasattr(mesh, "triangles") or len(mesh.triangles) == 0:
        raise ValueError("Empty or unsupported STL triangle mesh")
    original_face_keys = [
        _triangle_key(tri, scale, tolerance_m)
        for tri in np.asarray(mesh.triangles, dtype=float)
    ]
    nodes, elements = _load_reference(project)
    exported_face_keys = [
        _triangle_key([nodes[vid] for vid in v], 1.0, tolerance_m)
        for v in elements.values()
    ]
    original_faces = Counter(original_face_keys)
    exported_faces = Counter(exported_face_keys)
    missing = original_faces - exported_faces
    added = exported_faces - original_faces
    original_edge_counts, original_bad_edges = _welded_topology(original_face_keys)
    exported_edge_counts, exported_bad_edges = _welded_topology(exported_face_keys)
    # Edge incidence is based on geometric coincidence, not file index.
    source_inherited = original_bad_edges & exported_bad_edges
    introduced = exported_bad_edges - original_bad_edges

    def examples(counter, limit=15):
        result = []
        for face, count in counter.items():
            if len(result) >= limit:
                break
            centroid = [sum(vertex[i] for vertex in face) *
                        tolerance_m * 1000 / 3 for i in range(3)]
            result.append({
                "centroid_mm": [round(float(x), 5) for x in centroid],
                "multiplicity": count,
            })
        return result

    report = {
        "source_stl": str(source_path),
        "project": str(project),
        "matching_tolerance_mm": tolerance_mm,
        "original_triangles": len(original_face_keys),
        "exported_triangles": len(exported_face_keys),
        "exact_face_matches_at_tolerance":
            sum((original_faces & exported_faces).values()),
        "triangles_only_in_stl": sum(missing.values()),
        "triangles_only_in_export": sum(added.values()),
        "stl_welded_edge_topology": original_edge_counts,
        "numcalc_export_welded_edge_topology": exported_edge_counts,
        "bad_edges_shared_by_source_and_export": len(source_inherited),
        "bad_edges_present_only_in_export": len(introduced),
        "stl_only_face_examples": examples(missing),
        "export_only_face_examples": examples(added),
        "note": (
            "Geometric triangle matching after quantizing vertices to the "
            "given tolerance. STL is a triangle soup, and raw trimesh "
            "watertightness with process=False can be misleading. "
            "The STL and NumCalc reference can differ due to import "
            "validation, triangulation, or unit/axis conversions. "
            "Matching topology alone does not validate acoustic physics."),
    }
    path = project / "analysis" / "stl_vs_numcalc_geometry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report, path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--stl", default=None)
    parser.add_argument("--tolerance-mm", default=0.0001, type=float)
    args = parser.parse_args()
    r, path = compare_source_export(
        args.project, args.stl, args.tolerance_mm)
    print(f"[SOURCE] Original STL {r['original_triangles']} triangles; "
          f"NumCalc export {r['exported_triangles']} triangles.")
    print(f"[SOURCE] Matched geometrically: "
          f"{r['exact_face_matches_at_tolerance']}; "
          f"STL only: {r['triangles_only_in_stl']}; "
          f"export only: {r['triangles_only_in_export']}.")
    print("[SOURCE] STL welded edge topology: " +
          json.dumps(r["stl_welded_edge_topology"]))
    print("[SOURCE] Export welded edge topology: " +
          json.dumps(r["numcalc_export_welded_edge_topology"]))
    print(f"[SOURCE] Defective edges inherited: "
          f"{r['bad_edges_shared_by_source_and_export']}; "
          f"present only in export: {r['bad_edges_present_only_in_export']}")
    print("[SOURCE] Report: " + str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
