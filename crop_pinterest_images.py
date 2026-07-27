#!/usr/bin/env python3
"""Crop the bottom of Pinterest images and resize to 32-pixel multiples."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from tqdm import tqdm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--crop-bottom-ratio", type=float, default=0.09)
    parser.add_argument("--multiple", type=int, default=32)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(128, os.cpu_count() or 1),
        help="Number of image-processing worker processes (default: min(128, CPU count))",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Reprocess valid existing output images"
    )
    return parser.parse_args()


def _save_image(image: Image.Image, destination: Path, image_format: str | None) -> None:
    image_format = (image_format or destination.suffix.removeprefix(".")).upper()
    if image_format == "JPG":
        image_format = "JPEG"

    save_kwargs: dict[str, Any] = {}
    if image_format == "JPEG":
        if image.mode not in ("RGB", "L"):
            background = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image.convert("RGB"))
            image = background
        save_kwargs.update(quality=95, subsampling=0, optimize=True)
    elif image_format == "WEBP":
        save_kwargs.update(quality=95, method=4)
    elif image_format == "PNG":
        save_kwargs.update(optimize=True, compress_level=6)

    temporary = destination.with_name(
        f".{destination.stem}.tmp-{os.getpid()}{destination.suffix}"
    )
    try:
        image.save(temporary, format=image_format, **save_kwargs)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def process_one(task: tuple[str, str, float, int, bool]) -> dict[str, Any]:
    source_text, destination_text, crop_ratio, multiple, overwrite = task
    source = Path(source_text)
    destination = Path(destination_text)
    result: dict[str, Any] = {
        "original_image_path": str(source),
        "cropped_image_path": str(destination),
    }

    try:
        if not source.is_file():
            raise FileNotFoundError(f"source image does not exist: {source}")

        with Image.open(source) as opened:
            image_format = opened.format
            image = ImageOps.exif_transpose(opened)
            image.load()

        width, height = image.size
        crop_bottom_pixels = max(1, int(round(height * crop_ratio)))
        cropped_height = height - crop_bottom_pixels
        target_width = (width // multiple) * multiple
        target_height = (cropped_height // multiple) * multiple
        if target_width < multiple or target_height < multiple:
            raise ValueError(
                f"image is too small after cropping: original={width}x{height}, "
                f"cropped={width}x{cropped_height}, multiple={multiple}"
            )

        expected_size = (target_width, target_height)
        if destination.is_file() and not overwrite:
            try:
                with Image.open(destination) as existing:
                    if existing.size == expected_size:
                        result.update(
                            status="skipped_existing",
                            original_size=[width, height],
                            cropped_size=[width, cropped_height],
                            output_size=[target_width, target_height],
                            crop_bottom_pixels=crop_bottom_pixels,
                        )
                        return result
            except Exception:
                pass

        cropped = image.crop((0, 0, width, cropped_height))
        if cropped.size != expected_size:
            cropped = cropped.resize(expected_size, Image.Resampling.LANCZOS)

        destination.parent.mkdir(parents=True, exist_ok=True)
        _save_image(cropped, destination, image_format)
        result.update(
            status="processed",
            original_size=[width, height],
            cropped_size=[width, cropped_height],
            output_size=[target_width, target_height],
            crop_bottom_pixels=crop_bottom_pixels,
        )
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
    return result


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> int:
    args = parse_args()
    if not 0 < args.crop_bottom_ratio < 1:
        raise SystemExit("--crop-bottom-ratio must be between 0 and 1")
    if args.multiple <= 0:
        raise SystemExit("--multiple must be positive")
    if args.workers <= 0:
        raise SystemExit("--workers must be positive")

    input_json = args.input_json.resolve()
    output_dir = args.output_dir.resolve()
    output_json = args.output_json.resolve()
    with input_json.open(encoding="utf-8") as file:
        records = json.load(file)
    if not isinstance(records, list):
        raise SystemExit("input JSON must contain a top-level list")

    output_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, float, int, bool]] = []
    destinations: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("image_path"), str):
            source = ""
            destination = output_dir / f"invalid_record_{index:08d}.jpg"
        else:
            source = record["image_path"]
            destination = output_dir / Path(source).name
        destinations.append(str(destination))
        tasks.append(
            (source, str(destination), args.crop_bottom_ratio, args.multiple, args.overwrite)
        )

    chunksize = max(1, math.ceil(len(tasks) / max(1, args.workers * 32)))
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        processed = list(
            tqdm(
                executor.map(process_one, tasks, chunksize=chunksize),
                total=len(tasks),
                desc="Cropping/resizing",
                unit="image",
                dynamic_ncols=True,
            )
        )

    output_records = []
    for record, result, destination in zip(records, processed, destinations):
        output_record = dict(record) if isinstance(record, dict) else {"original_record": record}
        output_record["original_image_path"] = (
            record.get("image_path") if isinstance(record, dict) else None
        )
        output_record["cropped_image_path"] = destination
        output_record["crop_resize"] = {
            key: value
            for key, value in result.items()
            if key not in {"original_image_path", "cropped_image_path"}
        }
        output_records.append(output_record)

    atomic_write_json(output_json, output_records)
    counts: dict[str, int] = {}
    for result in processed:
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
    print(f"Input records: {len(records)}")
    print(f"Status counts: {counts}")
    print(f"Output directory: {output_dir}")
    print(f"Output JSON: {output_json}")
    return 1 if counts.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
