#!/usr/bin/env bash

set -euo pipefail

VLM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$VLM_DIR/sh/$(basename "${BASH_SOURCE[0]}")"
CLASSIFIER="$VLM_DIR/src/classify_image.py"

DEFAULT_SOURCE_SUMMARY="$VLM_DIR/output/gpt_5_6_luna_sky_sea/summary.jsonl"
DEFAULT_API_CONFIG="$VLM_DIR/api/gpt_5_6_luna.json"
DEFAULT_OUTPUT_DIR="$VLM_DIR/output/gpt_5_6_luna_sky_has_sun"

run_worker() {
    local image_path="$1"
    local output_dir="$2"
    local api_config="$3"
    local timeout="$4"
    local retries="$5"
    local python_bin="$6"
    local result_dir="$output_dir/results"
    local error_dir="$output_dir/errors"
    local temp_dir="$output_dir/tmp"
    local events_log="$output_dir/events.log"
    local image_name
    local result_path
    local error_path
    local temp_result
    local temp_error

    image_name="$(basename "$image_path")"
    result_path="$result_dir/$image_name.json"
    error_path="$error_dir/$image_name.stderr"

    if [[ -s "$result_path" ]] && jq -e . "$result_path" >/dev/null 2>&1; then
        printf '%s\tSKIP\t%s\n' "$(date '+%F %T')" "$image_path" >>"$events_log"
        return 0
    fi

    temp_result="$(mktemp "$temp_dir/${image_name}.XXXXXX.result")"
    temp_error="$(mktemp "$temp_dir/${image_name}.XXXXXX.error")"

    if "$python_bin" "$CLASSIFIER" \
        "$image_path" \
        --prompt sky_has_sun \
        --api-config "$api_config" \
        --timeout "$timeout" \
        --retries "$retries" \
        --output "$temp_result" \
        >/dev/null 2>"$temp_error"; then
        mv "$temp_result" "$result_path"
        rm -f "$temp_error" "$error_path"
        printf '%s\tOK\t%s\n' "$(date '+%F %T')" "$image_path" >>"$events_log"
        return 0
    fi

    rm -f "$temp_result"
    mv "$temp_error" "$error_path"
    printf '%s\tFAIL\t%s\n' "$(date '+%F %T')" "$image_path" >>"$events_log"
    return 1
}

if [[ "${1:-}" == "--worker" ]]; then
    shift
    run_worker "$@"
    exit $?
fi

SOURCE_SUMMARY="${SOURCE_SUMMARY:-$DEFAULT_SOURCE_SUMMARY}"
API_CONFIG="${API_CONFIG:-$DEFAULT_API_CONFIG}"
OUTPUT_DIR="${OUTPUT_DIR:-$DEFAULT_OUTPUT_DIR}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONCURRENCY="${CONCURRENCY:-200}"
TIMEOUT="${TIMEOUT:-180}"
RETRIES="${RETRIES:-3}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-10}"

if [[ ! -f "$SOURCE_SUMMARY" ]]; then
    echo "Source summary does not exist: $SOURCE_SUMMARY" >&2
    exit 2
fi
if [[ ! -f "$API_CONFIG" ]]; then
    echo "API config does not exist: $API_CONFIG" >&2
    exit 2
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python executable does not exist: $PYTHON_BIN" >&2
    exit 2
