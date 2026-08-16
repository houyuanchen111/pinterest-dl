#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=${REPO_DIR:-/mnt/aigc/houyuanchen/pinterest-dl}
OUTPUT_ROOT=${OUTPUT_ROOT:-$REPO_DIR/light_repo/lens_flare}
PYTHON=${PYTHON:-/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio/bin/python}
NUM=${NUM:-1000}
RETRY_DELAY=${RETRY_DELAY:-30}
QUERY=${QUERY:-"镜头光晕"}

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing executable Python interpreter: $PYTHON" >&2
  exit 1
fi

cd "$REPO_DIR"
mkdir -p "$OUTPUT_ROOT"

JSON_PATH="$OUTPUT_ROOT/pinterest_images.json"

echo "[$(date '+%F %T')] Starting: $QUERY -> $OUTPUT_ROOT"

while true; do
  if PYTHONUNBUFFERED=1 "$PYTHON" search_atmosphere_images.py \
    --query "$QUERY" \
    --all \
    --max-items "$NUM" \
    --output "$OUTPUT_ROOT" \
    --cache "$JSON_PATH" \
    "$@"; then
    break
  else
    status=$?
  fi

  echo "[$(date '+%F %T')] Query '$QUERY' exited with status $status; retrying in ${RETRY_DELAY}s..." >&2
  sleep "$RETRY_DELAY"
done

echo "[$(date '+%F %T')] Lens flare crawl completed."
