# Ear Acoustic Lab — one-button Mesh2HRTF resonance analysis

This GUI lives **inside your `Mesh2HRTF` fork**, under `tools/ear_acoustic_lab`. It uses the upstream **NumCalc 3D BEM wave solver** and the Blender exporter internally. You **do not need to manually install or enable the Blender add-on**: `blender_export.py` loads and registers the add-on for each headless export. Blender is still required as an automatic STL-to-NumCalc conversion dependency.

## Arch Linux: install and run

From your existing checkout, switch to the feature branch (or fetch it from your fork if `origin` points to upstream):

```sh
cd ~/Mesh2HRTF
git fetch origin
git switch --track origin/feature/ear-acoustic-lab
sudo pacman -S --needed blender python python-pip tk base-devel ffmpeg libsndfile
bash tools/ear_acoustic_lab/install_and_run.sh
```

If you already have the branch locally, use `git switch feature/ear-acoustic-lab` and `git pull --ff-only`. The launch script creates `.venv` just for this tool, installs Python dependencies, compiles NumCalc if needed, then opens the GUI. The **first** run downloads Python packages; later runs reuse them. Mesh2HRTF itself uses CPU cores, not CUDA.

If your clone's `origin` is `Any2HRTF/Mesh2HRTF` rather than your fork, first run `git remote add mine https://github.com/ballerburg9005/Mesh2HRTF.git`, then `git fetch mine` and `git switch --track mine/feature/ear-acoustic-lab`.

## Physical RAM / swap safety

For desktop machines with 32 GiB of RAM the previous **four workers, two-second
launch stagger and no fixed memory budget** could cause severe swap traffic.
The default remains **one worker** (two maximum), with a *system-wide*
physical-memory cap leaving at least 2.5 GiB or 8% of installed RAM available
for other work. The former 35%/8 GiB reserve deadlocked a nearly finished
80-frequency run despite 13 GiB of Linux-reported available physical RAM.
NumCalc's per-frequency memory estimate remains multiplied by two, and process
starts remain staggered by 30 seconds. The manager now applies that same safety
factor when **selecting** a job, preventing accidental memory overcommit.
If there are no NumCalc workers and even the smallest remaining job cannot
fit for two minutes, it exits with an actionable error rather than waiting
forever. The same scheduling rules apply to resumed runs.
No swap capacity is counted as free physical RAM. Linux's `Swap: used`
number alone is not proof of ongoing disk activity. This cannot guarantee
zero swapping if other applications begin consuming memory later.
Avoid running two independent managers at once.

If an older manager is currently running, stop it and its NumCalc workers before
pulling these changes. Keep the existing `earlab_runs/ear_*` folder: completed
steps may be reused with `--resume ... --jobs 1`. Do not resume while another
manager is active. Both new and resumed runs now check `make` for stale NumCalc
binaries so C++ solver patches are not silently ignored.

## Blender fast-sweep export compatibility

Fast 20/24 kHz modes now ask Blender Mesh2Input for **80 frequency steps**
rather than passing a 250 or 300 Hz placeholder step that does not divide
the selected interval. The exported project is then rewritten to the
endpoint-exact 80-tone grid before NumCalc runs. All fixed-step presets are
validated before Blender launches. This prevents the
`frequencyStepSize is not a divisor of maxFrequency-minFrequency` error.
Use a **new run** after pulling this fix; the failed Blender export did not
create any NumCalc frequency results to recover.

## Fail-fast solver scheduling

Ear Acoustic Lab now verifies **each frequency's actual completion** before
starting another. If NumCalc exits unsuccessfully, writes no microphone
pressure, or does not finish its log, the scan **stops immediately** instead
of wasting hours launching the next dozens of frequencies. The saved run
folder and already completed frequency files are preserved. This strict
mode currently executes workers sequentially even if `--jobs 2` is selected;
use `--jobs 1` to reflect the effective concurrency. Other callers of
`mesh2hrtf.manage_numcalc` retain the original asynchronous behavior
unless they opt into `stop_on_failed_step=True`.

