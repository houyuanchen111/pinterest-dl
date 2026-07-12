#!/usr/bin/env sh
set -eu

REPO_DIR=/mnt/aigc/houyuanchen/pinterest-dl
OUTPUT_DIR=/mnt/aigc/houyuanchen/pinterest-dl/pinterest
JSON_PATH=/mnt/aigc/houyuanchen/pinterest-dl/pinterest/pinterest_images.json
COOKIES=/mnt/aigc/houyuanchen/pinterest-dl/cookies.json
TARGET_KEYWORD=${TARGET_KEYWORD:-"人像摄影 氛围感"}
NUM=1000

# Optional for smoke tests:
#   DRY_RUN=1 LIMIT=3 sh sh/run_portrait_atmosphere_uid_related_1000.sh
DRY_RUN=${DRY_RUN:-0}
LIMIT=${LIMIT:-}

cd "$REPO_DIR"

if [ ! -f "$JSON_PATH" ]; then
  echo "Missing JSON file: $JSON_PATH" >&2
  exit 1
fi

if [ ! -f "$COOKIES" ]; then
  echo "Missing cookies file: $COOKIES" >&2
  exit 1
fi

PIN_UIDS=$(
  TARGET_KEYWORD="$TARGET_KEYWORD" JSON_PATH="$JSON_PATH" LIMIT="$LIMIT" python - <<'PY'
import json
import os
import sys
from pathlib import Path

json_path = Path(os.environ["JSON_PATH"])
target_keyword = os.environ["TARGET_KEYWORD"]
limit_text = os.environ.get("LIMIT", "").strip()

try:
    limit = int(limit_text) if limit_text else None
except ValueError:
    print(f"LIMIT must be an integer, got: {limit_text}", file=sys.stderr)
    sys.exit(2)

if limit is not None and limit < 0:
    print(f"LIMIT must be >= 0, got: {limit}", file=sys.stderr)
    sys.exit(2)

with json_path.open("r", encoding="utf-8") as fh:
    records = json.load(fh)

seen = set()
emitted = 0
for record in records:
    if record.get("keyword") != target_keyword:
        continue

    pin_uid = str(record.get("pin_uid", "")).strip()
    if not pin_uid or pin_uid in seen:
        continue

    print(pin_uid)
    seen.add(pin_uid)
    emitted += 1

    if limit is not None and emitted >= limit:
        break
PY
)

if [ -z "$PIN_UIDS" ]; then
  echo "No pin_uid found for keyword: $TARGET_KEYWORD" >&2
  exit 1
fi

pin_count=$(printf '%s\n' "$PIN_UIDS" | wc -l | awk '{print $1}')
echo "Found $pin_count unique pin_uid(s) for keyword: $TARGET_KEYWORD"

failures=0

for PIN_UID in $PIN_UIDS
do
  echo "=== Crawling related images for pin_uid: $PIN_UID (-n $NUM) ==="

  if [ "$DRY_RUN" = "1" ]; then
    printf '%s\n' \
      "python crawl_pinterest_related_images.py \"$PIN_UID\" --mode pin -n $NUM -o \"$OUTPUT_DIR\" --json-path \"$JSON_PATH\" --cookies \"$COOKIES\" $*"
    echo "=== Dry-run finished pin_uid: $PIN_UID ==="
    continue
  fi

  if python crawl_pinterest_related_images.py "$PIN_UID" \
    --mode pin \
    -n "$NUM" \
    -o "$OUTPUT_DIR" \
    --json-path "$JSON_PATH" \
    --cookies "$COOKIES" \
    "$@"; then
    echo "=== Finished pin_uid: $PIN_UID ==="
  else
    failures=$((failures + 1))
    echo "=== Failed pin_uid: $PIN_UID ===" >&2
  fi
done

if [ "$failures" -gt 0 ]; then
  echo "Completed with $failures failed pin_uid run(s)." >&2
  exit 1
fi

echo "Completed all pin_uid runs."
