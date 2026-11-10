"""Interactive inspection of NumCalc *boundary* pressure (never a new solve).

The visualization-only masks do not alter the solver surface or acoustic
boundary conditions. In particular, a cutaway does NOT calculate pressure
on a new cutting plane or at interior air points.
"""
from pathlib import Path
import numpy as np


def mesh_views(vertices_mm, faces):
    """Masks for full, remove large triangles, and two geometric cutaways."""
    verts = np.asarray(vertices_mm, float)
    faces = np.asarray(faces, int)
    tri = verts[faces]
    cross = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    areas = .5 * np.linalg.norm(cross, axis=1)
    # Filter by longest edge, NOT by area: long, narrow back/enclosure
    # triangles can have a tiny area even if their longest edge is 60 mm.
    # Prefer retaining the detailed pinna triangles while hiding those
    # oversized panels. The hard 12 mm floor avoids removing normal faces
    # from a finely tessellated pinna, even on a small/atypical mesh.
    # NumCalc triangle distortion diagnostic is a separate display mode.
    e0 = np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1)
    e1 = np.linalg.norm(tri[:, 2] - tri[:, 1], axis=1)
    e2 = np.linalg.norm(tri[:, 0] - tri[:, 2], axis=1)
    max_edge = np.maximum(np.maximum(e0, e1), e2)
    cutoff_mm = max(12.0, float(np.quantile(max_edge, .97)))
    trimmed = max_edge <= cutoff_mm
    if not np.any(trimmed):
        trimmed = np.ones(len(faces), dtype=bool)
    distortion = max_edge / (1.51967 * np.sqrt(np.maximum(areas, 1e-30)))
    well_shaped = (areas > 1e-14) & (distortion <= 10.0)
    if not np.any(well_shaped):
        well_shaped = np.ones(len(faces), dtype=bool)
    centers = tri.mean(axis=1)
    xmid = float(np.median(verts[:, 0]))
    ymid = float(np.median(verts[:, 1]))
    x_cut = centers[:, 0] >= xmid
    y_cut = centers[:, 1] <= ymid
    return {
        "Full mesh": np.ones(len(faces), dtype=bool),
        f"Hide large faces ({np.count_nonzero(~trimmed)} removed; edge >{cutoff_mm:.1f} mm)": trimmed,
        f"Hide distorted faces ({np.count_nonzero(~well_shaped)} removed)": well_shaped,
        "Cut X: show +X half": x_cut,
        "Cut Y: show back half": y_cut,
    }