## Diagnose 79 started but only 15 completed frequency steps

The `N/M starting instance` counter counts **launched** frequencies, not
completed ones. A residual in `NCn-n.out` proves only that the solver reached
its iteration reporting; NumCalc writes the microphone `pEvalGrid` file
separately, and writes `End time:` after post-processing.

Inspect the actual saved run **without starting any workers**:

```sh
tools/ear_acoustic_lab/.venv/bin/python tools/ear_acoustic_lab/run_analysis.py \
  --inspect-resume ./earlab_runs/ear_20261110_234807_961528
```

This saves `analysis/resume_audit.json` with one status per frequency:
`completed`, `solver_rejected`, `interrupted_or_failed`,
`partial_pressure`, `postprocessing_missing`, or `not_started`.
Completion requires a **nonempty** `be.out/be.N/pEvalGrid` and an
`End time:` marker in the matching NumCalc log. A partial output is not
treated as success. The audit does not change solver results or prove
acoustic/mesh convergence.

`--resume` now prints this audit automatically. If the logs explicitly say
CGS and GMRES both failed, the tool refuses to rerun those known failed
frequencies in an unchanged project unless `--retry-failed` is passed.
A clear failure is not silently counted as a valid pressure response.
Interrupted steps remain eligible for retry; already completed steps
remain intact. Do not start a second manager while one is still running.

## One-button test

1. Save the physical **air-facing** 3D ear geometry as `~/model.stl`. If possible, **move the STL's coordinate origin to the microphone** in Blender before exporting. Then the GUI's default `Mic XYZ = 0 0 0` is correct; no other microphone setup required. Coordinates must use the same units as the STL, default **millimetres**. You can also specify a different microphone XYZ if you prefer. The STL is imported automatically, not via the Blender GUI.
2. Leave `Sound source` on **Near 12 cm**, `axis +Y` (toward your ear opening) unless your model uses another orientation. Check **Preview STL + mic** to verify the red mic position and orange source position relative to the geometry. The **Far 1 m** analytical point source does not itself increase the number of BEM surface elements. (For accurate physical geometry, the mic must lie in the connected air region; being at the origin alone does not make it correct.)
3. Choose **20 kHz fast (80 tones)** (new default) to survey 100 Hz–20 kHz at 80 approximately 252 Hz-spaced frequencies, including both endpoints, then press **RUN ACOUSTIC SWEEP**. Choose **24 kHz fast (80 tones)** to extend the endpoint to 24 kHz. For finer broadband screening, use the 100 Hz overview modes (200 points through 20 kHz or 240 through 24 kHz). The fast sweeps are screening tools and can miss narrow resonances. NumCalc runs selected frequency-domain acoustic solves and saves `response.png`, `response.csv`, `peaks.json` and the complete solver project to `<your-clone>/earlab_runs/ear_<timestamp>/`. Use **Open graphs** to see the response and prominent resonance peaks.
4. For a more precise sweep around a suspicious tone select **Focused ~1kHz** (10 Hz spacing) or **Deep 1kHz** (5 Hz). Use **Full spectrum** for 100 Hz–20 kHz at 100 Hz steps, or **Detailed spectrum** for 25 Hz steps. These presets can take much longer with detailed meshes.
5. Optionally select a WAV/MP3 music file before running; **Play music audition** applies the simulated *linear complex frequency response* to up to 30 s of the input, creating `music_audition.wav`. This is offline auralization, **not** nonlinear mic distortion or actual music being injected into the frequency-domain solver. Sparse sweeps may be inaccurate for listening; use a denser sweep for music.

## CLI (also works without GUI)

```sh
cd ~/Mesh2HRTF
./tools/ear_acoustic_lab/.venv/bin/python tools/ear_acoustic_lab/run_analysis.py \
  --stl ~/model.stl --mic 0 0 0 --units mm \
  --source-mode 'Near 12 cm' --axis=+Y --preset 'Quick ~1kHz' --jobs 4
```

