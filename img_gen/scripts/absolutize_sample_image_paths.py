#!/usr/bin/env python3
"""Expand sampled JSONL image paths using the roots from human YAML files."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT_U2 = "s3://u2-generative/"
ROOT_IMAGE_0518 = "s3://infographics/vigeneval/data/supplier-data/baixing/image-0518/"
ROOT_IMAGE_0530 = "s3://infographics/vigeneval/data/supplier-data/baixing/image-0530/"
ROOT_IMAGE_0604 = "s3://infographics/vigeneval/data/"
ROOT_IMAGE_0611 = (
    "s3://infographics/vigeneval/data/supplier-data/baixing/image-0611/"
)
ROOT_AGG = "s3://human_images/agg_human/images/supplier_02/"
ROOT_BOYUE = "s3://human_images/boyue/boyue_human_260505/"


def resolve_root(image: str) -> str:
    if image.startswith("human/260515/"):
        return ROOT_U2
    if image.startswith("human/260522/"):
        return ROOT_U2
    if image.startswith("human/260529/"):
        return ROOT_U2
    if image.startswith("supplier-data/baixing/image-0604/"):
        return ROOT_IMAGE_0604
    if image.startswith("HU/MA/"):
        return ROOT_IMAGE_0611
    if image.startswith("batch"):
        return ROOT_AGG
    if image.startswith(("01.character_", "02.character_", "03.character_", "04.character_")):
        return ROOT_BOYUE
    if image.startswith("HUMAN"):
        return ROOT_IMAGE_0518
    if image.startswith("human/HUMAN"):
        return ROOT_IMAGE_0530
    raise ValueError(f"Cannot resolve image_root for image path: {image}")


def absolutize(input_path: Path, backup: bool) -> dict[str, int]:
    temp_path = input_path.with_suffix(input_path.suffix + ".tmp")
    if backup:
        backup_path = input_path.with_suffix(input_path.suffix + ".bak")
        shutil.copy2(input_path, backup_path)

    counts: dict[str, int] = {}
    total = 0
    with input_path.open(encoding="utf-8") as source, temp_path.open(
        "w", encoding="utf-8"
    ) as target:
        for line_number, line in enumerate(source, 1):
            record = json.loads(line)
            image = record.get("image")
            if not isinstance(image, str) or image.startswith("s3://"):
                raise ValueError(
                    f"Line {line_number} has an invalid or already absolute image: "
                    f"{image!r}"
                )
            root = resolve_root(image)
            record["image"] = root + image
            counts[root] = counts.get(root, 0) + 1
            target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            target.write("\n")
            total += 1

    temp_path.replace(input_path)
    counts["total"] = total
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input",
        type=Path,
        default=Path(
            "/mnt/aigc/houyuanchen/pinterest-dl/img_gen/data/human_sample_50k.jsonl"
        ),
        nargs="?",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    counts = absolutize(args.input, backup=not args.no_backup)
    for key, value in counts.items():
        print(f"{key}\t{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
