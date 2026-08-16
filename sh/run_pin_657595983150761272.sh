#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=${REPO_DIR:-/mnt/aigc/houyuanchen/pinterest-dl}
PYTHON_ENV=${PYTHON_ENV:-/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio}
PINTEREST_DL=${PINTEREST_DL:-$PYTHON_ENV/bin/pinterest-dl}
PIN_UID=${PIN_UID:-657595983150761272}
PIN_URL=${PIN_URL:-"https://www.pinterest.com/pin/$PIN_UID/"}
OUTPUT_DIR=${OUTPUT_DIR:-$REPO_DIR/light_repo/pin_${PIN_UID}_related}
CACHE_PATH=${CACHE_PATH:-$OUTPUT_DIR/pinterest_images.json}
NUM=${NUM:-1000}
RETRY_DELAY=${RETRY_DELAY:-30}

if [[ ! -x "$PINTEREST_DL" ]]; then
  echo "Missing executable pinterest-dl command: $PINTEREST_DL" >&2
  exit 1
fi

cd "$REPO_DIR"
mkdir -p "$OUTPUT_DIR"

echo "[$(date '+%F %T')] Starting related-image crawl for pin UID: $PIN_UID"
echo "[$(date '+%F %T')] Pin URL: $PIN_URL"
echo "[$(date '+%F %T')] Output directory: $OUTPUT_DIR"

while true; do
  if "$PINTEREST_DL" scrape "$PIN_URL" \
    --related-only \
    --client api \
    --num "$NUM" \
    --output "$OUTPUT_DIR" \
    --cache "$CACHE_PATH" \
    "$@"; then
    break
  else
    status=$?
  fi

  echo "[$(date '+%F %T')] Crawl exited with status $status; retrying in ${RETRY_DELAY}s..." >&2
  sleep "$RETRY_DELAY"
done

echo "[$(date '+%F %T')] Related-image crawl completed for pin UID: $PIN_UID"
