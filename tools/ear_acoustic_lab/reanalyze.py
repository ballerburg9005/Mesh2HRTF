#!/usr/bin/env python3
"""Build an intelligible report from an EXISTING completed NumCalc run.

No new acoustic simulations are launched. Reads the saved microphone SOFA,
and, when present, the boundary pressures NumCalc already calculated.
"""
import csv
import html
import json
from pathlib import Path
import sys
import traceback

import numpy as np

from analysis_tools import get_sofa_response, reference_transfer

SPEED_OF_SOUND = 343.0


def _safe_db(values):
    return 20 * np.log10(np.maximum(np.abs(values), 1e-15))


def _select_spatial_frequencies(freq, color_db):
    """At most 3 distinctive frequencies already contained in the run."""
    available = np.arange(len(freq), dtype=int)
    if not len(available):
        return []
    # Beginning, strongest sampled tonal departure, and end.
    picks = [0, int(np.argmax(np.abs(color_db))), len(freq) - 1]
    return list(dict.fromkeys(picks))


def _surface_maps(project, output, freq, color_db):
    """Map *computed boundary pressure*; never interpolate unsolved tones."""
    try:
        from mesh2hrtf.Output2HRTF.output2hrtf import _read_numcalc_data
        from surface_viewer import mesh_views, write_interactive_surface, write_vtk_surfaces
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import Normalize
        from matplotlib.cm import ScalarMappable
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        base = project / "ObjectMeshes" / "Reference"
        if not (base / "Nodes.txt").is_file():
            return None, [], "No exported Reference surface mesh"
        coords = np.loadtxt(base / "Nodes.txt", skiprows=1)
        elements = np.loadtxt(base / "Elements.txt", skiprows=1)
        coords = np.atleast_2d(coords)
        elements = np.atleast_2d(elements)
        if coords.shape[1] < 4 or elements.shape[1] < 7:
            return None, [], "Unsupported Reference mesh file shape"
        ids = coords[:, 0].astype(int)
        lut = {int(x): idx for idx, x in enumerate(ids)}
        face_ids = elements[:, 1:4].astype(int)
        faces = np.array([[lut[int(n)] for n in tri] for tri in face_ids], dtype=int)
        vertices_mm = coords[:, 1:4] * 1000.0
        faces_mm = vertices_mm[faces]
        pressure, _ = _read_numcalc_data(1, len(freq), str(project), "pBoundary")
        if pressure.shape != (len(faces), 1, len(freq)):
            return None, [], "Boundary pressure shape does not match mesh: " + str(pressure.shape)

        indices = _select_spatial_frequencies(freq, color_db)
        fig = plt.figure(figsize=(5.1 * len(indices), 5.5), dpi=120)
        cmap = plt.get_cmap("coolwarm")
        surface_values = []
        for index in indices:
            raw = _safe_db(pressure[:, 0, index])
            surface_values.append(raw - np.median(raw))
        all_values = np.concatenate(surface_values)
        limit = float(np.clip(np.percentile(np.abs(all_values), 92), 3, 30))
        norm = Normalize(vmin=-limit, vmax=limit, clip=True)

        center = (vertices_mm.min(axis=0) + vertices_mm.max(axis=0)) / 2
        halfwidth = max(float(np.max(np.ptp(vertices_mm, axis=0))) * .55, 1.)
        for col, (index, values) in enumerate(zip(indices, surface_values)):
            ax = fig.add_subplot(1, len(indices), col + 1, projection="3d")
            polys = Poly3DCollection(faces_mm, linewidths=0, antialiased=False)
            polys.set_facecolor(cmap(norm(values)))
            ax.add_collection3d(polys)
            ax.set_xlim(center[0] - halfwidth, center[0] + halfwidth)
            ax.set_ylim(center[1] - halfwidth, center[1] + halfwidth)
            ax.set_zlim(center[2] - halfwidth, center[2] + halfwidth)
            ax.set_box_aspect((1, 1, 1))
            ax.set_axis_off()
            ax.view_init(elev=20, azim=90)
            ax.set_title(f"{freq[index]:.0f} Hz — boundary pressure", fontsize=11)
        fig.subplots_adjust(left=.03, right=.88, bottom=.05, top=.91, wspace=.02)
        cax = fig.add_axes([.90, .20, .014, .58])
        fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=cax,
                     label="dB relative to surface median at each frequency")
        outpath = output / "surface_pressure.png"
        fig.savefig(outpath, dpi=140)
        plt.close(fig)

        selected_pressure = np.take(pressure[:, 0, :], indices, axis=1)
        selected_frequencies = np.asarray(freq)[indices]
        np.savez_compressed(output / "surface_samples.npz",
                            vertices_mm=vertices_mm, faces=faces,
                            frequency_hz=selected_frequencies,
                            pressure_complex=selected_pressure)
        vtk_paths = []
        for index in indices:
            # ParaView-compatible 3D scalar field, using Mesh2HRTF's own exporter.
            try:
                m2h.export_vtk(str(project), object="Reference", mode="pressure",
                               frequency_steps=[index + 1, index + 1], dB=True)
                vtk_paths.append(str(project / "Output2HRTF" / "vtk" /
                                     "Reference_pressure_db" /
                                     f"frequency_step_{index + 1}.vtk"))
            except Exception as exc:
                return outpath, vtk_paths, f"VTK export warning: {exc}"
        return outpath, vtk_paths, None
    except Exception as exc:
        return None, [], f"Boundary maps unavailable: {exc}"


