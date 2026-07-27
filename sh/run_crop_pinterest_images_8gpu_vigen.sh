#!/usr/bin/env bash
set -euo pipefail

REPO=/mnt/aigc/houyuanchen/pinterest-dl
CONDA_ENV=/mnt/aigc/houyuanchen/miniconda3/envs/diffsynth_studio
PYTHON=$CONDA_ENV/bin/python
INPUT_JSON=$REPO/0715_pinterest_images_filtered.json
OUTPUT_DIR=$REPO/pinterest_crop
OUTPUT_JSON=$REPO/0715_pinterest_images_filtered_cropped.json
JOB_NAME=${JOB_NAME:-0715_crop_pinterest_images_8gpu}
# An 8-card N6lS node has substantially more CPU and memory than the 1-card job.
# 512 processes intentionally oversubscribe CPU to overlap AFS reads and image decoding.
WORKERS=${WORKERS:-512}
LOG_PATH=${LOG_PATH:-$REPO/0715_crop_pinterest_images_8gpu.log}

/root/.sco/bin/sco acp jobs create \
    --workspace-name=aigc \
    --aec2-name=vigen \
    --job-name="$JOB_NAME" \
    --priority=highest \
    --container-image-url='registry.ms-sc-01.maoshanwangtech.com/ms-ccr/zoe:20260319-23h56m41s' \
    --storage-mount='c83d08bc-2965-11ef-b8c5-929f74fd8884:/mnt/aigc/,047443d2-c3f2-11ee-a5f9-9e29792dec2f:/mnt/afs1/' \
    --training-framework=pytorch \
    --worker-nodes=1 \
    --worker-spec=N6lS.Iq.I10.8 \
    --command="set -euo pipefail; \
cd $REPO; \
export PYTHONUNBUFFERED=1; \
echo whoami=\$(whoami); \
echo python=$PYTHON; \
$PYTHON --version; \
$PYTHON -c \"from PIL import features; assert features.check('webp'), 'Pillow WebP support is unavailable'\"; \
echo workers=$WORKERS; \
$PYTHON $REPO/crop_pinterest_images.py \
  --input-json $INPUT_JSON \
  --output-dir $OUTPUT_DIR \
  --output-json $OUTPUT_JSON \
  --crop-bottom-ratio 0.09 \
  --multiple 32 \
  --workers $WORKERS \
  2>&1 | tee -a $LOG_PATH"
