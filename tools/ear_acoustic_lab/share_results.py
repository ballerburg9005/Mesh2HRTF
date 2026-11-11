#!/usr/bin/env python3
"""Prepare reviewable acoustic snapshots for a public GitHub repository.

NEVER commits or pushes. Mesh and mic/source positions are included in
snapshots; full NumCalc solver workspaces are not.
"""
import argparse
import json
from pathlib import Path
import shutil

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARE_ROOT = REPO_ROOT / "tools" / "ear_acoustic_lab" / "shared_runs"

FILES = [
    "earlab_config.json", "parameters.json", "mesh_preflight.json",
    "geometry_check.json", "mesh_quality.json", "mesh_quality.csv",
    "analysis/report.html", "analysis/response.png",
    "analysis/surface_pressure.png", "analysis/surface_cutaway.png",
    "analysis/surface_3d.html", "analysis/response.csv",
    "analysis/diagnostic_samples.csv", "analysis/diagnostics.json",
    "analysis/peaks.json", "analysis/solver_checks.json",
    "analysis/probe_spread.csv", "analysis/pressure.npz",
    "analysis/surface_samples.npz", "analysis/surface_hotspots.csv",
    "Output2HRTF/HRTF_MicProbe.sofa"
]


def prepare_snapshot(project, include_stl=False, share_root=SHARE_ROOT):
    project = Path(project).expanduser().resolve()
    if project.name == "analysis":
        project = project.parent
    if not (project/"earlab_config.json").is_file():
        raise FileNotFoundError("Not an Ear Acoustic Lab project: " + str(project))
    if not (project/"analysis"/"response.csv").is_file():
        raise FileNotFoundError("The simulation has no completed response.csv: " + str(project))
    share_root = Path(share_root).expanduser().resolve()
    target = share_root/project.name
    target.mkdir(parents=True, exist_ok=True)
    included, omitted = [], []
    max_bytes = 12 * 1024 * 1024
    for rel in FILES:
        source = project/rel
        if not source.is_file():
            continue
        size = source.stat().st_size
        if size > max_bytes:
            omitted.append({"file": rel, "bytes": size, "why": "exceeds 12 MiB"})
            continue
        dest = target/rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        included.append(rel)
    # Source STL is sensitive: only include if user opted in.
    if include_stl:
        cfg = json.loads((project/"earlab_config.json").read_text())
        original = Path(cfg["stl"]).expanduser()
        if original.is_file() and original.stat().st_size <= max_bytes:
            shutil.copy2(original, target/"model.stl")
            included.append("model.stl")
        else:
            omitted.append({"file": str(original), "why": "missing or >12 MiB"})
    manifest = {
        "source_run": project.name,
        "snapshot_version": 1,
        "included": included,
        "omitted": omitted,
        "solver_results_reused": True,
        "github_note": "A compact preview; contains source/mic coordinates, mesh and pressure data. No files are committed or pushed automatically."
    }
    (target/"snapshot_manifest.json").write_text(json.dumps(manifest, indent=2))
    readme = (
        "# Ear Acoustic Lab calculation snapshot\n\n"
        "Run: " + project.name + "\n\n"
        "The plotted and tabulated numbers came from the completed NumCalc run. "
        "The surface_samples.npz stores boundary geometry and complex boundary "
        "pressures at selected solved frequencies, NOT a 3D air volume field. "
        "surface_hotspots.csv is human-readable and locates pressure extrema. "
        "The full NumCalc workspace is intentionally omitted. The original "
        "model STL is optional and not included by default.\n\n"
        "This snapshot will become PUBLIC if you push it into a public GitHub fork. "
        "To inspect 3D locally, open analysis/surface_3d.html.\n"
    )
    (target/"README.md").write_text(readme)
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("project", help="Completed run folder or analysis folder")
    p.add_argument("--include-stl", action="store_true")
    args = p.parse_args()
    snapshot = prepare_snapshot(args.project, include_stl=args.include_stl)
    print("[SHARE] Reviewable output prepared:", snapshot)
    print("[SHARE] Git changes are local; nothing committed or pushed.")
    print("[SHARE] Publish with:")
    print("  git add tools/ear_acoustic_lab/shared_runs")
    print("  git commit -m 'Add ear acoustic calculation'")
    print("  git push")


if __name__ == "__main__":
    main()
