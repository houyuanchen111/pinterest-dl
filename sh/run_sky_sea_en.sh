#!/usr/bin/env sh
set -eu

REPO_DIR=${REPO_DIR:-/mnt/aigc/houyuanchen/pinterest-dl}
OUTPUT_DIR=${OUTPUT_DIR:-$REPO_DIR/light_repo/sky_sea_en}
JSON_PATH=${JSON_PATH:-$OUTPUT_DIR/pinterest_images.json}
COOKIES=${COOKIES:-$REPO_DIR/cookies.json}
PYTHON=${PYTHON:-/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio/bin/python}
NUM=${NUM:-1000}
KEYWORD=${KEYWORD:-"sky sea"}

cd "$REPO_DIR"

if [ ! -f "$COOKIES" ]; then
  echo "Missing cookies file: $COOKIES" >&2
  exit 1
fi

if [ ! -x "$PYTHON" ]; then
  echo "Missing executable Python interpreter: $PYTHON" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

"$PYTHON" crawl_pinterest_related_images.py "$KEYWORD" \
  --mode keyword \
  -n "$NUM" \
  -o "$OUTPUT_DIR" \
  --json-path "$JSON_PATH" \
  --cookies "$COOKIES" \
  "$@"
