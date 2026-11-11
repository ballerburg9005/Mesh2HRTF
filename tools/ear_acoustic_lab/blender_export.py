"""Executed by Blender, not by normal Python. Exports a real NumCalc project."""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def get_args():
    args = sys.argv[sys.argv.index('--') + 1:]
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    return p.parse_args(args)


def _resize_microphone_grid(grid, xyz, units, radius_mm):
    """Write a tiny valid evaluation triangle centred exactly on the mic.

    Size is capped at one quarter of the nearest wall distance; this is a
    numerical grid, not a physical microphone capsule.
    """
    scale = .001 if units == 'mm' else 1.0
    x, y, z = (float(v) * scale for v in xyz)
    delta = radius_mm * .001
    nodes = ((x-delta, y-delta/3, z),
             (x+delta, y-delta/3, z),
             (x, y+2*delta/3, z))
    grid = Path(grid)
    grid.joinpath('Nodes.txt').write_text(
        '3\n' + ''.join(
            f'{10000000+i} {a:.14f} {b:.14f} {c:.14f}\n'
            for i, (a, b, c) in enumerate(nodes)))
    grid.joinpath('Elements.txt').write_text(
        '1\n10000000 10000000 10000001 10000002 2 0 1\n')


def main():
    import bpy
    from mathutils import Vector

    cfg = json.loads(Path(get_args().config).read_text())
    root = Path(cfg['mesh2hrtf']).expanduser().resolve()
    plugin = root / 'mesh2hrtf' / 'Mesh2Input' / 'mesh2input.py'
    if not plugin.is_file():
        raise FileNotFoundError('Mesh2HRTF Blender exporter missing: ' + str(plugin))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    stl = str(Path(cfg['stl']).expanduser().resolve())
    if hasattr(bpy.ops.wm, 'stl_import'):
        bpy.ops.wm.stl_import(filepath=stl)
    elif hasattr(bpy.ops.import_mesh, 'stl'):
        bpy.ops.import_mesh.stl(filepath=stl)
    else:
        raise RuntimeError('This Blender installation lacks an STL importer')
    objects = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    if not objects:
        raise ValueError('STL import yielded no mesh')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects: obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = 'Reference'

    # Ensure all polygons are triangles, a NumCalc requirement.
    import bmesh
    bm = bmesh.new(); bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data); bm.free()
    obj.data.update()
    skin = bpy.data.materials.get('Skin') or bpy.data.materials.new('Skin')
    obj.data.materials.clear()
    obj.data.materials.append(skin)
    for poly in obj.data.polygons: poly.material_index = 0

    # Near-wall microphone points are legal. The old arbitrary 0.15 mm
    # cutoff rejected them before NumCalc started. We shrink the three-node
    # evaluation triangle to remain small relative to wall clearance.
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromObject(obj, bpy.context.evaluated_depsgraph_get())
    mm_per_unit = 1.0 if cfg['units'] == 'mm' else 1000.0
    distances = {}
    for label, coords in [('microphone', cfg['mic']), ('source', cfg['source'])]:
        hit = tree.find_nearest(Vector(coords))
        if hit[0] is not None:
            d_mm = float(hit[3]) * mm_per_unit
            distances[label] = d_mm
            print(f'[GEOMETRY] {label} nearest solid boundary {d_mm:.4f} mm', flush=True)
            if d_mm < 0.001:
                raise ValueError(
                    f'{label} lies on or within 0.001 mm of a mesh surface. '
                    'Move it into the intended air space or correct the STL origin.')
            if d_mm < 0.15:
                print(
                    f'[WARNING] {label} is only {d_mm:.4f} mm from the mesh. '
                    'Proceeding, but near-wall pressure may have significant numerical error. '
                    'Unsigned distance cannot establish whether the point is in air or PLA.',
                    flush=True)
    radius_mm = min(0.1, 0.25 * distances.get('microphone', 0.4))
    _resize_microphone_grid(Path(cfg['grid']), cfg['mic'], cfg['units'], radius_mm)
    print(f'[GEOMETRY] Mic evaluation triangle radius: {radius_mm:.5f} mm',
          flush=True)
    (Path(cfg['project']) / 'geometry_check.json').write_text(json.dumps({
        'nearest_boundary_mm': distances,
        'mic_probe_triangle_radius_mm': radius_mm,
        'inside_outside_validated': False,
        'note': 'Nearest-wall distances do not establish that the microphone is in connected air.'
    }, indent=2))

    light_data = bpy.data.lights.new(name='Point source', type='POINT')
    point = bpy.data.objects.new('Point source', light_data)
    bpy.context.collection.objects.link(point)
    point.location = tuple(cfg['source'])
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    spec = importlib.util.spec_from_file_location('mesh2hrtf_blender_export', str(plugin))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register()
    project = str(Path(cfg['project']).resolve()) + os.sep
    print('[EXPORT] Source coordinates: ' + str(cfg['source']))
    print('[EXPORT] Microphone coordinates: ' + str(cfg['mic']))
    print('[EXPORT] Point source -> MicProbe grid, rigid surfaces')
    result = bpy.ops.mesh2input.inp(
        filepath=project,
        title='Ear acoustic cavity and microphone response',
        programPath=str(root / 'mesh2hrtf'),
        method='ML-FMM BEM',
        speedOfSound='343.0',
        densityOfMedium='1.2',
        sourceType='Point source',
        unit=cfg['units'],
        minFrequency=float(cfg['min_freq']),
        maxFrequency=float(cfg['max_freq']),
        frequencyVectorType=cfg.get('export_frequency_type', 'Step size'),
        frequencyVectorValue=float(cfg.get(
            'export_frequency_value', cfg['step_freq'])),
        evaluationGrids=str(Path(cfg['grid']).resolve()),
        pictures=False,
        reference=False,
        computeHRIRs=False,
    )
    print('[EXPORT RESULT] ' + str(result))
    if result != {'FINISHED'}:
        raise RuntimeError('Mesh2HRTF export failed: ' + str(result))
    assert (Path(cfg['project']) / 'NumCalc' / 'source_1' / 'NC.inp').is_file()


if __name__ == '__main__':
    main()