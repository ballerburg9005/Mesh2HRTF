"""Diagnose exactly the triangle-shape limits in NumCalc's NC_Input.cpp.

Reads Mesh2Input's exported Reference/Nodes.txt and Elements.txt, rather than
guessing how the STL triangle order changed during Blender import. Produces a
CSV report and colored OBJ overlays that can be inspected in Blender.
"""
import csv
import json
from pathlib import Path

import numpy as np


TRIANGLE_AREA_REFERENCE = 1.51967  # NumCalc NC_WriteParameters, NC_Input.cpp
WARNING_RATIO = 10.0
FATAL_RATIO = 100.0


def analyze_exported_mesh(project, units="mm"):
    project = Path(project)
    base = project / "ObjectMeshes" / "Reference"
    node_path = base / "Nodes.txt"
    element_path = base / "Elements.txt"
    if not node_path.is_file() or not element_path.is_file():
        raise FileNotFoundError(f"Missing exported Reference mesh: {base}")
    unit_scale = 1000.0 if units == "mm" else 1.0  # NumCalc coordinates are metres

    with node_path.open() as f:
        count = int(f.readline().strip())
        nodes = {}
        for line in f:
            v = line.split()
            if v:
                nodes[int(v[0])] = np.array([float(x) for x in v[1:4]])
    if len(nodes) != count:
        raise ValueError(f"Exported mesh expected {count} nodes, found {len(nodes)}")

    findings = []
    longest_edges_mm = []
    element_count = 0
    with element_path.open() as f:
        expected = int(f.readline().strip())
        for line in f:
            v = line.split()
            if not v:
                continue
            element_count += 1
            element_id = int(v[0])
            # Mesh2Input writes 3 vertex indices followed by 3 metadata items.
            # NumCalc also accepts 4-node quadrilaterals; the launcher triangulates.
            ids = [int(x) for x in v[1:-3]]
            if len(ids) != 3:
                continue
            try:
                pts = np.asarray([nodes[i] for i in ids], float)
            except KeyError as exc:
                raise ValueError(f"Element {element_id} has nonexistent vertex") from exc
            edges = np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)
            longest_edges_mm.append(float(max(edges) * 1000.))
            area = float(np.linalg.norm(np.cross(pts[1] - pts[0],
                                                 pts[2] - pts[0])) * .5)
            ref_length = TRIANGLE_AREA_REFERENCE * np.sqrt(area)
            ratio = float(max(edges) / ref_length) if ref_length > 0 else float("inf")
            if not np.isfinite(ratio) or ratio > WARNING_RATIO:
                findings.append({
                    "element_id": element_id,
                    "grade": "FATAL" if ratio > FATAL_RATIO else "WARN",
                    "ratio": ratio,
                    "max_edge_mm": float(max(edges) * 1000.),
                    "area_mm2": area * 1e6,
                    "centroid_x_mm": float(np.mean(pts[:, 0]) * 1000.),
                    "centroid_y_mm": float(np.mean(pts[:, 1]) * 1000.),
                    "centroid_z_mm": float(np.mean(pts[:, 2]) * 1000.),
                    "vertex_ids": ids,
                    "_points": pts * unit_scale,
                })

    if element_count != expected:
        raise ValueError(f"Reference has {element_count} elements; header says {expected}")

    findings.sort(key=lambda x: x["ratio"], reverse=True)
    csv_path = project / "mesh_quality.csv"
    cols = ["element_id", "grade", "ratio", "max_edge_mm", "area_mm2",
            "centroid_x_mm", "centroid_y_mm", "centroid_z_mm", "vertex_ids"]
    with csv_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=cols)
        writer.writeheader()
        for hit in findings:
            writer.writerow({k: (json.dumps(hit[k]) if k == "vertex_ids" else hit[k])
                             for k in cols})

    # Material-colored OBJ: import this over the original STL in Blender.
    # Coordinates use the same units as the original STL, not the NumCalc metres.
    if findings:
        obj_path = project / "mesh_bad_triangles.obj"
        mtl_path = project / "mesh_bad_triangles.mtl"
        mtl_path.write_text(
            "newmtl fatal\nKd 1.0 0.1 0.1\nKa 0.2 0 0\n"
            "newmtl warning\nKd 1.0 0.6 0.05\nKa 0.2 0.1 0\n")
        with obj_path.open("w") as file:
            file.write("mtllib mesh_bad_triangles.mtl\n")
            idx = 1
            for hit in findings:
                file.write(f"o NumCalc_element_{hit['element_id']}\n")
                file.write("usemtl fatal\n" if hit["grade"] == "FATAL"
                           else "usemtl warning\n")
                for p in hit["_points"]:
                    # _points are Blender-native coordinates already in
                    # STL units. Convert axes for Blender's default OBJ
                    # importer without applying the metre conversion twice.
                    obj_point = (p[0], p[2], -p[1])
                    file.write("v " + " ".join(f"{coord:.12g}" for coord in obj_point) + "\n")
                file.write(f"f {idx} {idx+1} {idx+2}\n")
                idx += 3

    fatalities = [h for h in findings if h["grade"] == "FATAL"]
    lengths = np.asarray(longest_edges_mm, dtype=float)
    # A heuristic, not a convergence test. BEM surface meshes typically need
    # several elements per wavelength; local pinna curvature may need more.
    resolution = []
    for hz in (5000, 10000, 20000, 24000):
        target_mm = 343000.0 / (6.0 * hz)
        resolution.append({
            "frequency_hz": hz,
            "wavelength_mm": 343000.0 / hz,
            "lambda_over_6_edge_mm": target_mm,
            "count_over_lambda_over_6": int(np.count_nonzero(lengths > target_mm)),
            "fraction_over_lambda_over_6": float(np.mean(lengths > target_mm)) if len(lengths) else 0.0,
        })
    summary = {
        "triangle_count": expected,
        "longest_edge_mm_p50": float(np.median(lengths)) if len(lengths) else None,
        "longest_edge_mm_p95": float(np.percentile(lengths, 95)) if len(lengths) else None,
        "longest_edge_mm_max": float(np.max(lengths)) if len(lengths) else None,
        "frequency_resolution": resolution,
        "warning_count": len(findings) - len(fatalities),
        "fatal_count": len(fatalities),
        "fatal_ids": [h["element_id"] for h in fatalities],
        "worst": [{k: h[k] for k in cols if k != "vertex_ids"}
                  for h in findings[:20]],
        "report_csv": str(csv_path),
        "overlay_obj": str(project / "mesh_bad_triangles.obj") if findings else None,
        "explanation": (
            "Matches NumCalc NC_Input.cpp: for a triangle, "
            "ratio=max_edge/(1.51967*sqrt(area)); warning>10; fatal>100. "
            "Manifoldness alone does not imply numerically well-shaped elements. "
            "lambda/6 figures estimate discretization risk, not verified BEM convergence."
        )
    }
    (project / "mesh_quality.json").write_text(json.dumps(summary, indent=2))
    return summary
