#!/usr/bin/env sh
set -eu

REPO_DIR=${REPO_DIR:-/mnt/aigc/houyuanchen/pinterest-dl}
OUTPUT_DIR=${OUTPUT_DIR:-$REPO_DIR/light_repo/sky_en}
JSON_PATH=${JSON_PATH:-$OUTPUT_DIR/pinterest_images.json}
PYTHON=${PYTHON:-/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio/bin/python}
NUM=${NUM:-1000}
KEYWORD=${KEYWORD:-"sky"}
RETRY_DELAY=${RETRY_DELAY:-30}

cd "$REPO_DIR"

if [ ! -x "$PYTHON" ]; then
  echo "Missing executable Python interpreter: $PYTHON" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

while true; do
  if PYTHONUNBUFFERED=1 "$PYTHON" search_atmosphere_images.py \
    --query "$KEYWORD" \
    --all \
    --max-items "$NUM" \
    --output "$OUTPUT_DIR" \
    --cache "$JSON_PATH" \
    "$@"; then
    break
  else
    status=$?
  fi

  echo "Crawler exited with status $status; retrying in ${RETRY_DELAY}s..." >&2
  sleep "$RETRY_DELAY"
done
