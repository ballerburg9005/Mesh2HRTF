"""Conservative diagnostics for UNSOLVED acoustic validity questions.

The checks identify unverified results; they never silently delete, filter or
smooth any calculated NumCalc pressure, resonance, or phase.
"""
import re
from pathlib import Path

import numpy as np


def inspect_spectrum(frequencies_hz, gain_db, jump_threshold_db=15.,
                     extreme_gain_db=40.):
    """Flag implausible/amplified samples and large adjacent-frequency jumps.

    Marking a sample as suspect is NOT proof of solver failure. Conversely,
    smooth curves can still be wrong if the mesh or boundary model is wrong.
    """
    freq = np.asarray(frequencies_hz, float)
    gain = np.asarray(gain_db, float)
    if (freq.ndim != 1 or gain.shape != freq.shape or len(freq) < 2
            or not np.all(np.isfinite(freq)) or not np.all(np.isfinite(gain))
            or np.any(np.diff(freq) <= 0)):
        raise ValueError("Expected aligned, finite and increasing frequencies/gains")
    jumps = np.abs(np.diff(gain))
    jump_indices = np.flatnonzero(jumps > jump_threshold_db)
    suspect = gain > extreme_gain_db
    for j in jump_indices:
        suspect[j] = True
        suspect[j+1] = True
    indices = np.flatnonzero(suspect)
    bands = []
    width_hz = 500.
    start = np.floor(freq[0] / width_hz) * width_hz
    while start <= freq[-1]:
        stop = start + width_hz
        selected = np.flatnonzero((freq >= start) & (freq < stop if stop <= freq[-1] else freq <= stop))
        if len(selected):
            local_jump = jumps[selected[:-1]] if len(selected) > 1 else np.asarray([], float)
            bands.append({
                "from_hz": float(start), "to_hz": float(stop),
                "points": int(len(selected)),
                "gain_min_db": float(np.min(gain[selected])),
                "gain_max_db": float(np.max(gain[selected])),
                "largest_adjacent_jump_db": float(np.max(local_jump)) if len(local_jump) else 0.,
                "suspect_points": int(np.count_nonzero(suspect[selected])),
            })
        start = stop
    return {
        "first_suspected_frequency_hz": float(freq[indices[0]]) if len(indices) else None,
        "suspect_indices": indices.astype(int).tolist(),
        "suspect_point_count": int(len(indices)),
        "jump_threshold_db": float(jump_threshold_db),
        "extreme_gain_threshold_db": float(extreme_gain_db),
        "largest_adjacent_jump_db": float(np.max(jumps)),
        "large_jump_count": int(len(jump_indices)),
        "bands_500hz": bands,
        "interpretation": ("Suspect means investigate mesh, acoustic boundary conditions "
                           "and solver residuals; it does not prove an acoustic resonance "
                           "or prove a numerical failure. Coarse sweeps can miss narrow peaks."),
    }


_STEP_RE = re.compile(r"Step\s+(\d+)\s*,\s*Frequency\s*=\s*([+\d.eE-]+)\s*Hz", re.I)
_SOLVER_RE = re.compile(
    r"(?:CGS|GMRES|BiCGSTAB|BiCG|QMR)\s+solver\s*:"
    r"\s*number of iterations\s*=\s*(\d+)\s*,\s*relative error\s*=\s*([+\d.eE-]+)",
    re.I)


_GMRES_FALLBACK_RE = re.compile(
    r"GMRES fallback:\s*iterations\s*=\s*(\d+),\s*"
    r"true relative residual\s*=\s*([+\d.eE-]+)", re.I)
_CGS_TRUE_RE = re.compile(r"CGS TRUE relative residual\s*=\s*([+\d.eE-]+)", re.I)


def inspect_numcalc_logs(project, residual_warning=1e-5):
    """Read saved NumCalc iteration residuals without launching NumCalc again."""
    folder = Path(project) / "NumCalc" / "source_1"
    results = {}
    paths = sorted(folder.glob("NC*.out")) if folder.is_dir() else []
    for path in paths:
        data = path.read_text(errors="replace")
        matches = list(_STEP_RE.finditer(data))
        for n, match in enumerate(matches):
            end = matches[n+1].start() if n+1 < len(matches) else len(data)
            block = data[match.end():end]
            solver_match = _SOLVER_RE.search(block)
            gmres_match = _GMRES_FALLBACK_RE.search(block)
            true_match = _CGS_TRUE_RE.search(block)
            if solver_match is None and gmres_match is None:
                continue
            error = float(gmres_match.group(2) if gmres_match else
                          true_match.group(1) if true_match else
                          solver_match.group(2))
            if not np.isfinite(error):
                continue
            step = int(match.group(1))
            results[step] = {
                "step": step,
                "frequency_hz": float(match.group(2)),
                "iterations": int(gmres_match.group(1) if gmres_match else
                                  solver_match.group(1)),
                "method": "GMRES fallback" if gmres_match else "CGS",
                "relative_error": error,
                "log_file": path.name,
            }
    records = [results[k] for k in sorted(results)]
    failures = [r for r in records if r["relative_error"] > residual_warning]
    return {
        "logs_read": len(paths),
        "steps_with_reported_residual": len(records),
        "residual_warning_threshold": float(residual_warning),
        "max_reported_residual": max((r["relative_error"] for r in records), default=None),
        "steps_over_threshold": len(failures),
        "worst_steps": sorted(records, key=lambda r: -r["relative_error"])[:15],
        "steps_over_threshold_details": failures[:100],
        "note": ("Solver-reported iterative residuals are not a mesh convergence test. "
                 "Missing logs or residuals mean the solver could not be validated here."),
    }
