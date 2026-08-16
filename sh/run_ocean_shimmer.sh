#!/usr/bin/env bash
set -euo pipefail

REPO_DIR=${REPO_DIR:-/mnt/aigc/houyuanchen/pinterest-dl}
OUTPUT_ROOT=${OUTPUT_ROOT:-$REPO_DIR/light_repo/ocean_shimmer}
PYTHON=${PYTHON:-/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio/bin/python}
NUM=${NUM:-1000}
RETRY_DELAY=${RETRY_DELAY:-30}

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing executable Python interpreter: $PYTHON" >&2
  exit 1
fi

cd "$REPO_DIR"
mkdir -p "$OUTPUT_ROOT"

run_query() {
  local slug=$1
  local query=$2
  local output_dir="$OUTPUT_ROOT/$slug"
  local json_path="$output_dir/pinterest_images.json"
  local status
  shift 2

  mkdir -p "$output_dir"
  echo "[$(date '+%F %T')] Starting: $query -> $output_dir"

  while true; do
    if PYTHONUNBUFFERED=1 "$PYTHON" search_atmosphere_images.py \
      --query "$query" \
      --all \
      --max-items "$NUM" \
      --output "$output_dir" \
      --cache "$json_path" \
      "$@"; then
      break
    else
      status=$?
    fi

    echo "[$(date '+%F %T')] Query '$query' exited with status $status; retrying in ${RETRY_DELAY}s..." >&2
    sleep "$RETRY_DELAY"
  done

  echo "[$(date '+%F %T')] Finished: $query"
}

run_query "ocean_shimmer_zh" "海洋 波光粼粼" "$@"
run_query "ocean_shimmer_en" "ocean shimmering water" "$@"

echo "[$(date '+%F %T')] All ocean shimmer keyword crawls completed."
