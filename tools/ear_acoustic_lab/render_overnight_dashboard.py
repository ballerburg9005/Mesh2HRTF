#!/usr/bin/env python3
"""Render saved NumCalc validation results into PNGs + an offline HTML dashboard.

READ ONLY with respect to all solver projects and old results. Runs no
Blender or NumCalc. Dense direct-fallback pressures are shown as explicitly
UNVERIFIED candidates; low-frequency ML requests that NumCalc routed to SL
are reclassified as automatic SL switches, not multilevel failures.
"""
import argparse
from collections import Counter
import datetime as dt
import html
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go
from plotly.offline import plot as plotly_div

from fmm_one_frequency_check import (
    completed_true_residual, gain_db, load_three_node_pressure)

VARIANTS = ("sl8", "ml8", "sl10", "ml10", "dense")
LABELS = {"sl8": "SL-FMM (min 8)", "ml8": "ML-FMM (min 8)",
          "sl10": "SL-FMM (min 10)", "ml10": "ML-FMM (min 10)",
          "dense": "Dense BEM (direct fallback)"}
STATUS_NAMES = {
    "converged": "Verified residual",
    "auto_single": "Auto-switched to SL-FMM",
    "direct_unverified": "Dense direct output, unverified",
    "failed": "Failed/no valid result",
    "missing": "Not available",
}


def _result_from_pressure(source, step, freq, config, params):
    path = source / "be.out" / f"be.{step}" / "pEvalGrid"
    pressure = load_three_node_pressure(path)
    return gain_db(
        pressure, freq, config, params.get("speedOfSound", 343.))


def _actual_method_and_status(source, step, variant, status):
    log = source / f"NC{step}-{step}.out"
    if not log.is_file():
        return None, None, None
    contents = log.read_text(errors="replace")
    actual = next((method for method in (
        "Direct multilevel fast multipole BEM",
        "Single level fast multipole BEM",
        "Traditional BEM",
    ) if method in contents), None)
    if variant.startswith("ml") and status == "failed" and (
            actual == "Single level fast multipole BEM"):
        try:
            _, residual = completed_true_residual(contents)
            return actual, "auto_single", residual
        except ValueError:
            return actual, None, None
    if variant == "dense" and (
            "Iteration method CGS failed, direct method is used!" in contents
            and "End time:" in contents):
        return actual, "direct_unverified", None
    return actual, None, None


def collect(project):
    project = Path(project).expanduser().resolve()
    analysis = project / "analysis"
    cfg = json.loads((project / "earlab_config.json").read_text())
    params = json.loads((project / "parameters.json").read_text())
    freqs = params["frequencies"]
    records = {}
    originals = []
    for result_file in sorted(analysis.glob(
            "overnight_bem_validation_steps_*/results.json")):
        saved = json.loads(result_file.read_text())
        originals.append(str(result_file))
        for key, rec in saved.items():
            try:
                step_text, variant = key.split(":")
                step = int(step_text)
                if variant not in VARIANTS or step < 1 or step > len(freqs):
                    continue
                freq = float(freqs[step - 1])
                status = rec.get("status", "failed")
                row = {
                    "step": step, "frequency_hz": freq,
                    "variant": variant,
                    "source_result": str(result_file),
                    "status": status,
                    "gain_db": rec.get("gain_db")
                        if status == "converged" else None,
                    "phase_deg": rec.get("phase_deg")
                        if status == "converged" else None,
                    "true_relative_residual":
                        rec.get("true_relative_residual")
                        if status == "converged" else None,
                    "reason": rec.get("reason", ""),
                }
                source = (result_file.parent /
                          f"step{step:02d}_{variant}" / "project" /
                          "NumCalc" / "source_1")
                actual, corrected, residual = _actual_method_and_status(
                    source, step, variant, status)
                row["actual_method"] = actual
                if corrected:
                    try:
                        gain, phase = _result_from_pressure(
                            source, step, freq, cfg, params)
                    except (OSError, ValueError, TypeError):
                        pass
                    else:
                        row["status"] = corrected
                        row["gain_db"] = gain
                        row["phase_deg"] = phase
                        row["true_relative_residual"] = residual
                        row["reason"] = (
                            "Actual NumCalc solver selected single-level"
                            if corrected == "auto_single" else
                            "Direct Gaussian fallback has no independently "
                            "verified final residual")
                records[step, variant] = row
            except (KeyError, ValueError, TypeError):
                continue
    if not records:
        raise FileNotFoundError(
            "No overnight results.json found in " + str(analysis))
    rows = sorted(records.values(),
                  key=lambda x: (x["step"], VARIANTS.index(x["variant"])))
    return rows, originals


def _series(rows, variant, status="converged"):
    return sorted(
        [(r["frequency_hz"], r["gain_db"]) for r in rows
         if (r["variant"] == variant and r["status"] == status and
             r["gain_db"] is not None)],
        key=lambda x: x[0])


