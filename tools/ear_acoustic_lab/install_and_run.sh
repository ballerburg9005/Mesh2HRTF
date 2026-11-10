#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
if [[ ! -f "$REPO/mesh2hrtf/Mesh2Input/mesh2input.py" ]]; then
  echo "Error: this script must be inside your Mesh2HRTF checkout at tools/ear_acoustic_lab/" >&2
  exit 1
fi
for command in blender python make g++; do
  if ! command -v "$command" >/dev/null; then
    echo "Missing '$command'. On Arch Linux install dependencies:" >&2
    echo "sudo pacman -S --needed blender python python-pip tk base-devel ffmpeg libsndfile" >&2
    exit 1
  fi
done
if [[ ! -x "$HERE/.venv/bin/python" ]]; then
  python -m venv "$HERE/.venv"
fi
"$HERE/.venv/bin/python" -m pip install -e "$REPO" numpy scipy matplotlib soundfile trimesh plotly
make -C "$REPO/mesh2hrtf/NumCalc/src" -j2
exec "$HERE/.venv/bin/python" "$HERE/earlab_gui.py"