The launcher automatically resolves the current clone's root; `--mesh2hrtf` optionally overrides it.

## Inspect completed simulations without another NumCalc run

The optional quick 1 kHz preset solves only **31 frequency points, 500–3500 Hz, 100 Hz apart**. The new default **20 kHz fast (80 tones)** is a low-cost full-band survey, not a high-resolution resonance search.
That cannot establish there are no narrow resonances. A smooth graph from
that sweep is inconclusive about peaks between the samples.

After any completed run, press **Reanalyze saved** to read the newest completed
project in your chosen results folder. Then click **Open report** to see:

- `analysis/report.html`: explanation of each graph, frequency spacing, gain
  statistics, sampled extrema and explicit interpretation limitations.
- `analysis/response.png`: four panels — absolute relative gain, band-median
  removed coloration, phase and phase-derived group delay.
- `analysis/diagnostic_samples.csv` and `diagnostics.json`: numerical values
  and warnings, not just a thresholded list of >3 dB resonance peaks. The new
  numeric screening identifies the first suspicious frequency and reports
  suspect gain spikes / adjacent-frequency jumps in 500 Hz bands.
- `analysis/solver_checks.json`: NumCalc solver residuals from saved `NC*.out`
  logs, if present. Even tiny residuals do NOT verify mesh convergence.
- `analysis/probe_spread.csv`: consistency of the three closely spaced
  microphone-pressure samples, useful for detecting high-frequency evaluation
  instability. Unverified extreme-gain responses are refused for music audition.
- `analysis/surface_pressure.png`: maps of the pressure solved on the surface
  at up to four selected *actually simulated* frequencies.
- `Output2HRTF/vtk/Reference_pressure_db/frequency_step_*.vtk`: the same
  surface pressure fields for interactive inspection in ParaView.

**There is no need to run NumCalc again to make these reports.** The tool
uses saved solver output. It does not hallucinate missing frequencies or
reconstruct a spatial volume pressure field from the boundary mesh.

For an existing run, you can also use the command line:

```sh
cd ~/Mesh2HRTF
./tools/ear_acoustic_lab/.venv/bin/python tools/ear_acoustic_lab/reanalyze.py \
  ~/EarAcousticLab/results/ear_20261110_155147_122202
```

Select **Focused ~1kHz** or **Deep 1kHz** if you specifically need to
resolve tonal peaks in the 700–1600 Hz range. For genuinely broad music EQ
you must compute more of the audible range and validate the measured
headphone/microphone arrangement; this program does not yet output a
measurement-validated EQ correction.
## Long broadband runs, 3D cutaways, and sharing results through GitHub

From the GUI choose one of these additional frequency test batteries:

| Preset | Frequencies | Sampling |
| --- | ---: | --- |
| 20 kHz fast (80 tones) | 80 | 100 Hz to 20 kHz, endpoint-exact, ~252 Hz |
| 24 kHz fast (80 tones) | 80 | 100 Hz to 24 kHz, endpoint-exact, ~303 Hz |
| 20 kHz overview (100 Hz) | 200 | 100 Hz to 20 kHz |
| 24 kHz overview (100 Hz) | 240 | 100 Hz to 24 kHz |
| 10 kHz overview (100 Hz) | 100 | 100 Hz to 10 kHz |
| 10 kHz detailed (25 Hz) | 397 | 100 Hz to 10 kHz |
| 10 kHz deep (10 Hz) | 991 | 100 Hz to 10 kHz |

**For tonight's first diagnostic, choose the fast 20 kHz 80-tone preset; choose fast 24 kHz if you want the higher endpoint.** The 80 solves are substantially fewer than the previous 991-tone 10 Hz run, but even fast mode may take longer than 30 minutes because high-frequency NumCalc solves are more costly. If the broad-band scan looks plausible, run a denser targeted grid in the suspicious band. Long scans can run for an hour or longer; there is no artificial time limit.
Wall-clock time depends on your CPU, number of NumCalc jobs, available memory
and the mesh. Four NumCalc processes is the default. Leave the GUI running.
These scans are linear acoustic solves; nonlinear rattling, clipping and
harmonic distortion are not simulated.

