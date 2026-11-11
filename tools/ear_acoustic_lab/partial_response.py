"""Export only genuinely finished, convergence-verified NumCalc microphone data.

Unlike output2hrtf, this does not require every frequency to be present.
Missing/failed steps remain missing; the plot is points only, not a falsely
interpolated continuous frequency response.
"""
import csv
import json
import math
from pathlib import Path
import re

import numpy as np

from resume_audit import audit_project
from analysis_tools import reference_transfer


_GMRES = re.compile(
    r"GMRES fallback:\s*iterations\s*=\s*\d+,\s*"
    r"true relative residual\s*=\s*([+\d.eE-]+)", re.I)
_CGS_TRUE = re.compile(
    r"CGS TRUE relative residual\s*=\s*([+\d.eE-]+)", re.I)


def load_mic_probe(pressure_file):
    """Read exactly three complex MicProbe nodes from a NumCalc pEvalGrid."""
    lines = Path(pressure_file).read_text(errors="replace").splitlines()
    if len(lines) != 6:
        raise ValueError(f"Expected 3 MicProbe samples in {pressure_file}; "
                         f"found {max(0, len(lines) - 3)} data lines")
    try:
        counts = lines[2].split()
        if len(counts) != 2 or int(counts[1]) != 3:
            raise ValueError("Evaluation grid is not the expected 3-node mic")
        samples = {}
        for line in lines[3:]:
            fields = line.split()
            if len(fields) != 3:
                raise ValueError("Malformed complex pressure row")
            node = int(fields[0])
            value = complex(float(fields[1]), float(fields[2]))
            if not math.isfinite(value.real) or not math.isfinite(value.imag):
                raise ValueError("Nonfinite NumCalc pressure")
            if node in samples:
                raise ValueError("Repeated microphone node")
            samples[node] = value
        if set(samples) != {10000000, 10000001, 10000002}:
            raise ValueError("MicProbe node IDs are missing or incorrect")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Malformed NumCalc pEvalGrid: {pressure_file}: "
                         f"{exc}") from exc
    return sum(samples.values()) / 3


def _verified_residual(log_file):
    data = Path(log_file).read_text(errors="replace")
    # Prefer the successful fallback over a failed original CGS solve.
    match = _GMRES.search(data)
    method = "GMRES" if match else "CGS"
    if match is None:
        match = _CGS_TRUE.search(data)
    if match is None:
        return None, "unverified"
    residual = float(match.group(1))
    return residual, method


def export_partial_response(project, tolerance=1e-8):
    """Write a valid-only CSV and optional scatter plot; never write response.csv."""
    root = Path(project).expanduser().resolve()
    audit = audit_project(root)
    params = json.loads((root / "parameters.json").read_text())
    cfg = json.loads((root / "earlab_config.json").read_text())
    speed = float(params.get("speedOfSound", 343.))
    if speed <= 0:
        raise ValueError("Invalid speed of sound in parameters.json")

    output = root / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    points, excluded = [], []
    for row in audit["steps"]:
        if row["status"] != "completed":
            continue
        source = root / "NumCalc" / f"source_{row['source']}"
        log = source / f"NC{row['step']}-{row['step']}.out"
        residual, method = _verified_residual(log)
        if residual is None or not math.isfinite(residual) or residual > tolerance:
            excluded.append({"step": row["step"], "reason": "unverified solver residual"})
            continue
        pfile = source / "be.out" / f"be.{row['step']}" / "pEvalGrid"
        try:
            pressure = load_mic_probe(pfile)
        except ValueError as exc:
            excluded.append({"step": row["step"], "reason": str(exc)})
            continue
        frequency = row["frequency_hz"]
        if frequency is None:
            excluded.append({"step": row["step"], "reason": "frequency unavailable"})
            continue
        H = reference_transfer(
            np.asarray([frequency]), np.asarray([pressure]),
            cfg["mic"], cfg["source"], cfg["units"], speed=speed)[0]
        if not np.isfinite(H.real) or not np.isfinite(H.imag):
            excluded.append({"step": row["step"], "reason": "nonfinite normalized pressure"})
            continue
        points.append({
            "step": row["step"], "frequency_hz": frequency,
            "pressure_real": float(pressure.real),
            "pressure_imag": float(pressure.imag),
            "gain_db": float(20 * np.log10(max(abs(H), 1e-14))),
            "phase_deg": float(np.degrees(np.angle(H))),
            "solver_method": method, "true_relative_residual": residual,
        })
    points.sort(key=lambda r: (r["frequency_hz"], r["step"]))
    fields = ["step", "frequency_hz", "gain_db", "phase_deg",
              "pressure_real", "pressure_imag",
              "solver_method", "true_relative_residual"]
    path = output / "partial_response.csv"
    with path.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(points)
    summary = {
        "verified_points": len(points),
        "total_project_steps": audit["total"],
        "completed_steps": audit["completed"],
        "missing_or_failed_steps": audit["unfinished"],
        "completed_but_unverified": excluded,
        "frequency_range_hz": [points[0]["frequency_hz"],
                               points[-1]["frequency_hz"]] if points else None,
        "warning": ("ONLY completed frequencies with true relative residual "
                    f"<= {tolerance:g} are included. Missing frequencies are "
                    "NOT interpolated. Mesh/watertightness/acoustic accuracy "
                    "is not established. Do not use for full-band audition."),
    }
    (output / "partial_response.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    if points:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.scatter([p["frequency_hz"] for p in points],
                   [p["gain_db"] for p in points], s=25)
        ax.set(xlabel="Frequency (Hz)", ylabel="Gain relative to free field (dB)",
               title="PARTIAL: verified solved frequencies only (no interpolation)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output / "partial_response.png", dpi=140)
        plt.close(fig)
    return summary, path
