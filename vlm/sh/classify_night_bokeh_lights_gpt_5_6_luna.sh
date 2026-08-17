#!/usr/bin/env bash

set -euo pipefail

VLM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

INPUT_DIR="${INPUT_DIR:-/mnt/aigc/houyuanchen/pinterest-dl/light_repo/pin_657595983150761272_related}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$VLM_DIR/prompts/night_bokeh_lights.zh.md}"
PROMPT_PROFILE="${PROMPT_PROFILE:-$VLM_DIR/prompts/night_bokeh_lights.json}"
API_CONFIG="${API_CONFIG:-$VLM_DIR/api/gpt_5_6_luna.json}"
OUTPUT_DIR="${OUTPUT_DIR:-$VLM_DIR/output/gpt_5_6_luna_pin_657595983150761272_related_night_bokeh_lights}"
WORKERS="${WORKERS:-200}"
TIMEOUT="${TIMEOUT:-180}"
RETRIES="${RETRIES:-3}"
MIN_SCORE="${MIN_SCORE:-75}"

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "Input directory does not exist: $INPUT_DIR" >&2
    exit 2
fi

if [[ ! -f "$SYSTEM_PROMPT" ]]; then
    echo "System prompt does not exist: $SYSTEM_PROMPT" >&2
    exit 2
fi

if [[ ! -f "$PROMPT_PROFILE" ]]; then
    echo "Prompt profile does not exist: $PROMPT_PROFILE" >&2
    exit 2
fi

if [[ ! -f "$API_CONFIG" ]]; then
    echo "API config does not exist: $API_CONFIG" >&2
    exit 2
fi

if ! [[ "$WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    echo "WORKERS must be a positive integer: $WORKERS" >&2
    exit 2
fi

if ! [[ "$MIN_SCORE" =~ ^[0-9]+$ ]] || ((MIN_SCORE < 0 || MIN_SCORE > 100)); then
    echo "MIN_SCORE must be an integer between 0 and 100: $MIN_SCORE" >&2
    exit 2
fi

exec "$PYTHON_BIN" "$VLM_DIR/src/filter_directory.py" \
    "$INPUT_DIR" \
    --prompt "$PROMPT_PROFILE" \
    --api-config "$API_CONFIG" \
    --output-dir "$OUTPUT_DIR" \
    --workers "$WORKERS" \
    --timeout "$TIMEOUT" \
    --retries "$RETRIES" \
    --min-score "$MIN_SCORE" \
    --copy-passed \
    "$@"
