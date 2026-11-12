"""Fast topology and wavelength-resolution audit of exported Mesh2Input geometry.

Never changes Nodes.txt, Elements.txt, NumCalc inputs or existing results.
This is a diagnostic gate, NOT proof that the microphone is in connected air
or that a manifold mesh is physically accurate.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from blender_obj_axes import mesh2input_to_obj


def _load_reference(project):
    folder = Path(project) / "ObjectMeshes" / "Reference"
    nodes_file, elements_file = folder / "Nodes.txt", folder / "Elements.txt"
    if not nodes_file.is_file() or not elements_file.is_file():
        raise FileNotFoundError("Missing exported Reference geometry in " + str(folder))
    nodes = {}
    with nodes_file.open() as file:
        expected = int(file.readline().strip())
        for line in file:
            cols = line.split()
            if not cols:
                continue
            if len(cols) != 4:
                raise ValueError("Malformed Reference node: " + line.strip())
            index = int(cols[0])
            point = tuple(map(float, cols[1:]))
            if index in nodes or not all(map(math.isfinite, point)):
                raise ValueError("Duplicate or nonfinite Reference node " + str(index))
            nodes[index] = point
    if len(nodes) != expected:
        raise ValueError(f"Nodes.txt expected {expected} nodes, found {len(nodes)}")
    triangles = {}
    with elements_file.open() as file:
        expected = int(file.readline().strip())
        for line in file:
            cols = line.split()
            if not cols:
                continue
            if len(cols) != 7:
                raise ValueError("Reference topology audit requires triangles "
                                 "with three metadata fields; row: " + line.strip())
            tid = int(cols[0])
            vertex_ids = tuple(map(int, cols[1:4]))
            if tid in triangles:
                raise ValueError("Repeated element ID " + str(tid))
            if any(index not in nodes for index in vertex_ids):
                raise ValueError("Element refers to a missing vertex: " + str(tid))
            triangles[tid] = vertex_ids
    if len(triangles) != expected:
        raise ValueError(f"Elements.txt expected {expected} faces, "
                         f"found {len(triangles)}")
    return nodes, triangles


def _length(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _area_double(a, b, c):
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    return math.sqrt(
        (u[1]*v[2] - u[2]*v[1])**2 +
        (u[2]*v[0] - u[0]*v[2])**2 +
        (u[0]*v[1] - u[1]*v[0])**2)


def _max_frequency(project, explicit):
    if explicit is not None:
        return float(explicit)
    params = Path(project) / "parameters.json"
    if params.is_file():
        data = json.loads(params.read_text())
        if data.get("frequencies"):
            return float(max(data["frequencies"]))
        if data.get("maxFrequency"):
            return float(data["maxFrequency"])
    return None


def audit_exported_topology(project, max_frequency_hz=None, write_overlay=True):
    """Return mesh defects and save geometry_topology.json/OBJ without solving."""
    project = Path(project).expanduser().resolve()
    nodes, triangles = _load_reference(project)
    edge_owners = defaultdict(list)
    longest_edges = []
    bad_face_ids = []
    parent = {tid: tid for tid in triangles}
    component_size = {tid: 1 for tid in triangles}

    def find(tid):
        while parent[tid] != tid:
            parent[tid] = parent[parent[tid]]
            tid = parent[tid]
        return tid

    def union(a, b):
        x, y = find(a), find(b)
        if x != y:
            if component_size[x] < component_size[y]:
                x, y = y, x
            parent[y] = x
            component_size[x] += component_size[y]

    for tid, tri in triangles.items():
        if len(set(tri)) != 3:
            bad_face_ids.append(tid)
        a, b, c = (nodes[index] for index in tri)
        if _area_double(a, b, c) <= 1e-22:
            bad_face_ids.append(tid)
        edge_lengths = [_length(nodes[tri[i]], nodes[tri[(i+1)%3]])
                        for i in range(3)]
        longest_edges.append(max(edge_lengths))
        for i in range(3):
            u, v = tri[i], tri[(i+1)%3]
            key = min(u, v), max(u, v)
            edge_owners[key].append((tid, 1 if u < v else -1))

    issues = []
    affected = {}
    for (u, v), faces in edge_owners.items():
        count = len(faces)
        kind = ("open_boundary" if count == 1 else
                "nonmanifold" if count > 2 else
                "winding_conflict" if faces[0][1] == faces[1][1] else None)
        for owner, _ in faces[1:]:
            union(faces[0][0], owner)
        if kind is None:
            continue
        a, b = nodes[u], nodes[v]
        record = {
            "kind": kind, "vertex_ids": [u, v],
            "triangle_ids": [face for face, _ in faces],
            "midpoint_mm": [round((a[i] + b[i]) * 500., 4)
                            for i in range(3)],
            "length_mm": round(_length(a, b) * 1000., 5),
        }
        issues.append(record)
        priority = {"open_boundary": 1, "winding_conflict": 2,
                    "nonmanifold": 3}
        for face, _ in faces:
            if priority[kind] > priority.get(affected.get(face, ""), 0):
                affected[face] = kind

    components = Counter(find(tid) for tid in triangles)
    categories = Counter(issue["kind"] for issue in issues)
    max_hz = _max_frequency(project, max_frequency_hz)
    if max_hz is not None and (not math.isfinite(max_hz) or max_hz <= 0):
        raise ValueError("Maximum frequency must be positive and finite")
    resolution = None
    if max_hz:
        threshold = 343. / (6. * max_hz)
        over = sum(edge > threshold for edge in longest_edges)
        resolution = {
            "max_frequency_hz": max_hz,
            "wavelength_mm": 343000. / max_hz,
            "lambda_over_six_max_edge_mm": threshold * 1000.,
            "edges_of_triangles_over_lambda_over_six": over,
            "fraction_of_triangles_over": over / max(1, len(triangles)),
            "worst_triangle_edge_mm": max(longest_edges) * 1000.,
        }
    blockers = []
    for name in ("open_boundary", "nonmanifold", "winding_conflict"):
        if categories[name]:
            blockers.append(f"{categories[name]} {name} edges")
    if bad_face_ids:
        blockers.append(f"{len(set(bad_face_ids))} degenerate faces")
    if len(components) != 1:
        blockers.append(f"{len(components)} edge-disconnected surfaces")
    if resolution and resolution["fraction_of_triangles_over"] > .05:
        blockers.append(
            f"{100*resolution['fraction_of_triangles_over']:.1f}% of "
            f"triangles exceed wavelength/6 at {max_hz:g} Hz")

    issues.sort(key=lambda issue: (
        {"nonmanifold": 0, "winding_conflict": 1, "open_boundary": 2}
        [issue["kind"]], issue["triangle_ids"][0]))
    output = project / "geometry_topology.json"
    obj = project / "topology_defects.obj"
    mtl = project / "topology_defects.mtl"
    units = "mm"
    config = project / "earlab_config.json"
    if config.is_file():
        units = json.loads(config.read_text()).get("units", "mm")
    if units not in ("mm", "m"):
        raise ValueError("Unsupported STL units in earlab_config: " + str(units))
    if write_overlay and affected:
        mtl.write_text(
            "newmtl open_boundary\nKd 1.0 0.6 0.0\n"
            "newmtl nonmanifold\nKd 1.0 0.05 0.05\n"
            "newmtl winding_conflict\nKd 0.1 0.5 1.0\n")
        # Preserve native mesh XYZ for import with Forward Y / Up Z,
        # Scale 1.0, as used for the source STL. Never pre-rotate vertices.
        with obj.open("w") as file:
            file.write("mtllib topology_defects.mtl\n")
            idx = 1
            for tid, kind in sorted(affected.items()):
                file.write(f"o affected_NumCalc_triangle_{tid}\n"
                           f"usemtl {kind}\n")
                for node in triangles[tid]:
                    point = nodes[node]
                    file.write("v " + " ".join(
                        f"{coordinate:.12g}" for coordinate in
                        mesh2input_to_obj(point, units)) + "\n")
                file.write(f"f {idx} {idx+1} {idx+2}\n")
                idx += 3

    report = {
        "project": str(project), "node_count": len(nodes),
        "triangle_count": len(triangles),
        "open_boundary_edges": categories["open_boundary"],
        "nonmanifold_edges": categories["nonmanifold"],
        "winding_conflicts": categories["winding_conflict"],
        "degenerate_triangle_ids": sorted(set(bad_face_ids)),
        "edge_connected_components": len(components),
        "component_triangle_counts": sorted(components.values(), reverse=True),
        "issue_edges": issues,
        "resolution": resolution, "blockers": blockers,
        "ready_for_unattended_solver": not bool(blockers),
        "overlay_obj": str(obj) if affected and write_overlay else None,
        "note": ("This precheck detects mesh topological and resolution risks. "
                 "Even a passing mesh is not proof of a correct acoustic boundary "
                 "model, a source-to-microphone air path, or BEM convergence."),
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    return report


def print_topology(report):
    print("[TOPOLOGY] " + str(report["triangle_count"]) +
          " triangles, " + str(report["node_count"]) + " nodes", flush=True)
    print("[TOPOLOGY] Open edges: " + str(report["open_boundary_edges"]) +
          "; nonmanifold edges: " + str(report["nonmanifold_edges"]) +
          "; winding conflicts: " + str(report["winding_conflicts"]) +
          "; components: " + str(report["edge_connected_components"]),
          flush=True)
    for edge in report["issue_edges"][:12]:
        print(f"[TOPOLOGY] {edge['kind']}: "
              f"vertices={edge['vertex_ids']} "
              f"triangles={edge['triangle_ids']} "
              f"at {edge['midpoint_mm']} mm", flush=True)
    r = report["resolution"]
    if r:
        print(f"[RESOLUTION] At {r['max_frequency_hz']:g} Hz: "
              f"{100*r['fraction_of_triangles_over']:.1f}% of triangles "
              "have an edge longer than wavelength/6", flush=True)
    if report["blockers"]:
        print("[TOPOLOGY BLOCKED] " + "; ".join(report["blockers"]), flush=True)
        print("[TOPOLOGY] Repair defective faces; inspect "
              + str(report["overlay_obj"]), flush=True)
    else:
        print("[TOPOLOGY] No edge-connectivity or coarse-resolution blockers. "
              "Physical validity still unverified.", flush=True)
    print("[TOPOLOGY] Saved: " +
          str(Path(report["project"]) / "geometry_topology.json"), flush=True)


def enforce_topology_gate(project, max_frequency_hz=None, allow=False):
    report = audit_exported_topology(project, max_frequency_hz)
    print_topology(report)
    if report["blockers"] and not allow:
        raise RuntimeError(
            "Refusing to launch another expensive NumCalc sweep on an "
            "unvalidated mesh: " + "; ".join(report["blockers"]) + ". "
            "Import topology_defects.obj into Blender to locate the affected "
            "triangles, repair the original STL, and re-export. "
            "An expert can override with --allow-unvalidated-geometry, "
            "but the output must not be treated as physically validated.")
    if report["blockers"]:
        print("[TOPOLOGY OVERRIDE] User explicitly allowed unvalidated geometry; "
              "solver results require independent verification.", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--max-frequency", type=float, default=None)
    args = parser.parse_args()
    print_topology(audit_exported_topology(args.project, args.max_frequency))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
