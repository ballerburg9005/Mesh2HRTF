"""Blender helper: save a copy of the exact exported mesh with bad faces selected.

Invoked in background with the original '3d Model.blend' loaded first.
"""
import argparse
import json
import sys
from pathlib import Path

import bpy


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    project = Path(parser.parse_args(args).project)
    summary = json.loads((project / "mesh_quality.json").read_text())
    ids = set(summary["fatal_ids"])
    if not ids:
        return
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    obj = bpy.data.objects.get("Reference")
    if obj is None:
        raise RuntimeError("No Reference mesh in saved project")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for polygon in obj.data.polygons:
        polygon.select = polygon.index in ids
        polygon.hide = False
    if any(index >= len(obj.data.polygons) for index in ids):
        raise ValueError("Reported NumCalc element number exceeds Blender face count")
    result = project / "mesh_problem_faces.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(result), check_existing=False)
    print(f"[MESH QUALITY] Saved selected bad faces in Blender file: {result}")


if __name__ == "__main__":
    main()
