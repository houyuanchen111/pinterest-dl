#!/usr/bin/env bash

set -euo pipefail

VLM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAGGER="$VLM_DIR/src/tag_s3_jsonl_images.py"

INPUT_JSONL="${INPUT_JSONL:-$VLM_DIR/data/human_sample_50k.jsonl}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$VLM_DIR/prompts/image_vqa_scene_tags.zh.md}"
API_CONFIG="${API_CONFIG:-$VLM_DIR/api/gpt_5_6_luna.json}"
AOSS_CONFIG="${AOSS_CONFIG:-/mnt/aigc/houyuanchen/aoss_v2.conf}"
CLUSTER="${CLUSTER:-malai}"
OUTPUT_DIR="${OUTPUT_DIR:-$VLM_DIR/output/human_sample_50k_gpt_5_6_luna_1mp}"
WORKERS="${WORKERS:-200}"
TIMEOUT="${TIMEOUT:-180}"
RETRIES="${RETRIES:-3}"
TARGET_MEGAPIXELS="${TARGET_MEGAPIXELS:-1.0}"
PYTHON_BIN="${PYTHON_BIN:-python}"

for required_file in \
    "$TAGGER" \
    "$INPUT_JSONL" \
    "$SYSTEM_PROMPT" \
    "$API_CONFIG" \
    "$AOSS_CONFIG"; do
    if [[ ! -f "$required_file" ]]; then
        echo "Required file does not exist: $required_file" >&2
        exit 2
    fi
done

if ! [[ "$WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    echo "WORKERS must be a positive integer: $WORKERS" >&2
    exit 2
fi

mkdir -p "$OUTPUT_DIR"
RUN_LOG="$OUTPUT_DIR/run.log"
RUN_INFO="$OUTPUT_DIR/run.info"
RESULTS_JSONL="$OUTPUT_DIR/results.jsonl"
ERRORS_JSONL="$OUTPUT_DIR/errors.jsonl"
STARTED_AT="$(date --iso-8601=seconds)"
: >"$RUN_LOG"

cat >"$RUN_INFO" <<EOF
started_at=$STARTED_AT
input_jsonl=$INPUT_JSONL
system_prompt=$SYSTEM_PROMPT
api_config=$API_CONFIG
aoss_config=$AOSS_CONFIG
cluster=$CLUSTER
output_dir=$OUTPUT_DIR
results_jsonl=$RESULTS_JSONL
errors_jsonl=$ERRORS_JSONL
workers=$WORKERS
timeout=$TIMEOUT
retries=$RETRIES
target_megapixels=$TARGET_MEGAPIXELS
EOF

echo "Starting GPT-5.6 Luna tagging with $WORKERS workers."
echo "Results: $RESULTS_JSONL"
echo "Log: $RUN_LOG"

set +e
"$PYTHON_BIN" -u "$TAGGER" \
    --input-jsonl "$INPUT_JSONL" \
    --system-prompt "$SYSTEM_PROMPT" \
    --api-config "$API_CONFIG" \
    --aoss-config "$AOSS_CONFIG" \
    --cluster "$CLUSTER" \
    --output-dir "$OUTPUT_DIR" \
    --workers "$WORKERS" \
    --timeout "$TIMEOUT" \
    --retries "$RETRIES" \
    --target-megapixels "$TARGET_MEGAPIXELS" \
    2>&1 | tee -a "$RUN_LOG"
STATUS="${PIPESTATUS[0]}"
set -e

FINISHED_AT="$(date --iso-8601=seconds)"
RESULT_COUNT="$(wc -l <"$RESULTS_JSONL" 2>/dev/null || echo 0)"
ERROR_COUNT="$(wc -l <"$ERRORS_JSONL" 2>/dev/null || echo 0)"

cat >>"$RUN_INFO" <<EOF
finished_at=$FINISHED_AT
result_count=$RESULT_COUNT
error_count=$ERROR_COUNT
exit_status=$STATUS
EOF

echo "Finished with status=$STATUS results=$RESULT_COUNT errors=$ERROR_COUNT"
exit "$STATUS"