def _comparison(rows):
    by = {(r["step"], r["variant"]): r for r in rows}
    result = []
    for step in sorted(set(r["step"] for r in rows)):
        sl, ml, dense = (by.get((step, name), {})
                         for name in ("sl8", "ml8", "dense"))
        freq = next(r["frequency_hz"] for r in rows if r["step"] == step)
        result.append({
            "step": step, "frequency_hz": freq,
            "ml_minus_sl_db": (
                ml["gain_db"] - sl["gain_db"]
                if ml.get("status") == sl.get("status") == "converged"
                else None),
            "dense_candidate_minus_sl_db": (
                dense["gain_db"] - sl["gain_db"]
                if dense.get("status") == "direct_unverified"
                and sl.get("status") == "converged" else None),
        })
    return result


def make_pngs(rows, output):
    output.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12.5, 6.5))
    for variant, status, suffix, ls, marker in [
            ("sl8", "converged", "verified residual", "-", "o"),
            ("ml8", "converged", "verified residual", "-", "s"),
            ("dense", "direct_unverified",
             "UNVERIFIED direct fallback", "--", "d")]:
        data = _series(rows, variant, status)
        if data:
            ax.plot([x for x, _ in data], [y for _, y in data],
                    linestyle=ls, marker=marker, markersize=4,
                    label=f"{LABELS[variant]} — {suffix}")
    ax.set(xlabel="Frequency (Hz)", ylabel="Pressure gain vs free field (dB)",
           title="Repaired ear mesh: numerical methods — NOT PHYSICALLY VALIDATED")
    ax.grid(True, alpha=.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(output / "frequency_response.png", dpi=175)
    plt.close(fig)

    comparison = _comparison(rows)
    fig, ax = plt.subplots(figsize=(12.5, 5.3))
    for name, caption, ls, mark in [
            ("ml_minus_sl_db", "ML − SL (both verified operator residuals)",
             "-", "o"),
            ("dense_candidate_minus_sl_db",
             "Dense candidate − SL (direct residual UNVERIFIED)", "--", "d")]:
        data = [(x["frequency_hz"], x[name]) for x in comparison
                if x[name] is not None]
        if data:
            ax.plot([x for x, _ in data], [y for _, y in data],
                    linestyle=ls, marker=mark, markersize=5, label=caption)
    ax.axhline(0, linewidth=1)
    ax.set(xlabel="Frequency (Hz)", ylabel="Difference (dB)",
           title="Method disagreement, not acoustic measurement")
    ax.grid(True, alpha=.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(output / "method_disagreement.png", dpi=175)
    plt.close(fig)

    steps = sorted(set(r["step"] for r in rows))
    lookup = {(r["step"], r["variant"]): r["status"] for r in rows}
    coding = {"missing":0, "failed":1, "auto_single":2,
              "direct_unverified":3, "converged":4}
    values = np.array([[coding[lookup.get((step, v), "missing")]
                        for v in VARIANTS] for step in steps])
    fig, ax = plt.subplots(figsize=(10.5, max(5.6, .38*len(steps)+1.8)))
    im = ax.imshow(values, interpolation="nearest", aspect="auto",
                   vmin=0, vmax=4)
    ax.set_xticks(range(len(VARIANTS)),
                  [LABELS[v] for v in VARIANTS], rotation=20, ha="right")
    ax.set_yticks(range(len(steps)), [str(s) for s in steps])
    ax.set_ylabel("Frequency step")
    ax.set_title("Solver outcomes by frequency and method")
    cb = fig.colorbar(im, ax=ax, ticks=range(5), shrink=.8)
    cb.ax.set_yticklabels([
        "Not available", "Failed", "Auto-selected SL",
        "Direct output unverified", "Verified operator residual"])
    for i in range(len(steps)):
        for j in range(len(VARIANTS)):
            ax.text(j, i, "✓" if values[i,j]==4 else
                    "→" if values[i,j]==2 else
                    "?" if values[i,j]==3 else "×",
                    ha="center", va="center", fontsize=9,
                    color="white" if values[i,j] <= 2 else "black")
    fig.tight_layout()
    fig.savefig(output / "solver_status.png", dpi=170)
    plt.close(fig)
    return comparison


def _interactive_div(rows):
    fig = go.Figure()
    for variant, status, dash in [
            ("sl8", "converged", "solid"),
            ("ml8", "converged", "solid"),
            ("dense", "direct_unverified", "dash")]:
        data = _series(rows, variant, status)
        if not data:
            continue
        freq, db = zip(*data)
        label = LABELS[variant]
        if status != "converged":
            label += " — UNVERIFIED"
        fig.add_trace(go.Scatter(
            x=freq, y=db, name=label,
            mode="lines+markers", line={"dash": dash},
            hovertemplate="<b>%{x:.0f} Hz</b><br>%{y:+.3f} dB<extra>%{fullData.name}</extra>"))
    fig.update_layout(
        title="Numerical pressure response — NOT physical validation",
        xaxis_title="Frequency (Hz)", yaxis_title="Gain vs free field (dB)",
        hovermode="x unified", template="plotly_white",
        height=510, margin=dict(l=50,r=25,t=65,b=55),
        legend=dict(orientation="h",y=-.25))
    return plotly_div(fig, include_plotlyjs=True, output_type="div")


def render(project, output=None):
    project = Path(project).expanduser().resolve()
    rows, sources = collect(project)
    if output is None:
        output = project / "analysis" / "visual_overview"
    output = Path(output).expanduser().resolve()
    comparison = make_pngs(rows, output)
    stats = Counter(r["status"] for r in rows)
    by = {(r["step"], r["variant"]):r for r in rows}
    tr = []
    for line in comparison:
        cells = [f"{line['frequency_hz']:.0f}", str(line["step"])]
        for name in VARIANTS:
            rec = by.get((line["step"], name), {})
            status = rec.get("status", "missing")
            value = rec.get("gain_db")
            label = (f"{value:+.2f} dB" if value is not None else "—")
            if status == "direct_unverified":
                label += " (unverified)"
            elif status == "auto_single":
                label += " (auto SL)"
            elif status == "failed":
                label = "failed"
            cells.append(label)
        tr.append("<tr>"+ "".join(
            f"<td>{html.escape(c)}</td>" for c in cells)+"</tr>")
    div = _interactive_div(rows)
    counts = " · ".join(
        f"{stats[status]} {STATUS_NAMES[status].lower()}"
        for status in ("converged","auto_single","direct_unverified","failed")
        if stats[status])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mesh2HRTF · Overnight Numerical Overview</title>
<style>
:root{{color-scheme:light; font-family:system-ui,-apple-system,sans-serif}}
body{{max-width:1250px;margin:auto;padding:24px 18px;color:#1c242d;background:#f5f7f9}}
h1{{margin-bottom:8px}}p{{line-height:1.5}}.muted{{color:#606e7a}}
main{{background:#fff;border:1px solid #e3e8ed;border-radius:14px;
padding:16px;margin:18px 0}}img{{max-width:100%;height:auto}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;
font-size:13px}}th,td{{padding:9px 8px;text-align:right;
border-bottom:1px solid #e7ebf0;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}
.table-wrapper{{overflow-x:auto}}.warning{{font-weight:650}}
a{{color:#176a9a}}
</style></head>
<body><h1>Overnight BEM · Visual Overview</h1>
<p class="muted">Source: {html.escape(project.name)} · Generated
{html.escape(dt.datetime.now().strftime("%Y-%m-%d %H:%M"))} ·
{len(set(r['step'] for r in rows))} frequencies</p>
<p><strong>{html.escape(counts)}</strong></p>
<p class="warning">These are numerical outputs, not a validated HRTF. Dense
BEM's final direct-solver residual was not checked. Lower-frequency
multilevel requests automatically became single-level calculations.</p>
<main>{div}</main>
<main><h2>Method differences</h2>
<img src="method_disagreement.png" alt="Plot of FMM difference and
unverified dense candidate minus single-level FMM"></main>
<main><h2>Solver status</h2>
<img src="solver_status.png" alt="Matrix showing converged, failed,
auto-selected single-level, and unverified dense cases"></main>
<main><h2>All numerical results</h2><div class="table-wrapper">
<table><thead><tr><th>Hz</th><th>Step</th>
{''.join('<th>'+html.escape(LABELS[n])+'</th>' for n in VARIANTS)}
</tr></thead><tbody>{''.join(tr)}</tbody></table>
</div><p class="muted">A convergence residual applies only to its own
discrete operator; different FMM approximations can both converge but
disagree. The wavelength/6 check does not establish mesh convergence.</p>
</main>
<p class="muted">Offline HTML; hover and zoom the main interactive chart.
Other PNG files can be opened individually:
<a href="frequency_response.png">frequency_response.png</a> ·
<a href="method_disagreement.png">method_disagreement.png</a> ·
<a href="solver_status.png">solver_status.png</a>.</p>
</body></html>"""
    index = output / "index.html"
    index.write_text(page)
    (output / "chart_data.json").write_text(json.dumps({
        "project":str(project),"source_files":sources,
        "outcomes":dict(stats),"rows":rows,"comparison":comparison,
        "caveat":"Dense direct pressure is unverified without final residual"
    }, indent=2)+"\n")
    print(f"[VISUAL] {len(rows)} cases at "
          f"{len(set(r['step'] for r in rows))} frequencies", flush=True)
    print("[VISUAL] " + counts, flush=True)
    for name in ("index.html","frequency_response.png",
                 "method_disagreement.png","solver_status.png",
                 "chart_data.json"):
        print("[VISUAL] " + str(output/name), flush=True)
    print("[VISUAL] No NumCalc or Blender was run.", flush=True)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project",required=True)
    parser.add_argument("--output",default=None)
    args = parser.parse_args()
    render(args.project,args.output)


if __name__=="__main__":
    main()
