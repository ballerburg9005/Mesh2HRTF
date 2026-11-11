#!/usr/bin/env python3
"""Cross-check one saved ML-FMM frequency with independent single-level FMM.

Does not export Blender geometry, run a frequency sweep, or modify original
project input/output files. Verifies both numerical residuals independently.
"""
import argparse
import datetime
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys


def force_single_level(nc_input):
    """Change Mesh2Input Main Parameters I METHODBEM from 4 to 1."""
    lines = nc_input.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == '## 1. Main Parameters I':
            for k in range(index + 1, min(index + 5, len(lines))):
                if lines[k].strip() and not lines[k].lstrip().startswith('#'):
                    args = lines[k].split()
                    if len(args) < 9 or args[7] not in ('1', '4'):
                        raise ValueError('Unexpected METHODBEM: ' + lines[k].strip())
                    before = args[7]
                    args[7] = '1'
                    lines[k] = ' '.join(args) + '\n'
                    return ''.join(lines), before
            break
    raise ValueError('Could not locate NumCalc Main Parameters I')


def load_three_node_pressure(path):
    """Validate exact 3-node MicProbe format before averaging pressure."""
    lines = Path(path).read_text(errors='replace').splitlines()
    if len(lines) != 6 or lines[2].split() != ['1', '3']:
        raise ValueError('pEvalGrid is not an exact three-node MicProbe')
    values = {}
    for line in lines[3:]:
        tokens = line.split()
        if len(tokens) != 3:
            raise ValueError('Malformed microphone pressure data')
        i = int(tokens[0])
        p = complex(float(tokens[1]), float(tokens[2]))
        if not math.isfinite(p.real) or not math.isfinite(p.imag):
            raise ValueError('Nonfinite microphone pressure')
        if i in values:
            raise ValueError('Repeated microphone node')
        values[i] = p
    if set(values) != {10000000, 10000001, 10000002}:
        raise ValueError('Unexpected microphone node identifiers')
    return sum(values.values()) / 3


def completed_true_residual(log):
    """Require completed NumCalc and a successful, verified solver residual."""
    if 'End time:' not in log or (
        'GMRES fallback failed after' in log or
        'CGS and GMRES failed convergence' in log
    ):
        raise ValueError('NumCalc has not finished a valid calculation')
    gm = re.search(
        r'GMRES fallback:\s*iterations\s*=\s*\d+,\s*true relative residual\s*=\s*([\d.eE+-]+)',
        log)
    cg = re.search(r'CGS TRUE relative residual\s*=\s*([\d.eE+-]+)', log)
    match = gm or cg
    if not match:
        raise ValueError('No verified solver true residual in log')
    residual = float(match.group(1))
    if not math.isfinite(residual) or residual > 1e-8:
        raise ValueError(f'True residual {residual:g} exceeds 1e-8')
    return ('GMRES' if gm else 'CGS'), residual


def gain_db(pressure, frequency, config, speed):
    import numpy as np
    from analysis_tools import reference_transfer
    response = reference_transfer(
        np.asarray([frequency], dtype=float),
        np.asarray([pressure], dtype=complex),
        config['mic'], config['source'], config['units'], speed=float(speed))[0]
    return (20 * math.log10(abs(response)),
            float(math.degrees(math.atan2(response.imag, response.real))))


