"""Install a nonuniform frequency list in an exported Mesh2HRTF project.

Mesh2Input exposes only fixed linear step/number; NumCalc itself accepts
arbitrary monotone frequencies in the NC.inp 'Load Frequency Curve'.
This edits *only* the generated project, not the NumCalc solver or STL.
"""
import json
from pathlib import Path

import numpy as np


def log_overview(n=80, low=100., high=20000.):
    if n < 2 or low <= 0 or high <= low:
        raise ValueError("Invalid geometric sweep")
    return np.geomspace(float(low), float(high), n).tolist()


def linear_overview(n=80, low=100., high=20000.):
    """Coarse broadband scan with both endpoint frequencies solved exactly."""
    if n < 2 or low <= 0 or high <= low:
        raise ValueError("Invalid linear sweep")
    return np.linspace(float(low), float(high), int(n)).tolist()


def replace_frequency_vector(project, frequencies):
    project = Path(project)
    freq = np.asarray(frequencies, dtype=float)
    if (freq.ndim != 1 or len(freq) < 2 or
            not np.all(np.isfinite(freq)) or
            np.any(freq <= 0) or np.any(np.diff(freq) <= 0)):
        raise ValueError("Frequencies must be an ordered, unique positive 1D vector")

    nc_path = project / "NumCalc" / "source_1" / "NC.inp"
    params_path = project / "parameters.json"
    if not nc_path.is_file() or not params_path.is_file():
        raise FileNotFoundError("A Blender-generated Mesh2HRTF project is required")
    original = nc_path.read_text()
    lines = original.splitlines(keepends=True)
    control_header = "## Controlparameter II"
    curve_header = "## Load Frequency Curve"
    controls = [i for i, line in enumerate(lines)
                if line.strip() == control_header]
    curves = [i for i, line in enumerate(lines)
              if line.strip() == curve_header]
    if len(controls) != 1 or len(curves) != 1:
        raise ValueError("Unexpected NC.inp format: frequency sections missing")
    control = controls[0] + 1
    values = lines[control].strip().split()
    if len(values) < 7:
        raise ValueError("Unexpected NC.inp Controlparameter II")
    values[1] = str(len(freq))
    lines[control] = " ".join(values) + "\n"

    begin = curves[0] + 1
    end = next((i for i in range(begin, len(lines))
                if lines[i].strip() == "##"), None)
    if end is None:
        raise ValueError("NC.inp frequency curve end marker not found")

    new_curve = [f"0 {len(freq)+1}\n", "0.000000 0.000000e+00 0.0\n"]
    for i, f in enumerate(freq):
        # The second column in Mesh2Input uses 'e+04' as multiplier;
        # NumCalc sees the true physical frequency (Hz).
        new_curve.append(f"{(i+1)*1e-6:.6f} {float(f)/10000:.12f}e+04 0.0\n")
    updated = "".join(lines[:begin] + new_curve + lines[end:])

    params = json.loads(params_path.read_text())
    params["frequencies"] = [float(v) for v in freq]
    params["numFrequencies"] = len(freq)
    params["minFrequency"] = float(freq[0])
    params["maxFrequency"] = float(freq[-1])
    params["frequencyStepSize"] = 0.0
    # Write input and metadata after checking both were parseable.
    nc_path.write_text(updated)
    params_path.write_text(json.dumps(params, indent=4))
    return len(freq)
