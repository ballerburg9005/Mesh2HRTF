"""Blender-native XYZ for diagnostic OBJ files imported Y-forward / Z-up.

NumCalc saves Blender-native XYZ in metres. Our users import the original
STL and overlay OBJ with the same Y-forward / Z-up convention. Do not
pre-rotate OBJ vertices; doing so and then selecting Y/Z import axes causes
a second rotation and makes correct triangles appear elsewhere.
"""


def mesh2input_to_obj(point_m, units="mm"):
    """Convert metres to source STL units, preserving X, Y, Z exactly."""
    if units not in ("mm", "m"):
        raise ValueError("Expected units mm or m")
    x, y, z = (float(value) for value in point_m)
    scale = 1000.0 if units == "mm" else 1.0
    return x * scale, y * scale, z * scale


def obj_y_forward_z_up_to_blender(point):
    """Identity when imported with Forward Y, Up Z and Scale 1.0."""
    return tuple(float(value) for value in point)
