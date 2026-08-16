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
RESUME_CACHE=${RESUME_CACHE:-0}
MISSING_CACHE_PATH=${MISSING_CACHE_PATH:-$OUTPUT_DIR/.missing_pinterest_images.json}

if [[ ! -x "$PINTEREST_DL" ]]; then
  echo "Missing executable pinterest-dl command: $PINTEREST_DL" >&2
  exit 1
fi

cd "$REPO_DIR"
mkdir -p "$OUTPUT_DIR"

echo "[$(date '+%F %T')] Starting related-image crawl for pin UID: $PIN_UID"
echo "[$(date '+%F %T')] Pin URL: $PIN_URL"
echo "[$(date '+%F %T')] Output directory: $OUTPUT_DIR"

cache_item_count() {
  "$PYTHON_ENV/bin/python" - "$CACHE_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    records = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print(0)
else:
    print(len(records) if isinstance(records, list) else 0)
PY
}

if [[ "$RESUME_CACHE" == "1" ]] && [[ "$(cache_item_count)" -gt 0 ]]; then
  echo "[$(date '+%F %T')] Resuming from existing cache: $CACHE_PATH"
else
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

    cached_items=$(cache_item_count)
    if [[ "$cached_items" -gt 0 ]]; then
      echo "[$(date '+%F %T')] Crawl cached $cached_items items before exiting with status $status."
      echo "[$(date '+%F %T')] Retrying only missing downloads from the cache."
      break
    fi

    echo "[$(date '+%F %T')] Crawl exited with status $status before producing a cache; retrying in ${RETRY_DELAY}s..." >&2
    sleep "$RETRY_DELAY"
  done
fi

while true; do
  missing_items=$(
    "$PYTHON_ENV/bin/python" - "$CACHE_PATH" "$OUTPUT_DIR" "$MISSING_CACHE_PATH" <<'PY'
import json
import sys
from pathlib import Path

cache_path = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
missing_cache_path = Path(sys.argv[3])
records = json.loads(cache_path.read_text(encoding="utf-8"))

missing = []
for record in records:
    pin_id = str(record.get("id", "")).strip()
    if not pin_id:
        continue
    existing = [path for path in output_dir.glob(f"{pin_id}.*") if path.stat().st_size > 0]
    if not existing:
        missing.append(record)

missing_cache_path.write_text(
    json.dumps(missing, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(len(missing))
PY
  )

  if [[ "$missing_items" -eq 0 ]]; then
    rm -f "$MISSING_CACHE_PATH"
    break
  fi

  echo "[$(date '+%F %T')] Retrying $missing_items missing media item(s)."
  if "$PINTEREST_DL" download "$MISSING_CACHE_PATH" \
    --output "$OUTPUT_DIR"; then
    continue
  else
    status=$?
  fi

  echo "[$(date '+%F %T')] Missing-media download exited with status $status; retrying in ${RETRY_DELAY}s..." >&2
  sleep "$RETRY_DELAY"
done

echo "[$(date '+%F %T')] Related-image crawl completed for pin UID: $PIN_UID"
