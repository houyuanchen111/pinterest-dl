#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=${REPO_DIR:-/mnt/aigc/houyuanchen/pinterest-dl}
OUTPUT_DIR=${OUTPUT_DIR:-$REPO_DIR/light_repo/bokeh_portrait_night}
JSON_PATH=${JSON_PATH:-$OUTPUT_DIR/pinterest_images.json}
PYTHON=${PYTHON:-/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio/bin/python}
NUM=${NUM:-1000}
QUERY=${QUERY:-"散景 人像 夜晚"}
RETRY_DELAY=${RETRY_DELAY:-30}

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing executable Python interpreter: $PYTHON" >&2
  exit 1
fi

cd "$REPO_DIR"
mkdir -p "$OUTPUT_DIR"

echo "[$(date '+%F %T')] Starting: $QUERY -> $OUTPUT_DIR"

while true; do
  if PYTHONUNBUFFERED=1 "$PYTHON" search_atmosphere_images.py \
    --query "$QUERY" \
    --all \
    --max-items "$NUM" \
    --output "$OUTPUT_DIR" \
    --cache "$JSON_PATH" \
    "$@"; then
    break
  else
    status=$?
  fi

  echo "[$(date '+%F %T')] Query '$QUERY' exited with status $status; retrying in ${RETRY_DELAY}s..." >&2
  sleep "$RETRY_DELAY"
done

echo "[$(date '+%F %T')] Bokeh portrait night crawl completed."