**Resume a long run after interruption:** use the GUI button **Resume interrupted
scan**, or restart from the existing run folder without importing the STL again:

```sh
cd ~/Mesh2HRTF
tools/ear_acoustic_lab/.venv/bin/python tools/ear_acoustic_lab/run_analysis.py \
  --resume ~/Mesh2HRTF/earlab_runs/ear_YOUR_TIMESTAMP --jobs 4
```

NumCalc's manager skips previously written per-frequency pEvalGrid outputs.
Do not resume a project while an earlier NumCalc manager is still working
on it; the files are not designed for concurrent runs of the same project.
**3D inspection after an existing run:** press Reanalyze saved and Open report.
The report now renders from the **+Y pinna opening**, includes a more sideward
static view with unusually large triangles hidden, and an offline interactive
surface_3d.html viewer with camera presets (+Y/-Y/+X/-X/+Z/angled) and modes
Full mesh / Hide large faces / Cut X / Cut Y. The geometry is never altered
for the underlying BEM computation. **Hide large faces** now uses longest-edge length instead of triangle area (a 20 mm floor and adaptive 97th-percentile cutoff), so it can hide skinny back panels without discarding smaller pinna triangles. The display cuts reveal existing inner
surface triangles if present, but do NOT calculate pressure on an interior
cross-sectional plane through air. For that we would need additional
NumCalc evaluation points along an actual canal centreline.

**Results are now stored inside the checkout.** Full solver workspaces go to
earlab_runs/ (Git-ignored: potentially gigabytes). After a successful run
the tool automatically copies a compact snapshot to
tools/ear_acoustic_lab/shared_runs/<run-name>/, containing plots, SOFA,
CSV/JSON and up to four selected complex boundary-pressure samples with
the associated mesh geometry. The GUI has a **Prepare latest completed run
for GitHub** button to do the same for old results, including runs in the
previous ~/EarAcousticLab/results location.

Git changes are local until you choose to publish:

```sh
cd ~/Mesh2HRTF
git add tools/ear_acoustic_lab/shared_runs
git commit -m "Add ear acoustic measurement snapshot"
git push
```

**Privacy:** this is a public GitHub fork. Snapshots can expose ear geometry
and the source/microphone coordinates when pushed. Your original STL is
excluded by default; adding --include-stl via share_results.py is optional.

To share an older completed run explicitly:

```sh
tools/ear_acoustic_lab/.venv/bin/python tools/ear_acoustic_lab/share_results.py \
  ~/EarAcousticLab/results/ear_20261110_155147_122202
```
## What is and is not modeled

The STL **must contain the hollow, air-connected canal geometry, any microphone tube's air passage and its actual termination surface** to simulate their resonances. Simply importing an STL of the external pinna or a two-block print without the internal air surfaces does *not* recover them. Mic origin `(0,0,0)` is convenient only after you position the actual model to make the correct air probe coincide with that coordinate. The source must be in external air, not inside the plastic. The input may require repairing inconsistent winding, disconnected components, self-intersections or non-manifold surfaces first. The program warns about some mesh defects but does not automatically correct a bad STL.

The initial model uses **rigid PLA-like walls, an ideal point source, an ideal pressure probe and linear wave acoustics**; it does not yet model the capsule's acoustic impedance, narrow-passage viscothermal attenuation, leaks, PLA bending, or nonlinear microphone clipping. Those omissions may shift, exaggerate or suppress peaks. The 0.1 mm microphone evaluation triangle is just three nearly coincident numerical probe locations; it does **not** set the BEM surface discretization. The probe triangle is automatically reduced when the requested microphone lies closer than 0.4 mm to the wall. A microphone within 0.15 mm now produces a warning rather than an unconditional failure; measurements that close to an acoustic boundary may still be numerically inaccurate. The model does not infer whether the coordinate lies inside PLA or the connected air cavity.

