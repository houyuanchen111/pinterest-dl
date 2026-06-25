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
  1970393584353961 \
  3659243441358592 \
  304555993575249592 \
  647111040223668195 \
  1900024839393304 \
  90635011247372039 \
  1337074890277864 \
  148126275239105306 \
  1027735577486334437 \
  2674081026366679 \
  108297566036224611 \
  563018699577799 \
  7599893113932429 \
  30047522504943139 \
  583779170491844600 \
  103301385198230928 \
  1337074890277849 \
  672162313158918914 \
  603834262582142534 \
  1015421047245062978
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
