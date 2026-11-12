#!/usr/bin/env python3
"""Unattended 6.65–20 kHz continuation of the existing 80-tone ear model.

Use the already exported repaired mesh, microphone, and source. Run one
independent single-level FMM solve per high-band frequency. Continue past
failed solves, save every attempt, and render the visual overview afterward.
No changes to original NumCalc project, no re-export, no new mesh.
"""
import argparse
import json
from pathlib import Path
import sys

import overnight_bem_validation as validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", default="earlab_runs/ear_20261112_014827_323293",
        help="Original repaired 80-step NumCalc project")
    parser.add_argument("--start-step", type=int, default=27)
    parser.add_argument("--max-hours", type=float, default=8.)
    parser.add_argument("--timeout-per-step", type=int, default=480)
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    params = json.loads((project / "parameters.json").read_text())
    frequencies = params["frequencies"]
    if len(frequencies) != 80 or not 19990 <= frequencies[-1] <= 20010:
        parser.error("Expected the original 80-step project ending at 20 kHz; "
                     "refusing to run an unrelated frequency grid")
    if not 1 <= args.start_step <= len(frequencies):
        parser.error("--start-step must be between 1 and 80")

    # The earlier comparison already covers 1.86–6.4 kHz. Do not repeat
    # four variants/dense BEM at every new high-frequency point: this
    # sweep uses only SL-FMM and records nonconvergence rather than
    # pretending failed pressure data are acoustic measurements.
    validation.METHODS = tuple(
        method for method in validation.METHODS if method[0] == "sl8")
    steps = tuple(range(args.start_step, len(frequencies) + 1))
    print(f"[HIGH BAND] {len(steps)} frequencies, "
          f"{frequencies[steps[0]-1]:.1f}–{frequencies[-1]:.1f} Hz, "
          f"one method, max {args.max_hours:g} h.", flush=True)
    print("[HIGH BAND] Failed steps will not abort the remaining sweep.",
          flush=True)
    try:
        validation.run(
            project, steps=steps, per_case_timeout=args.timeout_per_step,
            max_hours=args.max_hours)
    finally:
        # Also render after an unexpected Python exception, when possible.
        try:
            from render_overnight_dashboard import render
            output = render(project)
            print(f"[HIGH BAND] HTML: {output / 'index.html'}", flush=True)
            print(f"[HIGH BAND] PNG: {output / 'frequency_response.png'}",
                  flush=True)
        except Exception as exc:
            print(f"[HIGH BAND] Visualization failed: {exc}", flush=True)
    print("[HIGH BAND COMPLETE] All saved results remain in the original "
          "project's analysis/ folders; original data were not modified.",
          flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[HIGH BAND FAILED] {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
