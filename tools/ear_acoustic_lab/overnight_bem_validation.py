#!/usr/bin/env python3
"""Unattended, bounded NumCalc validation on an existing clean mesh export.

A restartable diagnostic across only steps 13 and 14:
  - SL-FMM with standard minimum expansion (8)
  - ML-FMM with standard minimum expansion (8)
  - SL-FMM with higher minimum expansion (10)
  - ML-FMM with higher minimum expansion (10)
  - Traditional dense BEM, without FMM acceleration, iterative solver

Never runs Blender, the Mesh2HRTF manager, or an 80-tone sweep.
Never modifies the original project's solver inputs/outputs.
Each case uses a fresh independent project layout with a time limit and
physical-memory guard. A completed 3123 Hz SL/ML pair from the repaired mesh
may be reused after verifying input hashes, solver logs, and pressures.
A dense BEM convergence result is a numerical-method reference, NOT
proof the continuum acoustic boundary model or mesh is physically correct.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from fmm_one_frequency_check import (
    completed_true_residual, gain_db, load_three_node_pressure)
from fmm_clean_mesh_one_point import _mesh_check, _sha256
from topology_audit import _load_reference


METHODS = (
    ("sl8", 1, 8, "Single level fast multipole BEM"),
    ("ml8", 4, 8, "Direct multilevel fast multipole BEM"),
    ("sl10", 1, 10, "Single level fast multipole BEM"),
    ("ml10", 4, 10, "Direct multilevel fast multipole BEM"),
    ("dense", 0, 8, "Traditional BEM"),
)
SOURCE_FILES = {
    "NC.inp": "NumCalc/source_1/NC.inp",
    "Nodes.txt": "ObjectMeshes/Reference/Nodes.txt",
    "Elements.txt": "ObjectMeshes/Reference/Elements.txt",
    "parameters.json": "parameters.json",
    "earlab_config.json": "earlab_config.json",
}


def set_numcalc_method(nc_text, bem_method, expansion_terms=8):
    """Change ONLY METHODBEM and minimum expansion in Main Parameters I.

    METHODBEM: 0 traditional dense BEM, 1 single-level FMM,
    4 multilevel FMM. ISOLVER stays at the existing iterative setting.
    """
    if bem_method not in (0, 1, 4) or expansion_terms not in (8, 10):
        raise ValueError("Unsupported BEM method or expansion order")
    lines = nc_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == "## 1. Main Parameters I":
            for k in range(i + 1, min(i + 6, len(lines))):
                if lines[k].strip() and not lines[k].lstrip().startswith("#"):
                    a = lines[k].split()
                    if len(a) < 9 or a[7] not in ("0", "1", "4"):
                        raise ValueError("Invalid original METHODBEM row")
                    if a[8] != "0":
                        raise ValueError(
                            "Overnight reference expects NumCalc iterative "
                            "solver=0; refusing an unbounded direct solve.")
                    # Mesh2Input often writes 0, which NumCalc converts
                    # to the default minimum of 8. Keep it unchanged for
                    # order=8 so existing verified baseline is reusable.
                    if expansion_terms == 10:
                        a[3] = "10"
                    a[7] = str(bem_method)
                    lines[k] = " ".join(a) + "\n"
                    return "".join(lines)
    raise ValueError("Missing NumCalc Main Parameters I")


def _snapshot(project):
    return {
        name: _sha256(project / rel)
        for name, rel in SOURCE_FILES.items()
    }


def _read_result(source, step, expected_label, frequency, cfg, params):
    log_path = source / f"NC{step}-{step}.out"
    pressure_path = source / "be.out" / f"be.{step}" / "pEvalGrid"
    if not log_path.is_file() or not pressure_path.is_file():
        raise FileNotFoundError(
            f"Incomplete output for {expected_label}, step {step}")
    log = log_path.read_text(errors="replace")
    if expected_label not in log:
        raise ValueError(
            f"Expected NumCalc method {expected_label!r} not found in {log_path}")
    solver_method, residual = completed_true_residual(log)
    pressure = load_three_node_pressure(pressure_path)
    gain, phase = gain_db(
        pressure, frequency, cfg, params.get("speedOfSound", 343.))
    if not (math.isfinite(gain) and math.isfinite(phase)):
        raise ValueError("Non-finite gain or phase")
    return {
        "status": "converged", "gain_db": gain, "phase_deg": phase,
        "pressure_real": pressure.real, "pressure_imag": pressure.imag,
        "true_relative_residual": residual, "iterative_solver": solver_method,
        "log_path": str(log_path),
    }


def _read_dense_direct_fallback_unverified(source, step, frequency, cfg, params):
    """Recognize NumCalc's post-CGS Gaussian-elimination fallback.

    The CGS TRUE residual is NOT the Gaussian-elimination residual.
    Do not classify this as converged without an independently checked
    direct-solution residual. No solver runs or inputs are modified.
    """
    import re

    log_path = source / f"NC{step}-{step}.out"
    grid_path = source / "be.out" / f"be.{step}" / "pEvalGrid"
    if not log_path.is_file() or not grid_path.is_file():
        raise FileNotFoundError(
            "Traditional BEM direct-fallback outputs missing")
    log = log_path.read_text(errors="replace")
    if ("Traditional BEM" not in log or "End time:" not in log or
            "Iteration method CGS failed, direct method is used!" not in log):
        raise ValueError(
            "Not a completed NumCalc traditional BEM direct fallback")
    match = re.search(
        r"CGS TRUE relative residual\s*=\s*([\d.eE+-]+)", log)
    if not match:
        raise ValueError("Missing earlier, failed CGS residual")
    failed_cgs_residual = float(match.group(1))
    pressure = load_three_node_pressure(grid_path)
    gain, phase = gain_db(
        pressure, frequency, cfg, params.get("speedOfSound", 343.))
    return {
        "status": "direct_unverified",
        "candidate_gain_db": gain,
        "candidate_phase_deg": phase,
        "pressure_real": pressure.real,
        "pressure_imag": pressure.imag,
        "prior_failed_cgs_relative_residual": failed_cgs_residual,
        "direct_relative_residual": None,
        "reason": (
            "NumCalc switched from failed CGS to Gaussian elimination. "
            "Saved output exists, but no independently verified residual "
            "for the direct solution was produced."),
        "log_path": str(log_path),
        "source": "existing_dense_direct_fallback",
    }


def _recover_existing_dense_outputs(project, root, steps, results, cfg, params):
    """Reclassify *only* direct-fallback cases, without launching NumCalc."""
    refreshed = 0
    for step in steps:
        case_id = f"{step}:dense"
        rec = results.get(case_id, {})
        if rec.get("status") not in ("failed", "direct_unverified"):
            continue
        source = root / f"step{step:02d}_dense" / "project" / "NumCalc" / "source_1"
        try:
            classified = _read_dense_direct_fallback_unverified(
                source, step, float(params["frequencies"][step-1]),
                cfg, params)
        except (FileNotFoundError, ValueError):
            continue
        results[case_id] = classified
        refreshed += 1
    return refreshed


def _reuse_existing_step13(project, initial_hash, cfg, params, frequency):
    """Reuse prior clean-mesh step13 ONLY if inputs and verified outputs match."""
    analysis = project / "analysis"
    reports = sorted(
        analysis.glob("fmm_new_mesh_step13_*/comparison.json"),
        reverse=True)
    for report in reports:
        try:
            saved = json.loads(report.read_text())
            if (saved.get("step") != 13 or
                    abs(saved["frequency_hz"] - frequency) > 1e-6 or
                    saved.get("original_sha256", {}) != {
                        k: initial_hash[k] for k in
                        ("NC.inp", "Nodes.txt", "Elements.txt")
                    }):
                continue
            reused = {}
            for key, label in [
                ("sl8", "single_level"),
                ("ml8", "multilevel"),
            ]:
                method = ("Single level fast multipole BEM"
                          if key == "sl8" else
                          "Direct multilevel fast multipole BEM")
                working = report.parent / label / "NumCalc" / "source_1"
                saved_input = working / "NC.inp"
                expected_input = set_numcalc_method(
                    (project / SOURCE_FILES["NC.inp"]).read_text(),
                    1 if key == "sl8" else 4, 8)
                if saved_input.read_text() != expected_input:
                    raise ValueError("Saved baseline uses different NumCalc settings")
                result = _read_result(
                    working, 13, method, frequency, cfg, params)
                result["source"] = "verified_repaired_mesh_baseline"
                reused[key] = result
            return reused, str(report)
        except (OSError, KeyError, ValueError, TypeError):
            continue
    return {}, None


def _kill_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def _run_with_guards(command, cwd, log_path, timeout_s,
                     reserve_gib=2.5, min_available_gib=6.0):
    """One child process group, hard wall-time limit, physical-RAM guard."""
    import psutil

    def available():
        return psutil.virtual_memory().available / (1024 ** 3)

    free = available()
    if free < min_available_gib:
        raise RuntimeError(
            f"Insufficient available physical RAM: {free:.2f} GiB "
            f"(minimum {min_available_gib:.2f} GiB)")
    if any((p.info.get("name") or "").lower() in ("numcalc", "numcalc.exe")
           for p in psutil.process_iter(["name"])):
        raise RuntimeError("Another NumCalc process is active; refusing overlap")
    launch = time.monotonic()
    reason = None
    max_rss = 0
    with log_path.open("w") as output:
        proc = subprocess.Popen(
            command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
        child = psutil.Process(proc.pid)
        try:
            while proc.poll() is None:
                elapsed = time.monotonic() - launch
                if elapsed > timeout_s:
                    reason = f"Hard timeout exceeded ({timeout_s}s)"
                    break
                free = available()
                if free < reserve_gib:
                    reason = (
                        f"Physical RAM guard: only {free:.2f} GiB "
                        f"available (reserve {reserve_gib:.2f} GiB)")
                    break
                try:
                    rss = child.memory_info().rss
                    for descendant in child.children(recursive=True):
                        try:
                            rss += descendant.memory_info().rss
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    max_rss = max(max_rss, rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                try:
                    proc.wait(timeout=min(2.0, timeout_s - elapsed))
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if proc.poll() is None:
                _kill_group(proc)
    return {
        "return_code": proc.returncode,
        "stop_reason": reason,
        "seconds_elapsed": round(time.monotonic() - launch, 2),
        "max_observed_process_rss_gib": round(max_rss / 1024**3, 3),
    }


def _method_index():
    return {item[0]: item for item in METHODS}


def _reports_to_text(project, steps, results, params, budget_h, start_time):
    lines = [
        "Mesh2HRTF OVERNIGHT BEM NUMERICAL REFERENCE",
        "=========================================",
        f"Project: {project}",
        f"Started: {start_time}",
        f"Wall-time budget: {budget_h:g} hours",
        "Original project outputs are untouched.",
        "Each case used one selected frequency step, not a broadband sweep.",
        "",
    ]
    for step in steps:
        freq = float(params["frequencies"][step-1])
        lines.append(f"Frequency {freq:.2f} Hz / step {step}")
        done = {}
        for key, _, order, _ in METHODS:
            rec = results.get(f"{step}:{key}", {})
            status = rec.get("status", "not_run")
            if status == "converged":
                done[key] = rec
                lines.append(
                    f"  {key:6s} {rec['gain_db']:+9.3f} dB"
                    f"  true residual {rec['true_relative_residual']:.3e}"
                    f"  ({rec.get('source','new_solve')})")
            elif status == "direct_unverified":
                lines.append(
                    f"  {key:6s} {rec['candidate_gain_db']:+9.3f} dB"
                    "  DIRECT FALLBACK, RESIDUAL NOT VERIFIED"
                    f"  (earlier CGS residual "
                    f"{rec['prior_failed_cgs_relative_residual']:.3e})")
            else:
                lines.append(f"  {key:6s} {status:15s}  "
                             f"{rec.get('reason','')}")
        if "dense" in done:
            for other in ("sl8", "ml8", "sl10", "ml10"):
                if other in done:
                    lines.append(
                        f"  {other} minus dense = "
                        f"{done[other]['gain_db']-done['dense']['gain_db']:+.3f} dB")
            lines.append(
                "  Traditional BEM provides a non-FMM comparison on the "
                "SAME mesh, NOT a continuum/physical validation.")
        else:
            lines.append(
                "  No converged non-FMM reference here. "
                "Do NOT declare either FMM result correct.")
        if "sl8" in done and "ml8" in done:
            lines.append(
                f"  ML8 minus SL8 = "
                f"{done['ml8']['gain_db']-done['sl8']['gain_db']:+.3f} dB")
        if "sl10" in done and "ml10" in done:
            lines.append(
                f"  ML10 minus SL10 = "
                f"{done['ml10']['gain_db']-done['sl10']['gain_db']:+.3f} dB")
        lines.append("")
    lines += [
        "CAVEAT: True residual verifies the chosen discrete linear system,",
        "not the accuracy of the approximate FMM operator or geometry.",
        "The mesh has NOT been shown converged under surface refinement.",
        "Do not use these results as validated acoustic measurements.",
    ]
    return "\n".join(lines) + "\n"


def run(project, steps=(13,14), per_case_timeout=900,
        max_hours=6.0, reserve_gib=2.5,
        min_available_gib=6.0, reuse=True, dry_run=False):
    project = Path(project).expanduser().resolve()
    solver = (Path(__file__).resolve().parents[2] /
              "mesh2hrtf/NumCalc/bin/NumCalc")
    params = json.loads((project / "parameters.json").read_text())
    cfg = json.loads((project / "earlab_config.json").read_text())
    source = project / "NumCalc" / "source_1" / "NC.inp"
    nc_original = source.read_text()
    if not solver.is_file() or not os.access(solver, os.X_OK):
        raise FileNotFoundError("Compiled NumCalc executable missing: " + str(solver))
    snapshot = _snapshot(project)
    # Confirm this is an unchanged Mesh2Input multilevel export, not
    # an already modified single-level or dense BEM project.
    original_row = None
    lines = nc_original.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## 1. Main Parameters I":
            original_row = next(
                (v.split() for v in lines[i+1:i+6]
                 if v.strip() and not v.lstrip().startswith("#")), None)
            break
    if (original_row is None or len(original_row) < 9 or
            original_row[7] != "4" or original_row[8] != "0"):
        raise ValueError(
            "Expected fresh Mesh2Input export (METHODBEM=4, ISOLVER=0). "
            "Use your clean 8994-triangle export project.")

    if any(step < 1 or step > len(params["frequencies"]) for step in steps):
        raise ValueError("Requested frequency step outside project")
    for step in steps:
        freq = float(params["frequencies"][step - 1])
        mesh = _mesh_check(project, freq)
        print(f"[OVERNIGHT PREFLIGHT] step {step}, {freq:.2f} Hz, "
              f"{mesh['triangle_count']} triangles, "
              f"{100*mesh['fraction_over_wavelength_six']:.2f}% "
              "of faces longer than wavelength/6", flush=True)
    if not (0 < per_case_timeout <= 3600 and 0 < max_hours <= 24
            and 0 < reserve_gib < min_available_gib):
        raise ValueError("Invalid time or RAM protection settings")

    analysis = project / "analysis"
    root = analysis / ("overnight_bem_validation_steps_" +
                       "_".join(map(str, steps)))
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.json"
    binary_sha = _sha256(solver)
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        if (saved.get("original_source_sha256") != snapshot or
                saved.get("solver_binary_sha256") != binary_sha):
            raise RuntimeError(
                "Input project or NumCalc binary changed since prior "
                "overnight run. Refusing to mix incomparable results: "
                + str(root))
    else:
        manifest.write_text(json.dumps({
            "project": str(project), "original_source_sha256": snapshot,
            "requested_steps": list(steps), "created": dt.datetime.now().isoformat(),
            "solver_binary_sha256": binary_sha}, indent=2) + "\n")
    results_path = root / "results.json"
    results = (json.loads(results_path.read_text())
               if results_path.is_file() else {})
    recovered = _recover_existing_dense_outputs(
        project, root, steps, results, cfg, params)
    if recovered:
        print(f"[OVERNIGHT] Reclassified {recovered} saved dense cases "
              "as direct_unverified. No solver was rerun.", flush=True)
    begun = dt.datetime.now().isoformat()
    deadline = time.monotonic() + max_hours * 3600
    cases = []
    mapping = _method_index()
    for step in steps:
        for key, method, order, expected in METHODS:
            cases.append((step, key, method, order, expected))

    if reuse and 13 in steps:
        baseline, location = _reuse_existing_step13(
            project, snapshot, cfg, params, float(params["frequencies"][12]))
        for key, rec in baseline.items():
            case_id = f"13:{key}"
            if case_id not in results:
                results[case_id] = rec
        if location:
            print("[OVERNIGHT] Reusing two already-verified 3123 Hz "
                  "repaired-mesh results from " + location, flush=True)

    def save():
        # Atomic write helps resumption after a crash/power interruption.
        tmp = root / "results.json.tmp"
        tmp.write_text(json.dumps(results, indent=2) + "\n")
        tmp.replace(results_path)
        report = _reports_to_text(
            project, steps, results, params, max_hours, begun)
        tmp = root / "overnight_report.txt.tmp"
        tmp.write_text(report)
        tmp.replace(root / "overnight_report.txt")

    save()
    print(f"[OVERNIGHT] {len(cases)} possible cases at "
          f"{len(steps)} selected frequencies, "
          f"maximum {per_case_timeout}s per case and "
          f"{max_hours:g}h global deadline.", flush=True)
    print("[OVERNIGHT] Overnight report: " +
          str(root / "overnight_report.txt"), flush=True)
    if dry_run:
        print("[OVERNIGHT DRY RUN] No subprocesses started.", flush=True)
        for step, key, _, _, _ in cases:
            status = results.get(f"{step}:{key}", {}).get("status", "planned")
            print(f"  step={step} method={key} status={status}", flush=True)
        return results

    for step, key, method, order, expected in cases:
        case_id = f"{step}:{key}"
        if case_id in results and results[case_id].get("status") == "converged":
            print(f"[OVERNIGHT] SKIP verified/reused {case_id}", flush=True)
            continue
        if case_id in results:
            # Report failure, do not silently rerun a timed-out job on
            # accidental restart. Delete the specific failed JSON entry to
            # intentionally retry it, if desired.
            print(f"[OVERNIGHT] SKIP previously attempted {case_id}: "
                  f"{results[case_id].get('status')}", flush=True)
            continue
        if time.monotonic() >= deadline:
            print("[OVERNIGHT] Reached global time budget; "
                  "remaining cases preserved as not_run", flush=True)
            break
        current_input = _snapshot(project)
        if current_input != snapshot:
            print("[OVERNIGHT] SOURCE CHANGED. Stopping to avoid mixing "
                  "results from different geometries/settings.", flush=True)
            break
        name = f"step{step:02d}_{key}"
        box = root / name
        subproject = box / "project"
        workdir = subproject / "NumCalc" / "source_1"
        workdir.mkdir(parents=True, exist_ok=True)
        for folder in ("ObjectMeshes", "EvaluationGrids"):
            target = project / folder
            if not target.is_dir():
                raise FileNotFoundError(str(target))
            symlink = subproject / folder
            if not symlink.exists():
                symlink.symlink_to(target, target_is_directory=True)
        (subproject / "parameters.json").write_text(json.dumps(params))
        (subproject / "earlab_config.json").write_text(json.dumps(cfg))
        edited = set_numcalc_method(nc_original, method, order)
        (workdir / "NC.inp").write_text(edited)
        freq = float(params["frequencies"][step - 1])
        limit = min(per_case_timeout, max(1, int(deadline - time.monotonic())))
        print(f"[OVERNIGHT] RUN {case_id}: {freq:.2f} Hz; "
              f"method={method}, expansion={order}, "
              f"timeout={limit}s", flush=True)
        started = time.monotonic()
        try:
            subprocess_meta = _run_with_guards(
                [str(solver), "-istart", str(step), "-iend", str(step)],
                workdir, box / "stdout.txt", limit,
                reserve_gib=reserve_gib,
                min_available_gib=min_available_gib)
            if subprocess_meta["stop_reason"]:
                raise RuntimeError(subprocess_meta["stop_reason"])
            if subprocess_meta["return_code"] != 0:
                raise RuntimeError(
                    f"NumCalc exit code {subprocess_meta['return_code']}")
            try:
                verified = _read_result(
                    workdir, step, expected, freq, cfg, params)
            except ValueError:
                if key != "dense":
                    raise
                verified = _read_dense_direct_fallback_unverified(
                    workdir, step, freq, cfg, params)
            verified["source"] = "new_solve"
            verified["elapsed_s"] = subprocess_meta["seconds_elapsed"]
            verified["max_observed_process_rss_gib"] = (
                subprocess_meta["max_observed_process_rss_gib"])
            verified["input_sha256"] = _sha256(workdir / "NC.inp")
            results[case_id] = verified
            if verified["status"] == "converged":
                print(f"[OVERNIGHT] PASS {case_id}: "
                      f"{verified['gain_db']:+.3f} dB, "
                      f"residual {verified['true_relative_residual']:.2e}",
                      flush=True)
            else:
                print(f"[OVERNIGHT] UNVERIFIED {case_id}: "
                      f"{verified['candidate_gain_db']:+.3f} dB direct "
                      "fallback with NO checked final residual.",
                      flush=True)
        except Exception as exc:
            results[case_id] = {
                "status": "failed", "reason": str(exc),
                "elapsed_s": round(time.monotonic() - started, 2),
                "log": str(box / "stdout.txt"),
            }
            print(f"[OVERNIGHT] FAILED {case_id}: {exc}; continuing.",
                  flush=True)
        finally:
            save()

    if _snapshot(project) != snapshot:
        raise RuntimeError(
            "Original project changed while overnight validation ran; "
            "individual results must not be combined.")
    save()
    print("[OVERNIGHT COMPLETE] Converged cases: "
          + str(sum(r.get("status") == "converged" for r in results.values()))
          + " / " + str(len(cases)), flush=True)
    print("[OVERNIGHT COMPLETE] Read: " +
          str(root / "overnight_report.txt"), flush=True)
    print("[OVERNIGHT COMPLETE] JSON: " + str(results_path), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Unattended bounded dense vs FMM numerical crosscheck")
    parser.add_argument("--project", required=True)
    parser.add_argument("--steps", nargs="+", type=int, default=[13, 14])
    parser.add_argument("--timeout-per-case", type=int, default=900)
    parser.add_argument("--max-hours", type=float, default=6.0)
    parser.add_argument("--reserve-gib", type=float, default=2.5)
    parser.add_argument("--min-available-gib", type=float, default=6.)
    parser.add_argument("--no-reuse", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        run(
            args.project, tuple(args.steps), args.timeout_per_case,
            args.max_hours, args.reserve_gib, args.min_available_gib,
            reuse=not args.no_reuse, dry_run=args.dry_run)
    except Exception as exc:
        print("[OVERNIGHT FAILED] " + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