def render_project(path):
    """Return report file path; only reads stored numerical outputs."""
    project = Path(path).expanduser().resolve()
    if project.name == "analysis" and (project.parent / "earlab_config.json").is_file():
        project = project.parent
    cfg_file = project / "earlab_config.json"
    sofa = project / "Output2HRTF" / "HRTF_MicProbe.sofa"
    if not cfg_file.is_file() or not sofa.is_file():
        raise FileNotFoundError(f"Not a completed Ear Acoustic Lab project: {project}")

    cfg = json.loads(cfg_file.read_text())
    freq, pressure = get_sofa_response(sofa)
    freq = np.asarray(freq, float)
    if len(freq) < 2 or not np.all(np.diff(freq) > 0):
        raise ValueError("Stored frequencies are missing, unordered or too sparse")
    h = reference_transfer(freq, pressure, cfg["mic"], cfg["source"], cfg["units"])
    if not np.all(np.isfinite(h)):
        raise ValueError("Stored response has invalid values")

    output = project / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    gain = _safe_db(h)
    median = float(np.median(gain))
    color_db = gain - median  # Show *shape* independently of overall pressure scale
    phase = np.unwrap(np.angle(h))
    delay_ms = -np.gradient(phase, 2 * np.pi * freq) * 1000
    df = np.diff(freq)
    step = float(np.median(df))
    span = float(gain.max() - gain.min())
    # Report measured sampled local extrema, not invented sub-bin resonance peaks.
    local_high = [i for i in range(1, len(freq)-1)
                  if gain[i] >= gain[i-1] and gain[i] > gain[i+1]]
    local_low = [i for i in range(1, len(freq)-1)
                 if gain[i] <= gain[i-1] and gain[i] < gain[i+1]]
    high = sorted(local_high, key=lambda i: -gain[i])[:6]
    low = sorted(local_low, key=lambda i: gain[i])[:6]
    highest = list(np.argsort(gain)[-3:][::-1])
    lowest = list(np.argsort(gain)[:3])
    warnings = [
        "This is a linear frequency-domain BEM result, not measured microphone distortion.",
        "Only the listed frequency points were calculated; peaks between points may be completely absent from this report.",
        "A constant positive gain does not establish calibrated SPL, perceptual coloration or a valid canal boundary model.",
        "The point source is not a headphone diaphragm or headphone-pad acoustic boundary.",
        "The microphone is an ideal non-loading pressure probe. Capsule, termination and viscothermal losses are not modeled.",
        "The fluid domain and microphone-to-opening air connection have not been independently validated.",
    ]
    if step >= 50:
        warnings.insert(0, f"COARSE SWEEP: {step:g} Hz spacing. Narrow resonances and ringing cannot be resolved.")
    if freq.min() > 100 or freq.max() < 15000:
        warnings.insert(0, f"PARTIAL SPECTRUM: {freq.min():g}–{freq.max():g} Hz only. Not a full audio-band EQ profile.")
    if not len(high):
        warnings.append("No sampled local maxima were found. This does not imply an absence of physical resonances.")

    with (output / "diagnostic_samples.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frequency_hz", "gain_relative_free_field_db",
                         "coloration_relative_to_band_median_db",
                         "phase_unwrapped_degrees", "group_delay_ms",
                         "magnitude_ratio_linear"])
        for i in range(len(freq)):
            writer.writerow([float(freq[i]), float(gain[i]), float(color_db[i]),
                             float(np.degrees(phase[i])), float(delay_ms[i]),
                             float(np.abs(h[i]))])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(2, 2, figsize=(14, 9), dpi=130)
    axs[0, 0].plot(freq, gain, marker="o", markersize=3, lw=1.5)
    axs[0, 0].axhline(0, c="#888", linestyle=":")
    axs[0, 0].set_title("Pressure gain relative to the unobstructed point source")
    axs[0, 0].set_ylabel("dB (NOT calibrated SPL)")
    axs[0, 1].plot(freq, color_db, marker="o", markersize=3, lw=1.5)
    axs[0, 1].axhline(0, c="#888", linestyle=":")
    axs[0, 1].set_title(f"Spectral coloration (subtract {median:+.1f} dB band median)")
    axs[0, 1].set_ylabel("dB relative to this sweep's median")
    axs[1, 0].plot(freq, np.degrees(phase), marker="o", markersize=2.5, lw=1.4)
    axs[1, 0].set_title("Phase relative to the unobstructed point source")
    axs[1, 0].set_ylabel("Unwrapped degrees")
    axs[1, 1].plot(freq, delay_ms, marker="o", markersize=2.5, lw=1.4)
    axs[1, 1].set_title("Phase-derived group delay (coarse: approximate only)")
    axs[1, 1].set_ylabel("ms")
    for ax in axs.flat:
        ax.set_xlabel("Frequency (Hz)")
        ax.grid(alpha=.25)
        if np.max(freq) / np.min(freq) > 12:
            ax.set_xscale("log")
    fig.suptitle(
        f"Mesh2HRTF microphone response · {len(freq)} solved points · "
        f"{freq.min():g}–{freq.max():g} Hz · median step {step:g} Hz",
        fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, .95])
    graph = output / "response.png"
    fig.savefig(graph, dpi=145)
    plt.close(fig)

    def point_rows(indices, label):
        return [
            {"kind": label, "frequency_hz": float(freq[i]),
             "relative_gain_db": float(gain[i]),
             "coloration_db": float(color_db[i])}
            for i in indices
        ]

    extrema = point_rows(high, "sampled local high") + point_rows(low, "sampled local low")
    rankings = point_rows(highest, "highest sampled point") + point_rows(lowest, "lowest sampled point")
    surface, vtk, field_warning = _surface_maps(project, output, freq, color_db)
    if field_warning:
        warnings.append(field_warning)
    summary = {
        "project": str(project), "samples": int(len(freq)),
        "frequency_min_hz": float(freq.min()), "frequency_max_hz": float(freq.max()),
        "frequency_step_median_hz": step,
        "gain_median_db_relative_to_freefield": median,
        "gain_min_db": float(gain.min()), "gain_max_db": float(gain.max()),
        "observed_span_db": span,
        "sampled_extrema": extrema, "ranked_points": rankings,
        "warnings": warnings, "surface_map": str(surface) if surface else None,
        "boundary_vtk_files": vtk
    }
    (output / "diagnostics.json").write_text(json.dumps(summary, indent=2))

    def fmt_rows(rows):
        return "".join(
            f"<tr><td>{html.escape(p['kind'])}</td>"
            f"<td>{p['frequency_hz']:.0f}</td>"
            f"<td>{p['relative_gain_db']:+.2f}</td>"
            f"<td>{p['coloration_db']:+.2f}</td></tr>"
            for p in rows
        )

    bullets = "".join(f"<li>{html.escape(w)}</li>" for w in warnings)
    image = ('<h2>Pressure distribution on the simulated surface</h2>'
             '<p>This is boundary pressure on the mesh, NOT an interior cross-section. '
             'Each frequency is color-normalized to its own surface median. '
             'Inspect exported VTK in ParaView for a rotatable 3D field.</p>'
             '<img src="surface_pressure.png" alt="Boundary pressure maps">') if surface else ""
    dashboard = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ear Acoustic Lab – completed simulation report</title><style>
