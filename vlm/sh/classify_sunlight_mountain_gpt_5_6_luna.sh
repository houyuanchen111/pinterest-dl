#!/usr/bin/env bash

set -euo pipefail

VLM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$VLM_DIR/sh/$(basename "${BASH_SOURCE[0]}")"
CLASSIFIER="$VLM_DIR/src/classify_image.py"

DEFAULT_INPUT_DIR="/mnt/aigc/houyuanchen/pinterest-dl/light_repo/sunlight_mountain/sunlight_mountain_merged"
DEFAULT_API_CONFIG="$VLM_DIR/api/gpt_5_6_luna.json"
DEFAULT_OUTPUT_DIR="$VLM_DIR/output/gpt_5_6_luna_sunlight_mountain"

run_worker() {
    local image_path="$1"
    local output_dir="$2"
    local api_config="$3"
    local timeout="$4"
    local retries="$5"
    local result_dir="$output_dir/results"
    local error_dir="$output_dir/errors"
    local temp_dir="$output_dir/tmp"
    local events_log="$output_dir/events.log"
    local image_name
    local result_path
    local error_path
    local temp_result
    local temp_error
    local normalized_result
    local classifier_image
    local preprocessed_image=""
    local image_suffix
    local image_size

    image_name="$(basename "$image_path")"
    result_path="$result_dir/$image_name.json"
    error_path="$error_dir/$image_name.stderr"

    if [[ -s "$result_path" ]] && jq -e . "$result_path" >/dev/null 2>&1; then
        printf '%s\tSKIP\t%s\n' "$(date '+%F %T')" "$image_path" >>"$events_log"
        return 0
    fi

    temp_result="$(mktemp "$temp_dir/${image_name}.XXXXXX.result")"
    temp_error="$(mktemp "$temp_dir/${image_name}.XXXXXX.error")"
    normalized_result="$(mktemp "$temp_dir/${image_name}.XXXXXX.normalized")"
    classifier_image="$image_path"
    image_suffix="${image_name##*.}"
    image_suffix="${image_suffix,,}"
    image_size="$(stat -c '%s' "$image_path")"

    if [[ "$image_suffix" != "jpg" && "$image_suffix" != "jpeg" && "$image_suffix" != "png" ]] \
        || [[ "$image_size" -ge 7000000 ]]; then
        preprocessed_image="$(mktemp "$temp_dir/${image_name}.XXXXXX.jpg")"
        if ! python3 - "$image_path" "$preprocessed_image" 2>"$temp_error" <<'PY'
from pathlib import Path
import sys

from PIL import Image

source = Path(sys.argv[1])
target = Path(sys.argv[2])

with Image.open(source) as image:
    image.seek(0)
    image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    image.save(target, "JPEG", quality=90, optimize=True)
PY
        then
            rm -f "$temp_result" "$normalized_result" "$preprocessed_image"
            mv "$temp_error" "$error_path"
            printf '%s\tFAIL\t%s\n' "$(date '+%F %T')" "$image_path" >>"$events_log"
            return 1
        fi
        classifier_image="$preprocessed_image"
        : >"$temp_error"
    fi

    if python3 "$CLASSIFIER" \
        "$classifier_image" \
        --prompt sunlight_mountain \
        --api-config "$api_config" \
        --timeout "$timeout" \
        --retries "$retries" \
        --output "$temp_result" \
        >/dev/null 2>"$temp_error"; then
        if jq --arg image "$(realpath "$image_path")" '.image = $image' \
            "$temp_result" >"$normalized_result" \
            && [[ -s "$normalized_result" ]] \
            && jq -e . "$normalized_result" >/dev/null 2>&1; then
            mv "$normalized_result" "$result_path"
            if [[ -s "$result_path" ]] && jq -e . "$result_path" >/dev/null 2>&1; then
                rm -f "$temp_result" "$temp_error" "$error_path"
                if [[ -n "$preprocessed_image" ]]; then
                    rm -f "$preprocessed_image"
                fi
                printf '%s\tOK\t%s\n' "$(date '+%F %T')" "$image_path" >>"$events_log"
                return 0
            fi
        fi
        printf 'Result normalization or atomic write produced invalid JSON.\n' >>"$temp_error"
    fi

    rm -f "$temp_result" "$normalized_result" "$result_path"
    if [[ -n "$preprocessed_image" ]]; then
        rm -f "$preprocessed_image"
    fi
    mv "$temp_error" "$error_path"
    printf '%s\tFAIL\t%s\n' "$(date '+%F %T')" "$image_path" >>"$events_log"
    return 1
}

if [[ "${1:-}" == "--worker" ]]; then
    shift
    run_worker "$@"
    exit $?
fi

INPUT_DIR="${INPUT_DIR:-$DEFAULT_INPUT_DIR}"
API_CONFIG="${API_CONFIG:-$DEFAULT_API_CONFIG}"
OUTPUT_DIR="${OUTPUT_DIR:-$DEFAULT_OUTPUT_DIR}"
CONCURRENCY="${CONCURRENCY:-200}"
TIMEOUT="${TIMEOUT:-180}"
RETRIES="${RETRIES:-3}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-10}"

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "Input directory does not exist: $INPUT_DIR" >&2
    exit 2
