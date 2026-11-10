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

## One-button test

1. Save the physical **air-facing** 3D ear geometry as `~/model.stl`. If possible, **move the STL's coordinate origin to the microphone** in Blender before exporting. Then the GUI's default `Mic XYZ = 0 0 0` is correct; no other microphone setup required. Coordinates must use the same units as the STL, default **millimetres**. You can also specify a different microphone XYZ if you prefer. The STL is imported automatically, not via the Blender GUI.
2. Leave `Sound source` on **Near 12 cm**, `axis -Y` unless that points into solid PLA. Check **Preview STL + mic** to verify the red mic position and orange source position relative to the geometry. The **Far 1 m** analytical point source does not itself increase the number of BEM surface elements. (For accurate physical geometry, the mic must lie in the connected air region; being at the origin alone does not make it correct.)
3. Choose **Quick ~1kHz** initially, then press **RUN ACOUSTIC SWEEP**. NumCalc runs selected frequency-domain acoustic solves and saves `response.png`, `response.csv`, `peaks.json` and the complete solver project to `~/EarAcousticLab/results/ear_<timestamp>/`. Use **Open graphs** to see the response and prominent resonance peaks.
4. For a more precise sweep select **Focused ~1kHz** (10 Hz spacing) or **Deep 1kHz** (5 Hz). Use **Full spectrum** for 100 Hz–20 kHz at 100 Hz steps, or **Detailed spectrum** for 25 Hz steps. These presets can take much longer with detailed meshes.
5. Optionally select a WAV/MP3 music file before running; **Play music audition** applies the simulated *linear complex frequency response* to up to 30 s of the input, creating `music_audition.wav`. This is offline auralization, **not** nonlinear mic distortion or actual music being injected into the frequency-domain solver. Sparse sweeps may be inaccurate for listening; use a denser sweep for music.

## CLI (also works without GUI)

```sh
cd ~/Mesh2HRTF
./tools/ear_acoustic_lab/.venv/bin/python tools/ear_acoustic_lab/run_analysis.py \
  --stl ~/model.stl --mic 0 0 0 --units mm \
  --source-mode 'Near 12 cm' --axis=-Y --preset 'Quick ~1kHz' --jobs 2
```

The launcher automatically resolves the current clone's root; `--mesh2hrtf` optionally overrides it.

## What is and is not modeled

The STL **must contain the hollow, air-connected canal geometry, any microphone tube's air passage and its actual termination surface** to simulate their resonances. Simply importing an STL of the external pinna or a two-block print without the internal air surfaces does *not* recover them. Mic origin `(0,0,0)` is convenient only after you position the actual model to make the correct air probe coincide with that coordinate. The source must be in external air, not inside the plastic. The input may require repairing inconsistent winding, disconnected components, self-intersections or non-manifold surfaces first. The program warns about some mesh defects but does not automatically correct a bad STL.

The initial model uses **rigid PLA-like walls, an ideal point source, an ideal pressure probe and linear wave acoustics**; it does not yet model the capsule's acoustic impedance, narrow-passage viscothermal attenuation, leaks, PLA bending, or nonlinear microphone clipping. Those omissions may shift, exaggerate or suppress peaks. The 0.1 mm microphone evaluation triangle is just three nearly coincident numerical probe locations; it does **not** set the BEM surface discretization. Make sure the probe is at least 0.15 mm from the modeled wall.

Outputs are normalized against an analytical unobstructed point source at the true **source–microphone separation** to give a relative frequency-response curve. `response.csv` also reports raw complex microphone pressure. `peaks.json` includes peak frequency, prominence and approximate Q, which is only meaningful if the frequency spacing resolves the peak. A coarse sweep can miss narrow resonances.

**Verification:** The Python diagnostic, geometry-grid, and music-audition tests can run locally; however full Blender → NumCalc → STL mesh integration depends on your machine and must be checked with the real `~/model.stl`.

## Troubleshooting

- `Blender not found` / `No module named ...`: Install Arch packages above and rerun `install_and_run.sh`.
- `Invalid mesh` / missing pressure response: Check `mesh_preflight.json`, the saved Blender `.blend`, and NumCalc logs under the same result directory.
- `Mic lies on wall` / `source in plastic`: Re-export the STL relative to its true microphone location and adjust source axis.
- OOM or very slow solver: Start with `Quick ~1kHz` and a cleaned, reduced triangle count *away from the canal*. NumCalc limits concurrent jobs according to host RAM. Use `--jobs 1` if necessary.
- Graph shows an impossible resonance: Verify real channel/tube geometry, material assumptions and microphone termination before interpreting as physical.

Run component tests with `./tools/ear_acoustic_lab/.venv/bin/python -m unittest discover -s tools/ear_acoustic_lab/tests -v` from the repository root.