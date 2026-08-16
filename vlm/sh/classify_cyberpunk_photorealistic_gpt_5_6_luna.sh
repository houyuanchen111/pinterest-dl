#!/usr/bin/env bash

set -euo pipefail

VLM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

INPUT_DIR="${INPUT_DIR:-/mnt/aigc/houyuanchen/pinterest-dl/light_repo/visual_keywords/cyberpunk_neon_portrait_merged}"
PROMPT_FILE="${PROMPT_FILE:-$VLM_DIR/prompts/cyberpunk_photorealistic.zh.md}"
API_CONFIG="${API_CONFIG:-$VLM_DIR/api/gpt_5_6_luna.json}"
OUTPUT_DIR="${OUTPUT_DIR:-$VLM_DIR/output/gpt_5_6_luna_cyberpunk_photorealistic}"
WORKERS="${WORKERS:-64}"
TIMEOUT="${TIMEOUT:-180}"
RETRIES="${RETRIES:-3}"

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "Input directory does not exist: $INPUT_DIR" >&2
    exit 2
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "Prompt file does not exist: $PROMPT_FILE" >&2
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

exec "$PYTHON_BIN" "$VLM_DIR/src/filter_directory.py" \
    "$INPUT_DIR" \
    --prompt "$PROMPT_FILE" \
    --api-config "$API_CONFIG" \
    --output-dir "$OUTPUT_DIR" \
    --workers "$WORKERS" \
    --timeout "$TIMEOUT" \
    --retries "$RETRIES" \
    --min-score 0 \
    --copy-passed \
    "$@"
