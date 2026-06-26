#!/usr/bin/env sh
set -u

cd /mnt/aigc/houyuanchen/pinterest-dl

OUTPUT_DIR=/mnt/aigc/houyuanchen/pinterest-dl/pinterest
JSON_PATH=/mnt/aigc/houyuanchen/pinterest-dl/pinterest/pinterest_images.json
COOKIES=/mnt/aigc/houyuanchen/pinterest-dl/cookies.json

if [ ! -f "$COOKIES" ]; then
  echo "Missing cookies file: $COOKIES" >&2
  exit 1
fi

failures=0

for PIN_UID in \
  1092685928363116736 \
  1007750854116185194 \
  1970393584119249 \
  46021227439299869 \
  6051780746255246 \
  69383650506084236 \
  183451384834248826
do
  echo "=== Crawling pin_uid: $PIN_UID ==="
  if python crawl_pinterest_related_images.py "$PIN_UID" \
    --mode pin \
    -n 200 \
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
