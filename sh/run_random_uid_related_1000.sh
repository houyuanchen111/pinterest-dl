#!/usr/bin/env sh
set -eu

REPO_DIR=${REPO_DIR:-/mnt/aigc/houyuanchen/pinterest-dl}
OUTPUT_DIR=${OUTPUT_DIR:-$REPO_DIR/pinterest}
JSON_PATH=${JSON_PATH:-$OUTPUT_DIR/pinterest_images.json}
COOKIES=${COOKIES:-$REPO_DIR/cookies.json}
PYTHON=${PYTHON:-/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio/bin/python}

# Default job: sample 2,000 unique pin UIDs and process up to 1,000 related
# results for each UID. Set RANDOM_SEED explicitly to reproduce a sample.
SAMPLE_SIZE=${SAMPLE_SIZE:-2000}
NUM=${NUM:-1000}
RANDOM_SEED=${RANDOM_SEED:-$(date +%s)}

# Optional for smoke tests:
#   DRY_RUN=1 LIMIT=3 sh sh/run_random_uid_related_1000.sh
#   NUM=1 LIMIT=1 sh sh/run_random_uid_related_1000.sh
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

if [ ! -x "$PYTHON" ]; then
  echo "Missing executable Python interpreter: $PYTHON" >&2
  exit 1
fi

PIN_UIDS=$(
  JSON_PATH="$JSON_PATH" SAMPLE_SIZE="$SAMPLE_SIZE" RANDOM_SEED="$RANDOM_SEED" LIMIT="$LIMIT" "$PYTHON" - <<'PY'
import json
import os
import random
import sys
from pathlib import Path


def non_negative_int(name: str, *, allow_empty: bool = False) -> int | None:
    text = os.environ.get(name, "").strip()
    if allow_empty and not text:
        return None
    try:
        value = int(text)
    except ValueError:
        print(f"{name} must be an integer, got: {text}", file=sys.stderr)
        sys.exit(2)
    if value < 0:
        print(f"{name} must be >= 0, got: {value}", file=sys.stderr)
        sys.exit(2)
    return value


json_path = Path(os.environ["JSON_PATH"])
sample_size = non_negative_int("SAMPLE_SIZE")
limit = non_negative_int("LIMIT", allow_empty=True)
seed_text = os.environ["RANDOM_SEED"]

with json_path.open("r", encoding="utf-8") as fh:
    records = json.load(fh)

if not isinstance(records, list):
    print(f"JSON must contain a list of records: {json_path}", file=sys.stderr)
    sys.exit(2)

# dict preserves first-seen order before the seeded random sample is taken.
unique_uids = list(
    dict.fromkeys(
        pin_uid
        for record in records
        if isinstance(record, dict)
        for pin_uid in [str(record.get("pin_uid", "")).strip()]
        if pin_uid.isdigit()
    )
)

if not unique_uids:
    print(f"No numeric pin_uid found in: {json_path}", file=sys.stderr)
    sys.exit(1)

rng = random.Random(seed_text)
selected = rng.sample(unique_uids, min(sample_size, len(unique_uids)))
if limit is not None:
    selected = selected[:limit]

for pin_uid in selected:
    print(pin_uid)
PY
)

if [ -z "$PIN_UIDS" ]; then
  echo "No pin_uid selected (SAMPLE_SIZE=$SAMPLE_SIZE, LIMIT=${LIMIT:-unset})." >&2
  exit 1
fi

pin_count=$(printf '%s\n' "$PIN_UIDS" | wc -l | awk '{print $1}')
echo "Selected $pin_count unique pin_uid(s) (sample size: $SAMPLE_SIZE, seed: $RANDOM_SEED)"

failures=0

for PIN_UID in $PIN_UIDS
do
  echo "=== Crawling related images for pin_uid: $PIN_UID (-n $NUM) ==="

  if [ "$DRY_RUN" = "1" ]; then
    printf '%s\n' \
      "\"$PYTHON\" crawl_pinterest_related_images.py \"$PIN_UID\" --mode pin --ipv4 -n $NUM -o \"$OUTPUT_DIR\" --json-path \"$JSON_PATH\" --cookies \"$COOKIES\" $*"
    echo "=== Dry-run finished pin_uid: $PIN_UID ==="
    continue
  fi

  if "$PYTHON" crawl_pinterest_related_images.py "$PIN_UID" \
    --mode pin \
    --ipv4 \
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