fi
if ! [[ "$CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
    echo "CONCURRENCY must be a positive integer: $CONCURRENCY" >&2
    exit 2
fi

mkdir -p "$OUTPUT_DIR/results" "$OUTPUT_DIR/errors" "$OUTPUT_DIR/tmp"
touch "$OUTPUT_DIR/events.log" "$OUTPUT_DIR/progress.log"

IMAGE_LIST="$OUTPUT_DIR/images.list0"
INVALID_LIST="$OUTPUT_DIR/invalid_images.list"
DUPLICATE_LIST="$OUTPUT_DIR/duplicate_basenames.list"
: >"$IMAGE_LIST"
: >"$INVALID_LIST"
: >"$DUPLICATE_LIST"

while IFS= read -r image_path; do
    if [[ -f "$image_path" ]] && [[ "$image_path" =~ \.(jpg|jpeg|png|JPG|JPEG|PNG)$ ]]; then
        printf '%s\0' "$image_path" >>"$IMAGE_LIST"
    else
        printf '%s\n' "$image_path" >>"$INVALID_LIST"
    fi
done < <(jq -r 'select(.all_pass == true) | .image' "$SOURCE_SUMMARY")

TOTAL="$(tr -cd '\0' <"$IMAGE_LIST" | wc -c)"
INVALID="$(wc -l <"$INVALID_LIST")"
if [[ "$INVALID" -ne 0 ]]; then
    echo "Found $INVALID missing or unsupported all_pass images; see: $INVALID_LIST" >&2
    exit 2
fi
if [[ "$TOTAL" -eq 0 ]]; then
    echo "No all_pass images found in: $SOURCE_SUMMARY" >&2
    exit 2
fi

tr '\0' '\n' <"$IMAGE_LIST" \
    | xargs -r -n 1 basename \
    | sort \
    | uniq -d \
    >"$DUPLICATE_LIST"
if [[ -s "$DUPLICATE_LIST" ]]; then
    echo "Duplicate image basenames would overwrite results; see: $DUPLICATE_LIST" >&2
    exit 2
fi

STARTED_AT="$(date --iso-8601=seconds)"
cat >"$OUTPUT_DIR/run.info" <<EOF
started_at=$STARTED_AT
source_summary=$SOURCE_SUMMARY
api_config=$API_CONFIG
output_dir=$OUTPUT_DIR
prompt=sky_has_sun
concurrency=$CONCURRENCY
timeout=$TIMEOUT
retries=$RETRIES
total=$TOTAL
EOF

printf '%s\tSTART\ttotal=%s concurrency=%s\n' \
    "$(date '+%F %T')" "$TOTAL" "$CONCURRENCY" >>"$OUTPUT_DIR/events.log"

set +e
xargs -0 -r -P "$CONCURRENCY" -I '{}' \
    "$SCRIPT_PATH" --worker \
    '{}' "$OUTPUT_DIR" "$API_CONFIG" "$TIMEOUT" "$RETRIES" "$PYTHON_BIN" \
    <"$IMAGE_LIST" &
XARGS_PID=$!

while kill -0 "$XARGS_PID" 2>/dev/null; do
    COMPLETED="$(find "$OUTPUT_DIR/results" -maxdepth 1 -type f -name '*.json' | wc -l)"
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

COMPLETED="$(find "$OUTPUT_DIR/results" -maxdepth 1 -type f -name '*.json' | wc -l)"
FAILED="$(find "$OUTPUT_DIR/errors" -maxdepth 1 -type f -name '*.stderr' | wc -l)"
FINISHED_AT="$(date --iso-8601=seconds)"

find "$OUTPUT_DIR/results" -maxdepth 1 -type f -name '*.json' -print0 \
    | sort -z \
    | xargs -0 -r -n 1 jq -c . \
    >"$OUTPUT_DIR/summary.jsonl"

PASSED="$(jq -s 'map(select(.all_pass == true)) | length' "$OUTPUT_DIR/summary.jsonl")"
REJECTED=$((COMPLETED - PASSED))

cat >>"$OUTPUT_DIR/run.info" <<EOF
finished_at=$FINISHED_AT
completed=$COMPLETED
failed=$FAILED
passed=$PASSED
rejected=$REJECTED
xargs_status=$XARGS_STATUS
EOF

printf '%s\tFINISH\ttotal=%s completed=%s failed=%s passed=%s rejected=%s status=%s\n' \
    "$(date '+%F %T')" "$TOTAL" "$COMPLETED" "$FAILED" \
    "$PASSED" "$REJECTED" "$XARGS_STATUS" \
    | tee -a "$OUTPUT_DIR/events.log" "$OUTPUT_DIR/progress.log"

if [[ "$FAILED" -ne 0 || "$COMPLETED" -ne "$TOTAL" ]]; then
    exit 1
fi
