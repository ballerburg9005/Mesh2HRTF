#!/usr/bin/env python3
"""Bounded SL-FMM vs ML-FMM single-frequency check on a NEW exported project.

Runs two isolated NumCalc subprocesses, not the manager/80-tone sweep.
Checks the actual FMM method used, NumCalc end marker, GMRES/CGS true
residual, microphone pressure, and mesh resolution at this one frequency.

Not proof that either solution represents the real acoustic field.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from fmm_one_frequency_check import (
    completed_true_residual, force_single_level,
    gain_db, load_three_node_pressure)
from topology_audit import _load_reference


def _mesh_check(project, freq_hz, max_fraction=.05):
    """Read-only edge incidence and wavelength screen for selected frequency."""
    from collections import Counter
    nodes, faces = _load_reference(project)
    owners = Counter()
    edge_maximum = []
    for tri in faces.values():
        lengths = []
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            owners[(min(a, b), max(a, b))] += 1
            lengths.append(math.dist(nodes[a], nodes[b]))
        edge_maximum.append(max(lengths))
    open_edges = sum(n == 1 for n in owners.values())
    overfull_edges = sum(n > 2 for n in owners.values())
    threshold = 343. / (6 * freq_hz)
    over = sum(length > threshold for length in edge_maximum)
    fraction = over / len(edge_maximum)
    if open_edges or overfull_edges:
        raise RuntimeError(
            f"Exported mesh still has {open_edges} open and "
            f"{overfull_edges} nonmanifold edges")
    if fraction > max_fraction:
        raise RuntimeError(
            f"Even at {freq_hz:g} Hz, {fraction*100:.2f}% of triangles "
            "exceed wavelength/6. Refine mesh before this check.")
    return {
        "triangle_count": len(faces),
        "frequency_hz": freq_hz,
        "triangles_over_wavelength_six": over,
        "fraction_over_wavelength_six": fraction,
        "max_triangle_edge_mm": max(edge_maximum) * 1000.,
        "note": "Heuristic screen; not a mesh-convergence test.",
    }


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as data:
        for chunk in iter(lambda: data.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _check_processes_and_ram(min_free_gib):
    import psutil
    pids = [
        proc.pid for proc in psutil.process_iter(['name'])
        if (proc.info.get('name') or '').lower() in ('numcalc', 'numcalc.exe')
    ]
    if pids:
        raise RuntimeError(f"Other NumCalc processes are running: {pids}")
    free = psutil.virtual_memory().available / 1024 ** 3
    if free < min_free_gib:
        raise RuntimeError(
            f"Available physical RAM {free:.1f} GiB is below the "
            f"{min_free_gib:.1f} GiB minimum.")
    return free


def run(project, step=13, timeout=150, min_free_gib=6.):
    project = Path(project).expanduser().resolve()
    source = project / 'NumCalc' / 'source_1'
    nc_path = source / 'NC.inp'
    node_path = project / 'ObjectMeshes' / 'Reference' / 'Nodes.txt'
    element_path = project / 'ObjectMeshes' / 'Reference' / 'Elements.txt'
    solver = Path(__file__).resolve().parents[2] / (
        'mesh2hrtf/NumCalc/bin/NumCalc')
    if not solver.is_file():
        raise FileNotFoundError("Compiled NumCalc binary missing: " + str(solver))
    params = json.loads((project / 'parameters.json').read_text())
    cfg = json.loads((project / 'earlab_config.json').read_text())
    freqs = params['frequencies']
    if not 1 <= step <= len(freqs):
        raise ValueError(f"Frequency step {step} outside 1..{len(freqs)}")
    freq = float(freqs[step - 1])
    if not (0 < freq <= 20000 and math.isfinite(freq)):
        raise ValueError("Frequency is outside supported diagnostic range")
    mesh = _mesh_check(project, freq)
    original_nc = nc_path.read_text()
    forced_nc, old_mode = force_single_level(original_nc)
    if old_mode != '4':
        raise ValueError(f"Expected METHODBEM=4 in original NC.inp, got {old_mode}")
    if cfg['units'] not in ('mm', 'm'):
        raise ValueError("Unrecognized input units")
    free = _check_processes_and_ram(min_free_gib)
    original_sha = {
        "NC.inp": _sha256(nc_path),
        "Nodes.txt": _sha256(node_path),
        "Elements.txt": _sha256(element_path),
    }

    run_dir = (project / 'analysis' / (
        f'fmm_new_mesh_step{step}_' + dt.datetime.now().strftime(
            '%Y%m%d_%H%M%S_%f')))
    run_dir.mkdir(parents=True)
    for label in ('single_level', 'multilevel'):
        # Preserve Mesh2HRTF's conventional NumCalc/source_1 directory,
        # while keeping both FMM variants in entirely separate sandboxes.
        sandbox = run_dir / label
        sandbox.mkdir()
        for folder in ('ObjectMeshes', 'EvaluationGrids'):
            original = project / folder
            if not original.is_dir():
                raise FileNotFoundError("Missing exported geometry: " + str(original))
            (sandbox / folder).symlink_to(original, target_is_directory=True)
        (sandbox / 'parameters.json').write_text(json.dumps(params))
        (sandbox / 'earlab_config.json').write_text(json.dumps(cfg))
    print(f"[FOCUSED] {freq:.2f} Hz / step {step}. "
          "Two sequential isolated FMM solves, NO Blender/80-tone sweep.",
          flush=True)
    print(f"[FOCUSED] {mesh['triangle_count']} triangles; "
          f"{mesh['fraction_over_wavelength_six']*100:.2f}% exceed "
          "wavelength/6 at this frequency (not at 20 kHz).", flush=True)
    print(f"[FOCUSED] Free physical memory {free:.1f} GiB; "
          f"hard limit {timeout} seconds PER solve.", flush=True)
    print("[FOCUSED] Logs and results: " + str(run_dir), flush=True)

    runs = {}
    variants = [
        ("single_level", forced_nc, "Single level fast multipole BEM"),
        ("multilevel", original_nc, "Direct multilevel fast multipole BEM"),
    ]
    try:
        for label, input_text, method_log in variants:
            subfolder = run_dir / label / 'NumCalc' / 'source_1'
            subfolder.mkdir(parents=True)
            (subfolder / 'NC.inp').write_text(input_text)
            print(f"[FOCUSED] Running {label}, step {step} only...", flush=True)
            with (subfolder / 'stdout.txt').open('w') as stdout:
                try:
                    call = subprocess.run(
                        [str(solver), '-istart', str(step), '-iend', str(step)],
                        cwd=subfolder, stdout=stdout, stderr=subprocess.STDOUT,
                        timeout=timeout, check=False)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        f"{label} exceeded {timeout}s. "
                        f"Inspect {subfolder / 'stdout.txt'}") from exc
            log = subfolder / f'NC{step}-{step}.out'
            pressure_path = (
                subfolder / 'be.out' / f'be.{step}' / 'pEvalGrid')
            if call.returncode != 0:
                raise RuntimeError(
                    f"{label} returned exit code {call.returncode}; "
                    f"inspect {log} and {subfolder / 'stdout.txt'}")
            if not log.is_file() or not pressure_path.is_file():
                raise RuntimeError(
                    f"{label} wrote no complete microphone output; see {log}")
            log_text = log.read_text(errors='replace')
            if method_log not in log_text:
                raise RuntimeError(
                    f"{label} did not use expected FMM method "
                    f"({method_log}); see {log}")
            method, residual = completed_true_residual(log_text)
            pressure = load_three_node_pressure(pressure_path)
            gain, phase = gain_db(
                pressure, freq, cfg, params.get('speedOfSound', 343.))
            if not math.isfinite(gain):
                raise RuntimeError(f"{label} pressure produced nonfinite gain")
            runs[label] = {
                "method": method,
                "true_relative_residual": residual,
                "gain_db": gain, "phase_deg": phase,
                "pressure_real": pressure.real,
                "pressure_imag": pressure.imag,
                "log": str(log),
            }
            print(f"[FOCUSED] {label}: {gain:+.3f} dB; "
                  f"verified residual {residual:.3e}", flush=True)

        final_sha = {
            "NC.inp": _sha256(nc_path),
            "Nodes.txt": _sha256(node_path),
            "Elements.txt": _sha256(element_path),
        }
        if final_sha != original_sha:
            raise RuntimeError(
                "Original source input or mesh changed DURING testing; "
                "comparison rejected.")
        delta = runs['multilevel']['gain_db'] - runs['single_level']['gain_db']
        report = {
            "original_project": str(project), "step": step,
            "frequency_hz": freq, "mesh": mesh,
            "original_sha256": original_sha,
            "single_level": runs['single_level'],
            "multilevel": runs['multilevel'],
            "multilevel_minus_single_db": delta,
            "method_agreement_within_1_db": abs(delta) <= 1.,
            "interpretation": (
                "Solver-converged FMM method comparison only. Neither agreement "
                "nor disagreement validates physical pressure, geometry, "
                "BEM approximation accuracy, or mesh convergence."),
        }
        output = run_dir / 'comparison.json'
        output.write_text(json.dumps(report, indent=2) + '\n')
        print(f"[RESULT] Multilevel minus single-level = {delta:+.3f} dB",
              flush=True)
        print("[RESULT] " + str(output), flush=True)
        if abs(delta) > 1:
            print("[UNVERIFIED] Large numerical-method disagreement remains. "
                  "Do not trust either method as a physical measurement.",
                  flush=True)
        else:
            print("[UNVERIFIED] Methods agree within 1 dB at this point, "
                  "but mesh convergence and physical accuracy are untested.",
                  flush=True)
        return report
    except BaseException as exc:
        # Preserve ALL outputs from any completed or failed variant.
        (run_dir / 'failed_comparison.json').write_text(json.dumps({
            "error": str(exc), "completed_methods": runs,
            "step": step, "frequency_hz": freq}, indent=2) + '\n')
        raise


def main():
    parser = argparse.ArgumentParser(
        description="One selected frequency from a clean exported Mesh2HRTF project")
    parser.add_argument('--project', required=True)
    parser.add_argument('--step', type=int, default=13)
    parser.add_argument('--timeout', type=int, default=150,
                        help="Maximum seconds PER method, not an ETA")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 900:
        parser.error('--timeout must be 1..900 seconds')
    try:
        run(args.project, args.step, args.timeout)
    except Exception as exc:
        print("[FOCUSED FAILED] " + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