def write_interactive_surface(output, vertices_mm, faces, frequencies_hz,
                              complex_surface_pressure, mic_mm=None,
                              suspect_onset_hz=None):
    """Create an offline, rotatable 3-D surface viewer.

    complex_surface_pressure must have shape (n_faces, n_selected_freq).
    Colors are dB relative to the surface median at each displayed frequency.
    """
    import plotly.graph_objects as go

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    verts = np.asarray(vertices_mm, dtype=float)
    faces = np.asarray(faces, dtype=int)
    freq = np.asarray(frequencies_hz, dtype=float).reshape(-1)
    pressure = np.asarray(complex_surface_pressure)
    if verts.ndim != 2 or verts.shape[1] != 3:
        raise ValueError("Expected (vertices,3) geometry")
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError("Expected triangular surface geometry")
    if len(faces) == 0 or np.any(faces < 0) or np.any(faces >= len(verts)):
        raise ValueError("Invalid face indices")
    if pressure.shape != (len(faces), len(freq)):
        raise ValueError("Expected one complex boundary pressure per face and frequency")
    if len(freq) == 0:
        raise ValueError("At least one solved frequency is required")

    db = 20.0*np.log10(np.maximum(np.abs(pressure), 1e-15))
    db -= np.median(db, axis=0, keepdims=True)
    limit = float(np.clip(np.quantile(np.abs(db), .95), 3., 30.))
    views = mesh_views(verts, faces)
    fig = go.Figure()
    settings = []
    for fi, hz in enumerate(freq):
        flag_text = " [UNVERIFIED]" if (suspect_onset_hz is not None
                     and hz >= suspect_onset_hz) else ""
        for label, mask in views.items():
            subset = faces[mask]
            # Plotly supports one intensity value per triangle in cell mode.
            fig.add_trace(go.Mesh3d(
                x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
                i=subset[:, 0], j=subset[:, 1], k=subset[:, 2],
                intensity=db[mask, fi],
                intensitymode="cell", flatshading=True,
                colorscale="RdBu_r", cmin=-limit, cmax=limit,
                showscale=True, colorbar=dict(title="relative dB"),
                hovertemplate="Boundary pressure: %{intensity:.2f} dB<extra></extra>",
                name=f"{hz:g} Hz{flag_text} · {label}",
                visible=(fi == 0 and label == "Full mesh"),
                lighting=dict(ambient=.65, diffuse=.75, specular=.08),
                lightposition=dict(x=100, y=100, z=100)))
            settings.append((fi, hz, label, flag_text))

    if mic_mm is not None:
        xyz = np.asarray(mic_mm, dtype=float)
        if xyz.shape == (3,):
            fig.add_trace(go.Scatter3d(
                x=[xyz[0]], y=[xyz[1]], z=[xyz[2]], mode="markers",
                marker=dict(color="#191919", size=5, symbol="circle"),
                name="Microphone at selected XYZ", visible=True))

    camera = {
        "Front (+Y / pinna opening)": dict(eye=dict(x=0, y=2.4, z=.4)),
        "Back (-Y)": dict(eye=dict(x=0, y=-2.4, z=.4)),
        "Side (+X)": dict(eye=dict(x=2.4, y=.15, z=.5)),
        "Side (-X)": dict(eye=dict(x=-2.4, y=.15, z=.5)),
        "Side/back (+X,-Y)": dict(eye=dict(x=2.4, y=-.65, z=.5)),
        "Side/back (-X,-Y)": dict(eye=dict(x=-2.4, y=-.65, z=.5)),
        "Top (+Z)": dict(eye=dict(x=.15, y=.2, z=2.4)),
        "Angled (+Y,+X)": dict(eye=dict(x=1.4, y=1.9, z=.6)),
    }
    selection_buttons = []
    for i, (_, hz, label, flag_text) in enumerate(settings):
        visible = [j == i for j in range(len(settings))]
        if mic_mm is not None and np.asarray(mic_mm).shape == (3,):
            visible.append(True)
        # A side/back camera is essential when the large enclosure panels
        # are hidden. Selecting a mode must change camera as well as mesh.
        if label.startswith("Hide large faces"):
            preferred_camera = camera["Side/back (+X,-Y)"]
        elif label.startswith("Hide distorted faces"):
            preferred_camera = camera["Front (+Y / pinna opening)"]
        elif label.startswith("Cut X"):
            preferred_camera = camera["Side (-X)"]
        elif label.startswith("Cut Y"):
            preferred_camera = camera["Front (+Y / pinna opening)"]
        else:
            preferred_camera = camera["Front (+Y / pinna opening)"]
        selection_buttons.append(dict(
            label=f"{hz:g} Hz{flag_text} · {label}",
            method="update",
            args=[{"visible": visible},
                  {"title": f"{hz:g} Hz{flag_text} · {label}",
                   "scene.camera": preferred_camera}]))
    camera_buttons = [
        dict(label=name, method="relayout",
             args=[{"scene.camera": viewpoint}])
        for name, viewpoint in camera.items()]
    bounds = np.ptp(verts, axis=0)
    aspect = bounds / max(float(bounds.max()), 1.)
    fig.update_layout(
        title=f"{freq[0]:g} Hz · Full mesh",
        scene=dict(
            xaxis_title="X (mm)", yaxis_title="Y (mm)",
            zaxis_title="Z (mm)", aspectmode="manual",
            aspectratio=dict(x=max(aspect[0], .1),
                             y=max(aspect[1], .1),
                             z=max(aspect[2], .1)),
            camera=camera["Front (+Y / pinna opening)"]),
        updatemenus=[
            dict(type="dropdown", x=.0, y=1.12, xanchor="left",
                 buttons=selection_buttons, showactive=True),
            dict(type="dropdown", x=.57, y=1.12, xanchor="left",
                 buttons=camera_buttons, showactive=True)],
        annotations=[dict(
            text="Filters hide faces in the picture only, NOT in the solver. Distorted-face mode is diagnostic; it cannot repair numerical artifacts.",
            x=0, y=-.07, xref="paper", yref="paper", showarrow=False,
            xanchor="left", font=dict(size=12))],
        width=1100, height=770,
        margin=dict(l=0, r=30, b=65, t=100),
        showlegend=False)
    path = output/"surface_3d.html"
    fig.write_html(str(path), include_plotlyjs=True, full_html=True)
    return path


def write_vtk_surfaces(output, vertices_mm, faces, frequencies_hz, complex_pressure):
    """Write selected already-calculated surface values to ParaView legacy VTK.

    Unlike Mesh2HRTF.export_vtk this does not re-read ALL solver frequency
    files for every selected tone; use this for long sweeps.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    verts = np.asarray(vertices_mm, float)
    triangles = np.asarray(faces, int)
    hz = np.asarray(frequencies_hz, float)
    pressure = np.asarray(complex_pressure)
    if pressure.shape != (len(triangles), len(hz)):
        raise ValueError("Expected one pressure per face and selected tone")
    geometry = (
        "# vtk DataFile Version 3.0\nNumCalc pressure on BEM boundary\n"
        "ASCII\nDATASET POLYDATA\n"
        f"POINTS {len(verts)} float\n" +
        "".join(f"{x:.9g} {y:.9g} {z:.9g}\n" for x,y,z in verts) +
        f"POLYGONS {len(triangles)} {len(triangles)*4}\n" +
        "".join(f"3 {a} {b} {c}\n" for a,b,c in triangles) +
        f"CELL_DATA {len(triangles)}\n"
    )
    paths = []
    for i, freq in enumerate(hz):
        p_db = 20*np.log10(np.maximum(np.abs(pressure[:,i]), 1e-15))
        rel = p_db - float(np.median(p_db))
        suffix = str(int(round(freq))) if abs(freq-round(freq)) < .001 else f"{freq:.3f}".replace(".","p")
        path = output / f"boundary_{suffix}Hz.vtk"
        path.write_text(
            geometry +
            "SCALARS pressure_relative_surface_median_db float 1\n"
            "LOOKUP_TABLE default\n" +
            "".join(f"{v:.8g}\n" for v in rel) +
            "SCALARS pressure_db_re_1Pa float 1\n"
            "LOOKUP_TABLE default\n" +
            "".join(f"{v:.8g}\n" for v in p_db))
        paths.append(str(path))
    return paths