fi
if [[ ! -f "$API_CONFIG" ]]; then
    echo "API config does not exist: $API_CONFIG" >&2
    exit 2
fi
if ! [[ "$CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
    echo "CONCURRENCY must be a positive integer: $CONCURRENCY" >&2
    exit 2
fi

mkdir -p "$OUTPUT_DIR/results" "$OUTPUT_DIR/errors" "$OUTPUT_DIR/tmp"
touch "$OUTPUT_DIR/events.log" "$OUTPUT_DIR/progress.log"

IMAGE_LIST="$OUTPUT_DIR/images.list0"
find "$INPUT_DIR" -maxdepth 1 -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) \
    -print0 | sort -z >"$IMAGE_LIST"

TOTAL="$(tr -cd '\0' <"$IMAGE_LIST" | wc -c)"
if [[ "$TOTAL" -eq 0 ]]; then
    echo "No PNG/JPEG/JPG images found in: $INPUT_DIR" >&2
    exit 2
fi

STARTED_AT="$(date --iso-8601=seconds)"
cat >"$OUTPUT_DIR/run.info" <<EOF
started_at=$STARTED_AT
input_dir=$INPUT_DIR
prompt=sunlight_mountain
api_config=$API_CONFIG
output_dir=$OUTPUT_DIR
concurrency=$CONCURRENCY
timeout=$TIMEOUT
retries=$RETRIES
extensions=jpg,jpeg,png
total=$TOTAL
EOF

printf '%s\tSTART\ttotal=%s concurrency=%s\n' \
    "$(date '+%F %T')" "$TOTAL" "$CONCURRENCY" >>"$OUTPUT_DIR/events.log"

set +e
xargs -0 -r -P "$CONCURRENCY" -I '{}' \
    "$SCRIPT_PATH" --worker \
    '{}' "$OUTPUT_DIR" "$API_CONFIG" "$TIMEOUT" "$RETRIES" \
    <"$IMAGE_LIST" &
XARGS_PID=$!

while kill -0 "$XARGS_PID" 2>/dev/null; do
    COMPLETED="$(find "$OUTPUT_DIR/results" -maxdepth 1 -type f -name '*.json' -size +0c | wc -l)"
    FAILED="$(find "$OUTPUT_DIR/errors" -maxdepth 1 -type f -name '*.stderr' | wc -l)"
    REMAINING=$((TOTAL - COMPLETED - FAILED))
    if [[ "$REMAINING" -lt 0 ]]; then
        REMAINING=0
    fi
    printf '%s total=%s completed=%s failed=%s remaining=%s\n' \
        "$(date '+%F %T')" "$TOTAL" "$COMPLETED" "$FAILED" "$REMAINING" \
        | tee -a "$OUTPUT_DIR/progress.log"
    sleep "$PROGRESS_INTERVAL"
done

wait "$XARGS_PID"
XARGS_STATUS=$?
set -e

COMPLETED="$(find "$OUTPUT_DIR/results" -maxdepth 1 -type f -name '*.json' -size +0c | wc -l)"
FAILED="$(find "$OUTPUT_DIR/errors" -maxdepth 1 -type f -name '*.stderr' | wc -l)"
FINISHED_AT="$(date --iso-8601=seconds)"

find "$OUTPUT_DIR/results" -maxdepth 1 -type f -name '*.json' -size +0c -print0 \
    | sort -z \
    | xargs -0 -r -n 1 jq -c . \
    >"$OUTPUT_DIR/summary.jsonl"

rm -rf "$OUTPUT_DIR/passed"
mkdir -p "$OUTPUT_DIR/passed"
jq -r 'select(.all_pass == true) | .image' "$OUTPUT_DIR/summary.jsonl" \
    >"$OUTPUT_DIR/passed.list"
while IFS= read -r image_path; do
    [[ -n "$image_path" ]] || continue
    cp -p -- "$image_path" "$OUTPUT_DIR/passed/"
done <"$OUTPUT_DIR/passed.list"
PASSED="$(find "$OUTPUT_DIR/passed" -maxdepth 1 -type f | wc -l)"

cat >>"$OUTPUT_DIR/run.info" <<EOF
finished_at=$FINISHED_AT
completed=$COMPLETED
failed=$FAILED
passed=$PASSED
xargs_status=$XARGS_STATUS
EOF

printf '%s\tFINISH\ttotal=%s completed=%s failed=%s passed=%s status=%s\n' \
    "$(date '+%F %T')" "$TOTAL" "$COMPLETED" "$FAILED" "$PASSED" "$XARGS_STATUS" \
    | tee -a "$OUTPUT_DIR/events.log" "$OUTPUT_DIR/progress.log"

if [[ "$FAILED" -ne 0 || "$COMPLETED" -ne "$TOTAL" ]]; then
    exit 1
fi
