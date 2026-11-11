#!/usr/bin/env python3
"""One-command Mesh2HRTF cavity analysis. Requires real Blender/NumCalc."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

from analysis_tools import mic_grid, get_sofa_response, reference_transfer, save_analysis, audition_music
from mesh_quality import analyze_exported_mesh
from frequency_grid import log_overview, linear_overview, replace_frequency_vector
from share_results import prepare_snapshot
from resume_audit import save_audit, print_audit

REPO_ROOT = Path(__file__).resolve().parents[2]

PRESETS = {
    '20 kHz fast (80 tones)': (100, 20000, 100),  # safe legacy placeholder; Blender exports 80 steps
    '24 kHz fast (80 tones)': (100, 24000, 100),  # safe legacy placeholder; Blender exports 80 steps
    '20 kHz overview (100 Hz)': (100, 20000, 100),
    '24 kHz overview (100 Hz)': (100, 24000, 100),
    'Log overview 80 tones': (100, 20000, 100),  # legacy geometric survey
    'Quick ~1kHz': (500, 3500, 100),
    '10 kHz overview (100 Hz)': (100, 10000, 100),
    '10 kHz detailed (25 Hz)': (100, 10000, 25),
    '10 kHz deep (10 Hz)': (100, 10000, 10),
    'Focused ~1kHz': (700, 1600, 10),
    'Deep 1kHz': (700, 1600, 5),
    'Full spectrum': (100, 20000, 100),
    'Detailed spectrum': (100, 20000, 25),
}

FAST_PRESETS = {
    '20 kHz fast (80 tones)': (80, 100., 20000.),
    '24 kHz fast (80 tones)': (80, 100., 24000.),
}


def export_frequency_settings(preset):
    """Return a Blender Mesh2Input-valid frequency setup.

    Fast/nonuniform scans use an 80-step temporary project which is replaced
    with the exact intended frequency vector after export. Step-size sweeps
    must divide the interval *exactly* or Mesh2Input raises before NumCalc.
    """
    low, high, increment = PRESETS[preset]
    if preset in FAST_PRESETS:
        return 'Num steps', FAST_PRESETS[preset][0]
    if preset == 'Log overview 80 tones':
        return 'Num steps', 80
    if (low >= high or increment <= 0 or
            (high - low) % increment != 0):
        raise ValueError(
            f"Invalid Mesh2Input step for {preset!r}: "
            f"{increment:g} does not divide {high-low:g} Hz")
    return 'Step size', increment


def safe_numcalc_options(args):
    """Conservative system-wide physical-memory cap, without swap budget."""
    import psutil
    total_gib = psutil.virtual_memory().total / (1024 ** 3)
    # Reserve physical RAM for Linux and unestimated allocations, but do not
    # strand otherwise available RAM behind an arbitrary 35% cap. On a
    # 30 GiB machine, this reserves 2.5 GiB; 13 GiB available can satisfy
    # a 9.78 GiB safety-adjusted job without scheduling swap.
    reserved = max(2.5, total_gib * 0.08)
    if total_gib <= reserved + 1.0:
        raise RuntimeError("Not enough physical RAM after NumCalc safety reserve")
    budget = total_gib - reserved
    workers = min(int(args.jobs), 2)
    print(f"[RAM GUARD] {total_gib:.1f} GiB physical RAM; "
          f"reserve {reserved:.1f} GiB; global memory ceiling {budget:.1f} GiB; "
          f"{workers} worker(s).", flush=True)
    print("[RAM GUARD] Only psutil.available physical RAM counts, not swap. "
          "2x NumCalc estimates; 30-second stagger.", flush=True)
    return dict(max_ram_load=budget, ram_safety_factor=2.0,
                max_instances=workers, wait_time=30, max_cpu_load=90,
                confirm_errors=False)

def step(title):
    print('\n' + '=' * 12 + ' ' + title + ' ' + '=' * 12, flush=True)


def call(args, cwd=None):
    print('$ ' + ' '.join(map(str,args)), flush=True)
    subprocess.run(list(map(str,args)),cwd=cwd,check=True)


def _preflight_stl(stl,units,mic,source):
    try:
        import trimesh
        m=trimesh.load_mesh(str(stl),process=False)
        if isinstance(m,trimesh.Scene):
            m=m.dump(concatenate=True)
        if len(m.faces)==0:raise ValueError('STL contains no triangles')
        scale = .001 if units=='mm' else 1
        bbox=np_array(m.bounds)*scale
        print('[MESH] Triangles:',len(m.faces),flush=True)
        print('[MESH] Bounding box (m):',bbox.tolist(),flush=True)
        if not m.is_watertight:
            print('[WARNING] STL is not watertight. BEM scattering and cavity results may be invalid.',flush=True)
        if not m.is_winding_consistent:
            print('[WARNING] Inconsistent triangle winding may invalidate internal/external fluid regions.',flush=True)
        if len(m.faces)>150000:
            print('[WARNING] >150k triangles; CPU BEM frequency solves could be very expensive. Decimate outside the canal first.',flush=True)
        for name,coords in [('Mic',mic),('Source',source)]:
            c=np_array(coords)*scale
            if name=='Mic' and not np_all((c>=bbox[0]-.005)&(c<=bbox[1]+.005)):
                print('[WARNING] Microphone lies outside or far beyond the STL bounding box.',flush=True)
        return {'triangles':len(m.faces),'watertight':bool(m.is_watertight),
                'winding_consistent':bool(m.is_winding_consistent),
                'bounds_m':bbox.tolist()}
    except ImportError:
        print('[INFO] trimesh not installed; skipping optional mesh preflight.',flush=True)
        return {}


def np_array(x):
    import numpy as np
    return np.asarray(x,float)

def np_all(x):
    import numpy as np
    return np.all(x)


def check_cached_solver_convergence(project, threshold=1e-8):
    """Block resume when finished-looking outputs came from failed CGS solves.

    The manager checks only for pEvalGrid existence. Old nonconverged data
    would otherwise be silently reused after installing a newer solver.
    """
    from spectral_diagnostics import inspect_numcalc_logs
    project = Path(project)
    checks = inspect_numcalc_logs(project, residual_warning=threshold)
    results = project / 'NumCalc' / 'source_1' / 'be.out'
    # The report caps details at 100 entries. Fail closed if more failures
    # exist than can be checked against their cached pressure files.
    if (checks['steps_over_threshold'] >
            len(checks['steps_over_threshold_details'])):
        raise RuntimeError(
            'More than 100 nonconverged solver steps are recorded and cached '
            'results cannot be fully verified. Keep this project for '
            'inspection; start a NEW fast scan instead.')
    flagged = []
    for hit in checks['steps_over_threshold_details']:
        step_number = int(hit['step'])
        if (results / f'be.{step_number}' / 'pEvalGrid').is_file():
            flagged.append((step_number, float(hit['frequency_hz']),
                            float(hit['relative_error'])))
    if flagged:
        samples = ', '.join(f'{hz:g} Hz (step {step}, residual {err:.2g})'
                            for step, hz, err in flagged[:5])
        raise RuntimeError(
            f'{len(flagged)} cached NumCalc frequency steps failed convergence '
            f'(relative residual > {threshold:g}); examples: {samples}. '
            'NumCalc would incorrectly skip these old pressure files on '
            'resume. Preserve this project for inspection and start a NEW '
            'fast scan with the repaired solver instead.')
    if checks['steps_with_reported_residual'] == 0:
        print('[RESUME WARNING] No stored CGS/GMRES convergence records found; '
              'existing cached pressure outputs cannot be verified.', flush=True)
    else:
        print(f"[RESUME] Checked {checks['steps_with_reported_residual']} "
              "saved solver residual records (not completed frequencies).", flush=True)


def resume_project(args):
    """Resume missing NumCalc frequency steps in an existing export."""
    project = Path(args.resume).expanduser().resolve()
    if not (project/'NumCalc'/'source_1'/'NC.inp').is_file():
        raise FileNotFoundError('No existing NumCalc input to resume: '+str(project))
    cfg = json.loads((project/'earlab_config.json').read_text())
    repo = Path(args.mesh2hrtf).expanduser().resolve()
    binary = repo/'mesh2hrtf'/'NumCalc'/'bin'/'NumCalc'
    call(['make','-C',repo/'mesh2hrtf'/'NumCalc'/'src','-j2'])
    if not binary.is_file():
        raise FileNotFoundError('NumCalc executable not found: '+str(binary))
    import mesh2hrtf as m2h
    step('Resume existing BEM calculation')
    print('[RESUME] Reusing the previous geometry and frequencies:', project, flush=True)
    print('[RESUME] Completed frequency steps are not recalculated by manage_numcalc.',flush=True)
    print('[RESUME] Do not start another NumCalc manager for this same project.',flush=True)
    audit, report = save_audit(project)
    print_audit(audit, report)
    check_cached_solver_convergence(project)
    rejected = [r for r in audit["steps"]
                if r["status"] == "solver_rejected"]
    if rejected and not getattr(args, "retry_failed", False):
        examples = ", ".join(
            f"step {r['step']} ({r['frequency_hz']} Hz)"
            for r in rejected[:8])
        raise RuntimeError(
            f"{len(rejected)} frequencies previously failed both CGS and "
            f"GMRES (e.g. {examples}). Automatically retrying unchanged "
            "numerical failures wastes hours. See " + str(report) + ". "
            "Inspect mesh and solver settings, or explicitly pass "
            "--retry-failed to reattempt. Completed outputs remain intact.")
    m2h.manage_numcalc(project_path=str(project),
                      numcalc_path=str(binary),
                      stop_on_failed_step=True, **safe_numcalc_options(args))
    step('Convert completed NumCalc output to microphone analysis')
    m2h.output2hrtf(str(project))
    sofa = project/'Output2HRTF'/'HRTF_MicProbe.sofa'
    if not sofa.is_file():
        raise RuntimeError('Missing microphone SOFA after resumed simulation')
    frequencies, pressure = get_sofa_response(sofa)
    response = reference_transfer(frequencies,pressure,cfg['mic'],
                                   cfg['source'],cfg['units'])
    output = project/'analysis'
    save_analysis(output,frequencies,response,pressure,cfg['mic'],
                  cfg['source'],cfg['units'])
    try:
        from reanalyze import render_project
        print('[RESUME] Report:',render_project(project),flush=True)
    except Exception as exc:
        print('[REPORT WARNING]',str(exc),flush=True)
    try:
        print('[SHARE] Snapshot:',prepare_snapshot(project),flush=True)
    except Exception as exc:
        print('[SHARE WARNING]',str(exc),flush=True)
    print('RESULT_DIR='+str(output),flush=True)
    print('RUN_FINISHED',flush=True)
    return 0


def run(args):
    if getattr(args, "inspect_resume", ""):
        audit, path = save_audit(args.inspect_resume)
        print_audit(audit, path)
        # Salvage the verified partial response without pretending that the
        # missing frequencies have been calculated. Never modify response.csv.
        try:
            from partial_response import export_partial_response
            summary, csv_path = export_partial_response(args.inspect_resume)
            print(f"[PARTIAL] {summary['verified_points']} verified "
                  f"frequency points -> {csv_path}", flush=True)
            if summary['verified_points']:
                print("[PARTIAL] Scatter plot -> " +
                      str(csv_path.with_suffix('.png')), flush=True)
        except Exception as exc:
            print(f"[PARTIAL WARNING] Cannot export verified samples: {exc}",
                  flush=True)
        return 0
    if args.resume:
        return resume_project(args)
    mesh2hrtf=Path(args.mesh2hrtf).expanduser().resolve()
    mesh2module=mesh2hrtf/'mesh2hrtf'
    exporter=mesh2module/'Mesh2Input'/'mesh2input.py'
    binary=mesh2module/'NumCalc'/'bin'/'NumCalc'
    if not exporter.is_file():
        raise FileNotFoundError('No Mesh2HRTF exporter at '+str(exporter)+'. Point --mesh2hrtf to its cloned REPOSITORY ROOT.')
    stl=Path(args.stl).expanduser().resolve()
    if not stl.is_file(): raise FileNotFoundError('STL file missing: '+str(stl))
    blender=shutil.which('blender')
    if not blender:raise RuntimeError('Blender not found in PATH. Install Blender 3.6+ or Blender 4/5.')
    if args.preset not in PRESETS:raise ValueError('Unknown preset: '+args.preset)
    fmin,fmax,stepf=PRESETS[args.preset]
    export_type, export_value = export_frequency_settings(args.preset)
    mic=np_array(args.mic).tolist()
    if args.source_mode=='Custom XYZ':
        source=np_array(args.source).tolist()
    else:
        unit_dist = .001 if args.units=='mm' else 1.0
        metres=.12 if args.source_mode=='Near 12 cm' else 1.0
        shift=metres/unit_dist
        axis={'-X':(-1,0,0),'+X':(1,0,0),'-Y':(0,-1,0),
              '+Y':(0,1,0),'-Z':(0,0,-1),'+Z':(0,0,1)}[args.axis]
        source=(np_array(mic)+np_array(axis)*shift).tolist()
    if (np_array(source)==np_array(mic)).all():
        raise ValueError('Source and mic cannot be the same coordinate')
    result_root=Path(args.results).expanduser().resolve()
    result_root.mkdir(parents=True,exist_ok=True)
    timestamp=dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    project=result_root/('ear_'+timestamp)
    if project.exists():raise FileExistsError(str(project))
    project.mkdir()
    grid=mic_grid(project/'MicProbe',mic,args.units)
    cfg={
      'mesh2hrtf':str(mesh2hrtf),'stl':str(stl),'units':args.units,
      'mic':mic,'source':source,'min_freq':fmin,'max_freq':fmax,
      'step_freq':stepf,
      'export_frequency_type':export_type,
      'export_frequency_value':export_value,
      'project':str(project),'grid':str(grid),
      'music':str(Path(args.music).expanduser()) if args.music else '',
      'preset':args.preset,
    }
    (project/'earlab_config.json').write_text(json.dumps(cfg,indent=2))
    step('Input geometry')
    mesh_stats=_preflight_stl(stl,args.units,mic,source)
    (project/'mesh_preflight.json').write_text(json.dumps(mesh_stats,indent=2))
    print('[RUN] Project: '+str(project),flush=True)
    print("[INFO] Blender's Mesh2HRTF exporter is registered automatically for this run; no manual add-on installation.",flush=True)
    print('[RUN] Mic / source:',mic,'/',source,'('+args.units+')',flush=True)
    if args.preset in FAST_PRESETS:
        _, low, high = FAST_PRESETS[args.preset]
        print(f'[RUN] 80 linearly spaced frequencies across {low:g}–{high:g} Hz; '
              'fast screening only, narrow resonances may be missed.',flush=True)
    elif args.preset == 'Log overview 80 tones':
        print('[RUN] 80 geometric frequencies across 100–20000 Hz; this is a *coarse* broadband survey.',flush=True)
    else:
        print('[RUN] '+args.preset+f': {fmin}-{fmax} Hz in {stepf} Hz steps',flush=True)
    print('[NOTE] Source at 1 metre does not require a larger BEM domain; it is NOT inherently a more expensive mode.',flush=True)
    print('[NOTE] Simulated response assumes rigid surfaces, ideal point source, ideal non-loading microphone.',flush=True)
    step('Export STL and custom microphone probe using Blender')
    call([blender,'--background','--python-exit-code','1','--python',Path(__file__).resolve().parent/'blender_export.py',
          '--','--config',project/'earlab_config.json'])
    if not (project/'NumCalc'/'source_1'/'NC.inp').is_file():
        raise RuntimeError('Blender export did not create a valid NumCalc input')
    if args.preset in FAST_PRESETS:
        count = replace_frequency_vector(project, linear_overview(*FAST_PRESETS[args.preset]))
        print(f'[RUN] Installed {count} endpoint-exact broadband frequencies '
              'in NumCalc and parameters.json', flush=True)
    elif args.preset == 'Log overview 80 tones':
        count = replace_frequency_vector(project, log_overview())
        print(f'[RUN] Installed {count} nonuniform frequency steps in NumCalc and parameters.json', flush=True)
    step('Check exported triangle quality (NumCalc exact thresholds)')
    quality = analyze_exported_mesh(project, args.units)
    print(f"[MESH QUALITY] {quality['triangle_count']} triangles; "
          f"{quality['warning_count']} warnings; {quality['fatal_count']} fatal slivers",
          flush=True)
    if quality["fatal_count"] or quality["warning_count"]:
        print("[MESH QUALITY] CSV: " + quality["report_csv"], flush=True)
        print("[MESH QUALITY] Colored Blender overlay: " + quality["overlay_obj"], flush=True)
        for hit in quality["worst"][:8]:
            print(f"  Element {hit['element_id']:6d}: {hit['grade']:5} "
                  f"ratio {hit['ratio']:.1f}, max edge {hit['max_edge_mm']:.4f} mm, "
                  f"centroid ({hit['centroid_x_mm']:.3f}, "
                  f"{hit['centroid_y_mm']:.3f}, {hit['centroid_z_mm']:.3f}) mm",
                  flush=True)
    print(f"[MESH RESOLUTION] Longest edge: median={quality['longest_edge_mm_p50']:.2f} mm, "
          f"p95={quality['longest_edge_mm_p95']:.2f} mm, "
          f"max={quality['longest_edge_mm_max']:.2f} mm", flush=True)
    for estimate in quality["frequency_resolution"]:
        if estimate["frequency_hz"] > fmax:
            continue
        pct = 100 * estimate["fraction_over_lambda_over_6"]
        print(f"[MESH RESOLUTION] {estimate['frequency_hz']} Hz: "
              f"{pct:.1f}% of triangle longest edges exceed "
              f"lambda/6 ({estimate['lambda_over_6_edge_mm']:.2f} mm)", flush=True)
        if pct > 5:
            print("[WARNING] Mesh is not adequately resolved at this frequency "
                  "by the lambda/6 screening rule. Results may be invalid; "
                  "compare with a refined mesh before trusting peaks.", flush=True)
    if quality["fatal_count"]:
        print("[MESH QUALITY] NumCalc would abort on these triangles. "
              "Fix their geometry locally; no change to microphone coordinates is needed.",
              flush=True)
        print("[MESH QUALITY] Open the saved '3d Model.blend' and select the "
              "listed Reference polygon indices, or import mesh_bad_triangles.obj "
              "over the original STL to see the thin faces.", flush=True)
        raise RuntimeError(
            f"{quality['fatal_count']} triangles exceed NumCalc's aspect-ratio limit "
            f"(max_edge/(1.51967*sqrt(area)) > 100). "
            f"First element: {quality['fatal_ids'][0]}. "
            f"See {quality['report_csv']} for coordinates and Blender overlay.")
    step('Recompile NumCalc if source files changed')
    call(['make','-C',mesh2module/'NumCalc'/'src','-j2'])
    if not binary.is_file():raise RuntimeError('NumCalc missing after build: '+str(binary))
    if not os.access(binary,os.X_OK):
        raise PermissionError('NumCalc is not executable: '+str(binary))
    step('Check NumCalc input and estimate memory')
    # Mesh2HRTF's manager suppresses the estimator's stdout, which made
    # errors unhelpful ("CalledProcessError ... -estimate_ram"). Run it
    # once ourselves, printing the diagnostic and the solver's NC.out.
    source_folder = project/'NumCalc'/'source_1'
    memory_file = source_folder/'Memory.txt'
    if memory_file.exists():
        memory_file.unlink()  # do not trust a partial file from a failed run
    estimate = subprocess.run(
        [str(binary), '-estimate_ram'],
        cwd=source_folder, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, errors='replace')
    if estimate.returncode:
        output = estimate.stdout or '(NumCalc produced no terminal output)'
        print('[NUMCALC ERROR] RAM estimation failed. Solver output follows:', flush=True)
        print(output[-24000:], flush=True)
        report = source_folder/'NC.out'
        if report.is_file():
            print('[NUMCALC ERROR] Last 160 lines of NC.out:', flush=True)
            print('\n'.join(report.read_text(errors='replace').splitlines()[-160:]),
                  flush=True)
        raise RuntimeError(
            f'NumCalc -estimate_ram failed with exit code {estimate.returncode}. '
            f'See diagnostic above and {report}. '
            'Mesh2HRTF may reject open/self-intersecting surfaces, bad normals, '
            'or invalid mesh/solver input.')
    if not memory_file.is_file() or memory_file.stat().st_size == 0:
        raise RuntimeError(
            f'NumCalc exited successfully but generated no RAM estimates: {memory_file}')
    print('[NUMCALC] RAM estimation completed:', memory_file, flush=True)
    step('Solve frequency-domain acoustic boundary problem')
    try:
        import mesh2hrtf as m2h
    except ImportError as e:
        raise ImportError('Python mesh2hrtf package not installed. Run: pip install -e '+str(mesh2hrtf)) from e
    print('[BEM] Starting CPU solver. Number of frequency points:',
          80 if args.preset in FAST_PRESETS or args.preset == 'Log overview 80 tones'
          else 1+int((fmax-fmin)//stepf),flush=True)
    # Memory-aware parallelization via the original NumCalc manager. Avoid
    # promising a CUDA speedup: Mesh2HRTF/NumCalc is CPU based.
    m2h.manage_numcalc(
        project_path=str(project), numcalc_path=str(binary),
        stop_on_failed_step=True, **safe_numcalc_options(args))
    step('Convert actual solver pressures to SOFA and plots')
    m2h.output2hrtf(str(project))
    sofa=project/'Output2HRTF'/'HRTF_MicProbe.sofa'
    if not sofa.is_file():
        raise RuntimeError('NumCalc completed, but no HRTF_MicProbe.sofa was produced. Inspect Output2HRTF/report_issues.txt')
    freq,pressure=get_sofa_response(sofa)
    H=reference_transfer(freq,pressure,mic,source,args.units)
    output=project/'analysis'
    peaks=save_analysis(output,freq,H,pressure,mic,source,args.units)
    # Reuse the full NumCalc output (including the surface pressures) to build
    # an interpretable report. A visualization failure must never discard
    # the successful physical computation or the original numeric results.
    try:
        from reanalyze import render_project
        report = render_project(project)
        print('[RESULT] Interactive HTML summary: ' + str(report), flush=True)
        print('[RESULT] Surface pressure maps: ' + str(output/'surface_pressure.png'), flush=True)
    except Exception as exc:
        print('[REPORT WARNING] Enhanced interpretation unavailable: ' + str(exc),
              flush=True)
        print('[REPORT WARNING] Original response.csv, response.png and SOFA remain valid.',
              flush=True)
    print('[RESULT] Response: '+str(output/'response.png'),flush=True)
    print('[RESULT] CSV: '+str(output/'response.csv'),flush=True)
    print('[RESULT] Peaks:',flush=True)
    for item in peaks[:10]:
        print(f"  {item['frequency_hz']:8.1f} Hz  {item['gain_db']:+7.1f} dB  "
              f"prominence {item['prominence_db']:.1f} dB  Q~{item['q']:.1f}"
              + ("  [UNVERIFIED]" if item.get("unverified_spectrum") else ""),flush=True)
    if not peaks:print('  No peaks greater than 3 dB prominence within simulated band.',flush=True)
    if args.music:
        step('Offline music audition — interpolate simulated transfer spectrum')
        try:
            info=audition_music(args.music,freq,H,output/'music_audition.wav')
            print('[RESULT] '+str(output/'music_audition.wav'),info,flush=True)
        except Exception as e:
            print('[MUSIC] Could not audition:',e,flush=True)
            print('[MUSIC] The physical BEM frequency response and graphs are still saved.',flush=True)
    (output/'README.txt').write_text('''Mesh2HRTF NumCalc produced this 3D linear-wave response.\n\nresponse.csv / response.png show H(f) as microphone pressure divided by\nfree-field point-source pressure at the same true source-to-mic distance.\n\nThe model assumes rigid PLA-like boundaries, open air connected to the\nsource, ideal point source, and an ideal zero-volume non-loading microphone.\nIt does NOT yet simulate the microphone capsule diaphragm impedance, tube\nviscothermal losses, PLA mechanical vibration, or nonlinear distortion.\nIf the STL does not contain an open, air-connected canal and tube, its\nresults CANNOT diagnose those cavities. Validate mesh geometry first.\n\nThe WAV audition interpolates a possibly sparse frequency spectrum and is\nonly a qualitative comparison, NOT a verified full-band physical HRIR.\nThe source distance does not change boundary mesh size in this solver.\n''')
    try:
        shared = prepare_snapshot(project)
        print('[SHARE] Ready for GitHub:', shared,flush=True)
        print('[SHARE] Commit and push tools/ear_acoustic_lab/shared_runs to let me inspect it.',flush=True)
    except Exception as exc:
        print('[SHARE WARNING] Results saved; snapshot creation failed:',exc,flush=True)
    print('\nRESULT_DIR='+str(output),flush=True)
    print('RUN_FINISHED',flush=True)
    return 0


def get_args():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mesh2hrtf',default=str(REPO_ROOT))
    ap.add_argument('--resume',default='',help='Resume a previously exported NumCalc project without re-exporting STL')
    ap.add_argument('--inspect-resume',default='',metavar='PROJECT',
                    help='Audit frequency completion without running NumCalc')
    ap.add_argument('--retry-failed',action='store_true',
                    help='Deliberately rerun known CGS/GMRES failures on resume')
    ap.add_argument('--stl',default='~/model.stl')
    ap.add_argument('--units',choices=['mm','m'],default='mm')
    ap.add_argument('--mic',nargs=3,type=float,default=[0,0,0],metavar=('X','Y','Z'))
    ap.add_argument('--source-mode',choices=['Near 12 cm','Far 1 m','Custom XYZ'],default='Near 12 cm')
    ap.add_argument('--axis',choices=['+Y','-Y','-X','+X','-Z','+Z'],default='+Y')
    ap.add_argument('--source',nargs=3,type=float,default=[0,120,0],metavar=('X','Y','Z'))
    ap.add_argument('--preset',choices=list(PRESETS),default='20 kHz fast (80 tones)')
    ap.add_argument('--results',default=str(REPO_ROOT / 'earlab_runs'))
    ap.add_argument('--music',default='')
    ap.add_argument('--jobs',type=int,default=1)
    a=ap.parse_args()
    if a.jobs<1 or a.jobs>32:ap.error('--jobs must be in 1..32')
    return a

if __name__=='__main__':
    try:
        sys.exit(run(get_args()))
    except Exception:
        traceback.print_exc()
        print('RUN_FAILED',flush=True)
        sys.exit(1)