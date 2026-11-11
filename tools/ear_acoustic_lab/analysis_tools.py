"""Actual Mesh2HRTF pressure-response diagnostics and offline music audition."""
import csv
import json
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks, peak_widths
from spectral_diagnostics import inspect_spectrum


def mic_grid(folder, xyz, units):
    """Three closely spaced air-pressure probes, one tiny triangle.

    Mesh2HRTF evaluation grids require elements, not isolated nodes.
    The triangle's centroid is the requested microphone coordinate.
    Node IDs must not overlap the STL mesh IDs.
    """
    p = Path(folder)
    p.mkdir(parents=True, exist_ok=False)
    mul = .001 if units == 'mm' else 1.0
    c = np.asarray(xyz, dtype=float) * mul
    delta = 0.0001  # 0.1 mm radius from requested mic center
    pts = [c + [-delta, -delta/3, 0],
           c + [delta, -delta/3, 0],
           c + [0, 2*delta/3, 0]]
    with (p/'Nodes.txt').open('w') as f:
        f.write('3\n')
        for i, node in enumerate(pts):
            f.write(f'{10000000+i} {node[0]:.12f} {node[1]:.12f} {node[2]:.12f}\n')
    (p/'Elements.txt').write_text('1\n10000000 10000000 10000001 10000002 2 0 1\n')
    return p


def get_sofa_response(path):
    import sofar as sf
    obj = sf.read_sofa(str(path))
    freq = np.asarray(obj.N, dtype=float).reshape(-1)
    p = np.asarray(obj.Data_Real) + 1j * np.asarray(obj.Data_Imag)
    if p.ndim != 3 or p.shape[-1] != len(freq):
        raise ValueError('Unexpected SOFA pressure dimensions: ' + str(p.shape))
    # One source, three extremely close evaluation nodes; average pressure
    if p.shape[0] != 3 or p.shape[1] != 1:
        raise ValueError('Expected 3 probe nodes and 1 source, got ' + str(p.shape))
    response = np.mean(p[:, 0, :], axis=0)
    return freq, response


def probe_spread(path):
    """Complex-pressure disagreement among the 3 nominally co-located probes.

    This is a consistency diagnostic, not a correction to the acoustic field.
    The probes are separated by only fractions of a millimetre.
    """
    import sofar as sf
    obj = sf.read_sofa(str(path))
    freq = np.asarray(obj.N, dtype=float).reshape(-1)
    node_pressures = np.asarray(obj.Data_Real) + 1j*np.asarray(obj.Data_Imag)
    if node_pressures.shape != (3, 1, len(freq)):
        raise ValueError("Unexpected SOFA probe shape: " + str(node_pressures.shape))
    nodes = node_pressures[:, 0, :]
    mean = np.mean(nodes, axis=0)
    rms = np.sqrt(np.mean(np.abs(nodes)**2, axis=0))
    spread = np.max(np.abs(nodes - mean), axis=0) / np.maximum(rms, 1e-15)
    return freq, spread


def reference_transfer(freq, pressure, mic, source, units, speed=343.):
    mic = np.asarray(mic, float)
    source = np.asarray(source, float)
    r = float(np.linalg.norm(mic - source)) * (.001 if units == 'mm' else 1.)
    if r < .002:
        raise ValueError('Source is too close to microphone')
    # Mesh2Input hard-codes point source amplitude 0.1; identical to
    # Mesh2HRTF reference_hrtfs.py. Our reference uses TRUE source-to-mic
    # distance rather than distance from the global mesh origin.
    free = .1 * np.exp(1j * 2 * np.pi * freq * r / speed) / (4*np.pi*r)
    return pressure / free


def find_resonances(f, h, threshold_db=3.):
    db = 20*np.log10(np.maximum(abs(h), 1e-14))
    if len(f)<5: return []
    flags = set(inspect_spectrum(f, db)["suspect_indices"])
    inds, meta = find_peaks(db, prominence=threshold_db, distance=2)
    if not len(inds): return []
    widths = peak_widths(db, inds, rel_height=.5)
    df = float(np.median(np.diff(f)))
    peaks = []
    for j, ind in enumerate(inds):
        bw = float(widths[0][j]*df)
        peaks.append(dict(frequency_hz=float(f[ind]), gain_db=float(db[ind]),
                          prominence_db=float(meta['prominences'][j]),
                          bandwidth_hz=bw, q=float(f[ind]/bw) if bw>0 else 0.,
                          unverified_spectrum=bool(ind in flags)))
    return sorted(peaks,key=lambda p:p['prominence_db'],reverse=True)


