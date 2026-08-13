#!/usr/bin/env bash

set -euo pipefail

VLM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 IMAGE_PATH [additional classify_image.py arguments]" >&2
    exit 2
fi

exec "$PYTHON_BIN" "$VLM_DIR/src/classify_image.py" "$@"
