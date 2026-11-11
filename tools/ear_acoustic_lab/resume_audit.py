"""Read-only NumCalc resume audit. A solver log alone is not a result."""
from collections import Counter
import json
from pathlib import Path
import re

_REJECTED = re.compile(
    r"CGS and GMRES failed convergence|GMRES fallback failed after",
    re.IGNORECASE)


def audit_project(project):
    """Summarize completed/unfinished steps without changing pressure outputs."""
    project = Path(project).expanduser().resolve()
    params = project / "parameters.json"
    if not params.is_file():
        raise FileNotFoundError("Missing parameters.json in " + str(project))
    metadata = json.loads(params.read_text())
    frequencies = metadata.get("frequencies") or []
    n = int(metadata.get("numFrequencies") or len(frequencies))
    if n < 1:
        raise ValueError("Cannot determine number of project frequencies")
    sources = sorted((project / "NumCalc").glob("source_*"))
    if not sources:
        raise FileNotFoundError("No NumCalc source folders in " + str(project))
    rows = []
    for source in sources:
        source_id = int(source.name.split("_")[-1])
        for step in range(1, n + 1):
            pressure = source / "be.out" / f"be.{step}" / "pEvalGrid"
            log = source / f"NC{step}-{step}.out"
            pressure_ok = pressure.is_file() and pressure.stat().st_size > 0
            log_text = log.read_text(errors="replace") if log.is_file() else ""
            ended = "End time:" in log_text
            if _REJECTED.search(log_text):
                state = "solver_rejected"
            elif pressure_ok and ended:
                state = "completed"
            elif pressure_ok:
                state = "partial_pressure"
            elif ended:
                state = "postprocessing_missing"
            elif log_text:
                state = "interrupted_or_failed"
            else:
                state = "not_started"
            rows.append({
                "source": source_id, "step": step,
                "frequency_hz": float(frequencies[step-1])
                                if len(frequencies) >= step else None,
                "status": state, "has_pressure": bool(pressure_ok),
                "has_log": log.is_file(), "end_marker": ended,
            })
    counts = Counter(row["status"] for row in rows)
    return {
        "project": str(project), "total": len(rows),
        "completed": counts["completed"],
        "unfinished": len(rows) - counts["completed"],
        "counts": dict(sorted(counts.items())), "steps": rows,
    }


def save_audit(project):
    audit = audit_project(project)
    path = Path(project) / "analysis" / "resume_audit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2) + "\n")
    return audit, path


def print_audit(audit, path):
    print(f"[RESUME AUDIT] {audit['completed']}/{audit['total']} complete "
          "(nonempty pEvalGrid AND NumCalc End time).", flush=True)
    for state, count in sorted(audit["counts"].items()):
        print(f"[RESUME AUDIT] {state}: {count}", flush=True)
    examples = [row for row in audit["steps"] if row["status"] != "completed"][:8]
    if examples:
        print("[RESUME AUDIT] Examples: " + ", ".join(
            f"step {r['step']} ({r['frequency_hz']} Hz): {r['status']}"
            for r in examples), flush=True)
    print("[RESUME AUDIT] Report: " + str(path), flush=True)
