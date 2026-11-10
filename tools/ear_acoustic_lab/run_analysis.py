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

REPO_ROOT = Path(__file__).resolve().parents[2]

PRESETS = {
    'Quick ~1kHz': (500, 3500, 100),
    'Focused ~1kHz': (700, 1600, 10),
    'Deep 1kHz': (700, 1600, 5),
    'Full spectrum': (100, 20000, 100),
    'Detailed spectrum': (100, 20000, 25),
}


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


def run(args):
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
      'step_freq':stepf,'project':str(project),'grid':str(grid),
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
    print('[RUN] '+args.preset+f': {fmin}-{fmax} Hz in {stepf} Hz steps',flush=True)
    print('[NOTE] Source at 1 metre does not require a larger BEM domain; it is NOT inherently a more expensive mode.',flush=True)
    print('[NOTE] Simulated response assumes rigid surfaces, ideal point source, ideal non-loading microphone.',flush=True)
    step('Export STL and custom microphone probe using Blender')
    call([blender,'--background','--python',Path(__file__).resolve().parent/'blender_export.py',
          '--','--config',project/'earlab_config.json'])
    if not (project/'NumCalc'/'source_1'/'NC.inp').is_file():
        raise RuntimeError('Blender export did not create a valid NumCalc input')
    step('Compile NumCalc if necessary')
    if not binary.is_file():
        call(['make','-C',mesh2module/'NumCalc'/'src','-j2'])
    if not binary.is_file():raise RuntimeError('NumCalc missing after build: '+str(binary))
    if not os.access(binary,os.X_OK):
        raise PermissionError('NumCalc is not executable: '+str(binary))
    step('Solve frequency-domain acoustic boundary problem')
    try:
        import mesh2hrtf as m2h
    except ImportError as e:
        raise ImportError('Python mesh2hrtf package not installed. Run: pip install -e '+str(mesh2hrtf)) from e
    print('[BEM] Starting CPU solver. Number of frequency points:',
          1+int((fmax-fmin)//stepf),flush=True)
    # Memory-aware parallelization via the original NumCalc manager. Avoid
    # promising a CUDA speedup: Mesh2HRTF/NumCalc is CPU based.
    m2h.manage_numcalc(
        project_path=str(project),numcalc_path=str(binary),
        max_instances=int(args.jobs),wait_time=2,
        max_cpu_load=99,confirm_errors=False)
    step('Convert actual solver pressures to SOFA and plots')
    m2h.output2hrtf(str(project))
    sofa=project/'Output2HRTF'/'HRTF_MicProbe.sofa'
    if not sofa.is_file():
        raise RuntimeError('NumCalc completed, but no HRTF_MicProbe.sofa was produced. Inspect Output2HRTF/report_issues.txt')
    freq,pressure=get_sofa_response(sofa)
    H=reference_transfer(freq,pressure,mic,source,args.units)
    output=project/'analysis'
    peaks=save_analysis(output,freq,H,pressure,mic,source,args.units)
    print('[RESULT] Response: '+str(output/'response.png'),flush=True)
    print('[RESULT] CSV: '+str(output/'response.csv'),flush=True)
    print('[RESULT] Peaks:',flush=True)
    for item in peaks[:10]:
        print(f"  {item['frequency_hz']:8.1f} Hz  {item['gain_db']:+7.1f} dB  "
              f"prominence {item['prominence_db']:.1f} dB  Q~{item['q']:.1f}",flush=True)
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
    print('\nRESULT_DIR='+str(output),flush=True)
    print('RUN_FINISHED',flush=True)
    return 0


def get_args():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mesh2hrtf',default=str(REPO_ROOT))
    ap.add_argument('--stl',default='~/model.stl')
    ap.add_argument('--units',choices=['mm','m'],default='mm')
    ap.add_argument('--mic',nargs=3,type=float,default=[0,0,0],metavar=('X','Y','Z'))
    ap.add_argument('--source-mode',choices=['Near 12 cm','Far 1 m','Custom XYZ'],default='Near 12 cm')
    ap.add_argument('--axis',choices=['+Y','-Y','-X','+X','-Z','+Z'],default='+Y')
    ap.add_argument('--source',nargs=3,type=float,default=[0,120,0],metavar=('X','Y','Z'))
    ap.add_argument('--preset',choices=list(PRESETS),default='Quick ~1kHz')
    ap.add_argument('--results',default='~/EarAcousticLab/results')
    ap.add_argument('--music',default='')
    ap.add_argument('--jobs',type=int,default=2)
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