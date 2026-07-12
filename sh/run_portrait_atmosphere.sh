#!/usr/bin/env sh
set -eu

cd /mnt/aigc/houyuanchen/pinterest-dl

python crawl_pinterest_related_images.py "人像摄影 氛围感" \
  -n 1000 \
  -o /mnt/aigc/houyuanchen/pinterest-dl/pinterest \
  --json-path /mnt/aigc/houyuanchen/pinterest-dl/pinterest/pinterest_images.json \
  --cookies /mnt/aigc/houyuanchen/pinterest-dl/cookies.json \
  "$@"
