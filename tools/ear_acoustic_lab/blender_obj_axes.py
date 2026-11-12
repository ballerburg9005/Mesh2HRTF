"""Coordinate conversion for Blender's default OBJ import axis settings.

Mesh2Input stores Blender-native xyz in metres. Blender 5.2 OBJ import
defaults to forward=-Z and up=Y, not the STL import's Y/Z convention.
Using conventional OBJ coordinates (x,z,-y) ensures no manual rotation is
needed when importing a diagnostic overlay over '3d Model.blend'.
"""


def mesh2input_to_obj(point_m, units="mm"):
    if units not in ("mm", "m"):
        raise ValueError("Expected units mm or m")
    x, y, z = (float(value) for value in point_m)
    scale = 1000.0 if units == "mm" else 1.0
    return x * scale, z * scale, -y * scale


def obj_default_import_to_blender(point):
    """Inverse transform, for tests and documentation only."""
    x, y, z = (float(value) for value in point)
    return x, -z, y