def save_analysis(output, f, h, pressure, mic, source, units):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    p = Path(output);p.mkdir(parents=True,exist_ok=True)
    h=np.asarray(h,dtype=np.complex128)
    if not (np.all(np.isfinite(h)) and np.all(np.isfinite(f))):
        raise ValueError('Solver output has invalid or missing frequency samples')
    db = 20*np.log10(np.maximum(abs(h),1e-14))
    phase = np.unwrap(np.angle(h))
    delay = -np.gradient(phase, np.asarray(f)*2*np.pi)*1000 if len(f)>1 else np.zeros_like(f)
    peaks=find_resonances(f,h)
    with (p/'response.csv').open('w',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['frequency_hz','gain_vs_freefield_db','phase_degrees_unwrapped',
                         'group_delay_ms','pressure_real_pa_relative','pressure_imag_pa_relative'])
        for i in range(len(f)):
            writer.writerow([f[i],db[i],np.degrees(phase[i]),delay[i],
                             pressure[i].real,pressure[i].imag])
    (p/'peaks.json').write_text(json.dumps(peaks,indent=2))
    np.savez_compressed(p/'pressure.npz',freq_hz=f,pressure=pressure,H=h)
    fig,axs=plt.subplots(2,1,figsize=(11,8),sharex=True)
    axs[0].plot(f,db,linewidth=1.4)
    axs[0].axhline(0,color='grey',ls=':',lw=1)
    for item in peaks[:8]:
        x=item['frequency_hz'];y=item['gain_db']
        axs[0].plot([x],[y],'o',markersize=3.5)
        axs[0].annotate(f'{x:.0f} Hz', (x,y),xytext=(5,6),textcoords='offset points',fontsize=8)
    axs[0].set_ylabel('dB relative to free-field source');axs[0].grid(True,alpha=.25)
    axs[0].set_title('Predicted ear + canal + tube pressure at the microphone\n'
                     'Linear BEM, rigid wall model — not measured distortion')
    axs[1].plot(f,delay,lw=1)
    axs[1].set_ylabel('Relative group delay (ms)');axs[1].set_xlabel('Frequency (Hz)')
    axs[1].grid(True,alpha=.25)
    fig.tight_layout();fig.savefig(p/'response.png',dpi=160);plt.close(fig)
    return peaks


def audition_music(audio_path, freq, h, outfile, max_seconds=30):
    """Interpolated frequency-domain auralization, NOT new solver output.

    Needs a sufficiently dense frequency sweep for a useful audition.
    Frequency gaps or response outside the sweep are approximated.
    """
    import soundfile as sf
    from scipy.signal import resample_poly
    import subprocess
    import shutil
    audio_path=Path(audio_path).expanduser()
    if audio_path.suffix.lower() in ('.mp3','.m4a','.aac','.ogg'):
        exe=shutil.which('ffmpeg')
        if not exe:raise RuntimeError('For MP3 audition install ffmpeg, or use WAV')
        data=subprocess.run([exe,'-v','error','-i',str(audio_path),'-t',str(max_seconds),
                             '-f','f32le','-acodec','pcm_f32le','-ac','2','-ar','48000','-'],
                             check=True,stdout=subprocess.PIPE).stdout
        samples=np.frombuffer(data,dtype='<f4').reshape(-1,2).astype(float)
        rate=48000
    else:
        samples,rate=sf.read(str(audio_path),always_2d=True,dtype='float64',
                             stop=None)
        samples=samples[:int(max_seconds*rate),:]
    if len(samples)==0:raise ValueError('Input audio is empty')
    if len(samples)>48000*max_seconds*4:
        raise ValueError('Audio file unexpectedly large')
    if rate!=48000:
        from math import gcd
        div=gcd(rate,48000)
        samples=resample_poly(samples,48000//div,rate//div,axis=0)
        rate=48000
    n=len(samples)
    xf=np.fft.rfftfreq(n,1/rate)
    quality = inspect_spectrum(freq, 20*np.log10(np.maximum(np.abs(h), 1e-14)))
    if quality["suspect_point_count"]:
        raise ValueError("Refusing to audition unverified frequency response: "
                         f"{quality['suspect_point_count']} suspect samples. "
                         "Resolve high-frequency solver/mesh anomalies first.")
    amp=np.abs(h);phi=np.unwrap(np.angle(h))
    amp_interp=np.interp(xf,freq,amp,left=amp[0],right=1.)
    phase_interp=np.interp(xf,freq,phi,left=phi[0],right=0.)
    # Outside the calculated band, pass the input unmodified, avoiding
    # extrapolating potentially enormous narrow resonances.
    spec=amp_interp*np.exp(1j*phase_interp)
    spec[0]=abs(spec[0])
    if n%2==0:spec[-1]=abs(spec[-1])
    y=np.fft.irfft(np.fft.rfft(samples,axis=0)*spec[:,None],n=n,axis=0)
    peak=np.max(np.abs(y))
    if peak>.90:y*=.90/peak
    sf.write(str(outfile),y,rate,subtype='PCM_16')
    return dict(duration_sec=n/rate,peak=float(np.max(np.abs(y))),note='Interpolated single-ear transfer applied to source audio')