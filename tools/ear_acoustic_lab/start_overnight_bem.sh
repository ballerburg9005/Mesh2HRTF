#!/usr/bin/env bash
# Start exactly one bounded, restartable NumCalc validation in background.
# No Blender, no frequency sweep, no interaction after launch.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
DEFAULT_PROJECT="$ROOT/earlab_runs/ear_20261112_014827_323293"
PROJECT="${1:-$DEFAULT_PROJECT}"
if [[ "$PROJECT" != /* ]]; then PROJECT="$ROOT/$PROJECT"; fi
PROJECT="$(realpath -m -- "$PROJECT")"

PY="$ROOT/tools/ear_acoustic_lab/.venv/bin/python"
SCRIPT="$ROOT/tools/ear_acoustic_lab/overnight_bem_validation.py"
SOLVER="$ROOT/mesh2hrtf/NumCalc/bin/NumCalc"
if [[ ! -x "$PY" ]]; then
  echo "[LAUNCH FAILED] Ear lab virtualenv python not found: $PY" >&2
  exit 1
fi
if [[ ! -x "$SOLVER" ]]; then
  echo "[LAUNCH FAILED] NumCalc must be compiled: $SOLVER" >&2
  exit 1
fi
if [[ ! -f "$PROJECT/NumCalc/source_1/NC.inp" ]]; then
  echo "[LAUNCH FAILED] Export-only NumCalc project not found: $PROJECT" >&2
  exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "[LAUNCH FAILED] flock (util-linux) is required to prevent concurrent runs." >&2
  exit 1
fi

OUT="$PROJECT/analysis/overnight_bem_validation_steps_13_14"
mkdir -p -- "$OUT"
LOG="$OUT/launcher.log"
LOCK="$OUT/overnight.lock"

# A child can only acquire this lock once. If started again while an
# overnight run is active, the second attempt quits immediately.
# A retained completed lock file is fine: locks are not persistent.
nohup flock -n -E 75 "$LOCK" "$PY" -u "$SCRIPT" \
  --project "$PROJECT" --timeout-per-case 900 --max-hours 6 \
  >> "$LOG" 2>&1 < /dev/null &
CHILD_PID=$!

echo "[OVERNIGHT LAUNCHED] PID=$CHILD_PID"
echo "[OVERNIGHT] No further interaction is required. Leave the computer powered on and awake."
echo "[OVERNIGHT] Log: $LOG"
echo "[OVERNIGHT] Morning report: $OUT/overnight_report.txt"
echo "[OVERNIGHT] Per-case results: $OUT/results.json"
echo "[OVERNIGHT] Lock prevents starting a second simultaneous run."