def run_crosscheck(project, step=13, timeout=180, min_free_gib=6.):
    project = Path(project).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    solver = repo / 'mesh2hrtf' / 'NumCalc' / 'bin' / 'NumCalc'
    source = project / 'NumCalc' / 'source_1'
    metadata = json.loads((project / 'parameters.json').read_text())
    config = json.loads((project / 'earlab_config.json').read_text())
    freqs = metadata['frequencies']
    if not 1 <= step <= len(freqs):
        raise ValueError(f'Step {step} outside grid of {len(freqs)}')
    frequency = float(freqs[step - 1])
    old_log = source / f'NC{step}-{step}.out'
    old_pressure = source / 'be.out' / f'be.{step}' / 'pEvalGrid'
    if not old_log.is_file() or not old_pressure.is_file():
        raise FileNotFoundError('Existing multilevel comparison missing at step ' + str(step))
    old_method, old_residual = completed_true_residual(
        old_log.read_text(errors='replace'))
    old_pressure_mean = load_three_node_pressure(old_pressure)

    import psutil
    workers = [
        p.pid for p in psutil.process_iter(['name'])
        if (p.info['name'] or '').lower() in ('numcalc', 'numcalc.exe')
    ]
    if workers:
        raise RuntimeError(f'NumCalc already running with PIDs {workers}')
    free = psutil.virtual_memory().available / 1024**3
    if free < min_free_gib:
        raise RuntimeError(
            f'Only {free:.1f} GiB physical memory available; require {min_free_gib:.1f} GiB')
    if not solver.is_file():
        raise FileNotFoundError('NumCalc binary missing: ' + str(solver))

    working = (
        project / 'analysis' /
        ('fmm_single_level_step' + str(step) + '_' +
         datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    )
    work_source = working / 'NumCalc' / 'source_1'
    work_source.mkdir(parents=True)
    for folder in ('ObjectMeshes', 'EvaluationGrids'):
        original = project / folder
        if not original.is_dir():
            raise FileNotFoundError('Original geometry missing: ' + str(original))
        (working / folder).symlink_to(original, target_is_directory=True)
    edited, old_mode = force_single_level((source / 'NC.inp').read_text())
    if old_mode != '4':
        raise ValueError(
            f'Original project is not multilevel FMM (METHODBEM={old_mode})')
    (work_source / 'NC.inp').write_text(edited)
    shutil.copy2(project / 'parameters.json', working / 'parameters.json')
    print(f'[ONE POINT] {frequency:.2f} Hz, step {step}, ML-FMM vs SL-FMM', flush=True)
    print(f'[ONE POINT] Existing multilevel true residual: {old_residual:g} '
          f'({old_method})', flush=True)
    print(f'[ONE POINT] Separate output: {working}', flush=True)
    print(f'[ONE POINT] Hard timeout {timeout} seconds; no Blender/sweep.', flush=True)

    with (working / 'numcalc_stdout.txt').open('w') as stream:
        try:
            outcome = subprocess.run(
                [str(solver), '-istart', str(step), '-iend', str(step)],
                cwd=work_source, stdout=stream, stderr=subprocess.STDOUT,
                timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f'NumCalc exceeded {timeout}-second limit. Logs: {working}') from exc

    new_log = work_source / f'NC{step}-{step}.out'
    new_pressure_file = work_source / 'be.out' / f'be.{step}' / 'pEvalGrid'
    if outcome.returncode != 0:
        raise RuntimeError(
            f'NumCalc exited {outcome.returncode}; see {working / "numcalc_stdout.txt"} and {new_log}')
    if not new_log.is_file() or not new_pressure_file.is_file():
        raise RuntimeError(
            f'No complete output; see {working / "numcalc_stdout.txt"} and {new_log}')
    new_method, new_residual = completed_true_residual(
        new_log.read_text(errors='replace'))
    new_pressure_mean = load_three_node_pressure(new_pressure_file)
    old_gain, old_phase = gain_db(
        old_pressure_mean, frequency, config, metadata.get('speedOfSound', 343.))
    new_gain, new_phase = gain_db(
        new_pressure_mean, frequency, config, metadata.get('speedOfSound', 343.))
    difference = new_gain - old_gain
    record = {
        'step': step, 'frequency_hz': frequency,
        'multilevel': {
            'gain_db': old_gain, 'phase_deg': old_phase,
            'method': old_method, 'true_relative_residual': old_residual,
        },
        'single_level': {
            'gain_db': new_gain, 'phase_deg': new_phase,
            'method': new_method, 'true_relative_residual': new_residual,
        },
        'single_minus_multilevel_db': difference,
        'interpretation': (
            'Method disagreement, not proof either solution matches real acoustics.'),
    }
    (working / 'comparison.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'[RESULT] multilevel {old_gain:+.3f} dB, '
          f'single-level {new_gain:+.3f} dB', flush=True)
    print(f'[RESULT] single minus multilevel: {difference:+.3f} dB', flush=True)
    print(f'[RESULT] new true residual: {new_residual:.2e}', flush=True)
    print(f'[RESULT] {working / "comparison.json"}', flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(
        description='Run one independent SL-FMM check of an existing ML-FMM result')
    parser.add_argument('--project', required=True)
    parser.add_argument('--step', type=int, default=13)
    parser.add_argument('--timeout', type=int, default=180)
    opts = parser.parse_args()
    if opts.timeout < 1 or opts.timeout > 900:
        parser.error('--timeout must be between 1 and 900 seconds')
    try:
        run_crosscheck(opts.project, opts.step, opts.timeout)
    except Exception as exc:
        print('[CROSSCHECK FAILED] ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