body{{font:16px/1.55 system-ui,sans-serif;max-width:1170px;margin:24px auto;padding:0 18px;color:#20252a;background:#fafafa}}
h1,h2{{line-height:1.2}} img{{display:block;width:100%;height:auto;margin:16px 0;border:1px solid #ddd;background:white}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.card{{padding:12px 16px;border:1px solid #ddd;border-radius:8px;background:#fff}}
.card strong{{display:block;font-size:25px}} table{{width:100%;border-collapse:collapse;background:white}}
th,td{{padding:8px 10px;border-bottom:1px solid #e5e5e5;text-align:left}}
.warning{{border-left:4px solid #9b6825;background:#fff9ed;padding:8px 18px}}
small{{color:#555}}a{{color:#075f96}}
</style></head><body>
<h1>Ear Acoustic Lab — frequency response report</h1>
<p>Saved simulation: <code>{html.escape(project.name)}</code>. All values below
come from the original NumCalc run; reanalysis did not solve another frequency.</p>
<div class="cards"><div class="card">Solved tones<strong>{len(freq)}</strong></div>
<div class="card">Frequency range<strong>{freq.min():g}–{freq.max():g} Hz</strong></div>
<div class="card">Median spacing<strong>{step:g} Hz</strong></div>
<div class="card">Measured band variation<strong>{span:.2f} dB</strong></div>
<div class="card">Median free-field gain<strong>{median:+.2f} dB</strong></div></div>
<h2>Microphone transfer function</h2>
<p>Upper-left: simulated mic pressure divided by the analytical unoccluded
point-source pressure. Upper-right: exactly the same values with the band
median subtracted to reveal the <b>frequency dependence</b>; it is not an EQ target.
Lower panels: phase and group delay, reliable only at adequate spacing.</p>
<img src="response.png" alt="Gain, spectral coloration, phase and delay">
<h2>Where are the strongest observed changes?</h2>
<p>These are <b>sampled values</b>, not accurately resolved resonance frequencies.
A narrow resonance between sampled tones will not appear here.</p>
<table><thead><tr><th>Type</th><th>Hz</th><th>Gain vs free field (dB)</th>
<th>Relative to this sweep's median (dB)</th></tr></thead><tbody>
{fmt_rows(extrema or rankings)}</tbody></table>
{image}
<h2>Interpretation limits</h2><div class="warning"><ul>{bullets}</ul></div>
<p><a href="diagnostic_samples.csv">Frequency table (CSV)</a> ·
<a href="diagnostics.json">Full numeric summary (JSON)</a> ·
<a href="pressure.npz">Saved complex microphone pressure (NPZ)</a> ·
<a href="../Output2HRTF/HRTF_MicProbe.sofa">Original SOFA data</a></p>
<small>Sources: NumCalc / Mesh2HRTF model output. No measured recording was
used, no mic loading or distortion was solved.</small></body></html>"""
    report = output / "report.html"
    report.write_text(dashboard)
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="Existing ear_<timestamp> project OR analysis directory")
    args = parser.parse_args()
    print("[REANALYZE] Reading previous BEM calculation — no solver run", flush=True)
    report = render_project(args.project)
    print("[RESULT] Detailed report:", report, flush=True)
    print("RESULT_DIR=" + str(report.parent), flush=True)
    print("RUN_FINISHED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("RUN_FAILED", flush=True)
        sys.exit(1)