Outputs are normalized against an analytical unobstructed point source at the true **source–microphone separation** to give a relative frequency-response curve. `response.csv` also reports raw complex microphone pressure. `peaks.json` includes peak frequency, prominence and approximate Q, which is only meaningful if the frequency spacing resolves the peak. A coarse sweep can miss narrow resonances.

**Verification:** The Python diagnostic, geometry-grid, and music-audition tests can run locally; however full Blender → NumCalc → STL mesh integration depends on your machine and must be checked with the real `~/model.stl`.

## Mesh diagnostics: a manifold surface can still fail NumCalc

NumCalc rejects extremely skinny triangles even on a manifold mesh. For
triangles it uses `reference length = 1.51967 * sqrt(area)`, warns if
`longest_edge / reference_length > 10`, and stops at `> 100`.

Ear Acoustic Lab now diagnoses the **exported NumCalc mesh** rather than the
unprocessed STL. For suspect elements it writes into the run folder:

- `mesh_quality.csv`: exact NumCalc element IDs, edge lengths, areas, quality
  ratios and triangle-centroid coordinates in millimetres.
- `mesh_quality.json`: counts, offending element IDs and the worst triangles.
- `mesh_bad_triangles.obj` plus `.mtl`: import over the source STL in Blender
  to see bad elements (red = fatal, orange = warning). Coordinates match
  the original STL unit setting.
- `3d Model.blend`: the exact exported Reference mesh with matching face IDs.

For example, to select NumCalc element **9917** in Blender, open the saved
`3d Model.blend`, select `Reference`, then run this in Blender's Python Console:

```python
import bpy
bpy.ops.object.mode_set(mode='OBJECT')
o = bpy.data.objects['Reference']
bpy.context.view_layer.objects.active = o
o.select_set(True)
o.data.polygons[9917].select = True
bpy.ops.object.mode_set(mode='EDIT')
```

Return to the 3D Viewport and press **Frame Selected** (numpad decimal).
Correct the local sliver/Boolean topology; do not blindly voxel-remesh
the ear cavity. `Smooth by Angle` only changes shading normals.

The mesh report additionally records longest-edge statistics and the fraction exceeding a wavelength/6 rule-of-thumb at 5, 10, 20 and 24 kHz. These are warnings, not proof of a valid calculation. In the first published 991-tone example, the original STL was **non-watertight**; 12 of 9074 triangles exceeded NumCalc's aspect-ratio warning threshold, and the microphone response jumped to +132 dB. Treat the spikes as unverified until mesh, topology, and solver residuals have been checked.

The Mesh2Input exporter now keeps twelve decimal places in metres instead
of six, so sub-millimetre geometry is not needlessly quantized.
## Troubleshooting

- `Blender not found` / `No module named ...`: Install Arch packages above and rerun `install_and_run.sh`.
- `NumCalc -estimate_ram failed`: The launcher now prints NumCalc's terminal diagnostics and the end of `NumCalc/source_1/NC.out`, rather than suppressing the actual error. An open or invalidly oriented STL is a potential cause; inspect those messages before trying a repair.
- `Invalid mesh` / missing pressure response: Check `mesh_preflight.json`, the saved Blender `.blend`, and NumCalc logs under the same result directory.
- `Mic lies on wall` / `source in plastic`: Re-export the STL relative to its true microphone location and adjust source axis.
- OOM or very slow solver: Start with `Quick ~1kHz` and a cleaned, reduced triangle count *away from the canal*. NumCalc limits concurrent jobs according to host RAM. Use `--jobs 1` if necessary.
- Graph shows an impossible resonance: Verify real channel/tube geometry, material assumptions and microphone termination before interpreting as physical.

Run component tests with `./tools/ear_acoustic_lab/.venv/bin/python -m unittest discover -s tools/ear_acoustic_lab/tests -v` from the repository root.