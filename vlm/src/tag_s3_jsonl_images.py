#!/usr/bin/env python3
"""Run VQA tagging on images referenced by an S3 or local JSONL file."""

from __future__ import annotations

import argparse
import base64
import configparser
import io
import json
import random
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import boto3
from botocore.config import Config
from PIL import Image, ImageOps

from classify_image import (
    ClassificationError,
    classify_with_retries,
    load_api_config,
    load_prompt_profile,
)


SCRIPT_DIR = Path(__file__).resolve().parent
VLM_DIR = SCRIPT_DIR.parent
DEFAULT_INPUT_JSONL = VLM_DIR / "data" / "human_sample_50k.jsonl"
DEFAULT_API_CONFIG = VLM_DIR / "api" / "gpt_5_6_luna.json"
DEFAULT_AOSS_CONFIG = Path("/mnt/aigc/houyuanchen/aoss_v2.conf")
DEFAULT_SYSTEM_PROMPT = VLM_DIR / "prompts" / "image_vqa_scene_tags.zh.md"
DEFAULT_OUTPUT_DIR = VLM_DIR / "output" / "image_vqa_scene_tags"
DEFAULT_SMOKE_DIR = Path(
    "/mnt/aigc/houyuanchen/pinterest-dl/tmp/image_vqa_scene_tags_smoke5"
)
DEFAULT_TARGET_MEGAPIXELS = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tag S3-backed images listed in a JSONL file with a VLM."
    )
    parser.add_argument("--input-jsonl", type=Path, default=DEFAULT_INPUT_JSONL)
    parser.add_argument("--system-prompt", type=Path, default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--api-config", type=Path, default=DEFAULT_API_CONFIG)
    parser.add_argument("--aoss-config", type=Path, default=DEFAULT_AOSS_CONFIG)
    parser.add_argument("--cluster", default="malai")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--target-megapixels",
        type=float,
        default=DEFAULT_TARGET_MEGAPIXELS,
        help="Resize each image proportionally to approximately this many megapixels.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N records. Use 0 for all records.",
    )
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument(
        "--smoke-dir",
        type=Path,
        default=DEFAULT_SMOKE_DIR,
        help="Directory for copied smoke-test images and result JSONL.",
    )
    parser.add_argument(
        "--save-images",
        action="store_true",
        help="Save resized images into --smoke-dir.",
    )
    return parser.parse_args()


def load_records(path: Path, limit: int, seed: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"Line {line_number} is not a JSON object")
            if not isinstance(record.get("image"), str):
                raise ValueError(f"Line {line_number} has no string image field")
            record["_input_line"] = line_number
            records.append(record)

    if limit < 0:
        raise ValueError("--limit must be zero or positive")
    if limit and len(records) > limit:
        random.Random(seed).shuffle(records)
        records = records[:limit]
    return records


def make_s3_client(config_path: Path, cluster: str):
    config = configparser.ConfigParser()
    config.read(config_path)
    if cluster not in config:
        raise ValueError(f"Cluster section not found: {cluster}")
    section = config[cluster]
    endpoint = section.get("host_base")
    access_key = section.get("access_key")
    secret_key = section.get("secret_key")
    if not endpoint or not access_key or not secret_key:
        raise ValueError(f"Incomplete AOSS configuration for cluster: {cluster}")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            connect_timeout=30,
            read_timeout=120,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Expected an s3:// URI, got: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def load_image_bytes(image_uri: str, client: Any | None) -> bytes:
    parsed = urlparse(image_uri)
    if parsed.scheme == "s3":
        if client is None:
            raise ValueError("S3 client is required for s3:// images")
        bucket, key = parse_s3_uri(image_uri)
        response = client.get_object(Bucket=bucket, Key=key)
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    if parsed.scheme == "file":
        image_path = Path(unquote(parsed.path))
    elif not parsed.scheme:
        image_path = Path(image_uri).expanduser()
    else:
        raise ValueError(f"Unsupported image URI scheme: {image_uri}")
    return image_path.read_bytes()


def image_data_url(image_bytes: bytes, media_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def resize_image(
    image_bytes: bytes,
    target_megapixels: float,
) -> tuple[bytes, str, tuple[int, int], tuple[int, int]]:
    target_pixels = round(target_megapixels * 1_000_000)
    with Image.open(io.BytesIO(image_bytes)) as source:
        source.seek(0)
        image = ImageOps.exif_transpose(source)
        source_size = image.size
        source_pixels = source_size[0] * source_size[1]
        if source_pixels <= 0:
            raise ValueError(f"Invalid image dimensions: {source_size}")

        scale = (target_pixels / source_pixels) ** 0.5
        resized_size = (
            max(1, round(source_size[0] * scale)),
            max(1, round(source_size[1] * scale)),
        )
        if resized_size != source_size:
            image = image.resize(resized_size, Image.Resampling.LANCZOS)
        else:
            image = image.copy()

        output = io.BytesIO()
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            image.save(output, format="PNG", optimize=True)
            media_type = "image/png"
        else:
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(output, format="JPEG", quality=90, optimize=True)
            media_type = "image/jpeg"

    return output.getvalue(), media_type, source_size, resized_size


def safe_name(record: dict[str, Any], index: int) -> str:
    record_id = str(record.get("id") or f"line_{record.get('_input_line', index)}")
    record_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", record_id)
    suffix = Path(urlparse(str(record["image"])).path).suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".jpg"
    return f"{index:05d}_{record_id}{suffix}"


def tag_one(
    index: int,
    record: dict[str, Any],
    client: Any,
    api: Any,
    profile: Any,
    user_prompt: str,
    timeout: float,
    retries: int,
    target_megapixels: float,
    save_images: bool,
    smoke_dir: Path,
) -> dict[str, Any]:
    image_uri = str(record["image"])
    image_bytes = load_image_bytes(image_uri, client)
    if not image_bytes:
        raise ValueError(f"Empty image: {image_uri}")

    resized_bytes, media_type, source_size, resized_size = resize_image(
        image_bytes,
        target_megapixels,
    )

    if save_images:
        smoke_dir.mkdir(parents=True, exist_ok=True)
        image_path = smoke_dir / safe_name(record, index)
        image_path = image_path.with_suffix(".png" if media_type == "image/png" else ".jpg")
        image_path.write_bytes(resized_bytes)

    result = classify_with_retries(
        api=api,
        profile=profile,
        image_url=image_data_url(resized_bytes, media_type),
        user_prompt=user_prompt,
        timeout=timeout,
        retries=retries,
    )
    return {
        "index": index,
        "input_line": record.get("_input_line"),
        "id": record.get("id"),
        "image": image_uri,
        "model": api.model,
        "prompt": str(profile.source),
        "source_size": list(source_size),
        "resized_size": list(resized_size),
        "tags": result,
    }


def main() -> int:
    args = parse_args()
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.retries <= 0:
        raise ValueError("--retries must be positive")
    if args.target_megapixels <= 0:
        raise ValueError("--target-megapixels must be positive")
    if not args.input_jsonl.is_file():
        raise FileNotFoundError(args.input_jsonl)
    if not args.system_prompt.is_file():
        raise FileNotFoundError(args.system_prompt)
    if not args.api_config.is_file():
        raise FileNotFoundError(args.api_config)

    api = load_api_config(args.api_config)
    profile = load_prompt_profile(str(args.system_prompt))
    records = load_records(args.input_jsonl, args.limit, args.seed)
    requires_s3 = any(urlparse(str(record["image"])).scheme == "s3" for record in records)
    if requires_s3:
        if not args.aoss_config.is_file():
            raise FileNotFoundError(args.aoss_config)
        client = make_s3_client(args.aoss_config, args.cluster)
    else:
        client = None
    user_prompt = (
        "请检查这张图片，并严格按照系统提示词完成视觉问答标注。"
        "只返回系统要求的合法 JSON 对象，不要输出其他文字。"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "results.jsonl"
    errors_path = args.output_dir / "errors.jsonl"
    if args.save_images:
        if args.smoke_dir.exists():
            shutil.rmtree(args.smoke_dir)
        args.smoke_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"records={len(records)} workers={args.workers} model={api.model} "
        f"target_megapixels={args.target_megapixels}",
        file=sys.stderr,
    )
    completed = 0
    errors = 0
    with (
        output_path.open("w", encoding="utf-8") as output,
        errors_path.open("w", encoding="utf-8") as error_output,
        ThreadPoolExecutor(max_workers=args.workers) as executor,
    ):
        futures = {
            executor.submit(
                tag_one,
                index,
                record,
                client,
                api,
                profile,
                user_prompt,
                args.timeout,
                args.retries,
                args.target_megapixels,
                args.save_images,
                args.smoke_dir,
            ): (index, record)
            for index, record in enumerate(records, 1)
        }
        for future in as_completed(futures):
            index, record = futures[future]
            try:
                result = future.result()
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                completed += 1
            except (ClassificationError, Exception) as exc:
                error = {
                    "index": index,
                    "input_line": record.get("_input_line"),
                    "id": record.get("id"),
                    "image": record.get("image"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                error_output.write(json.dumps(error, ensure_ascii=False) + "\n")
                errors += 1
            output.flush()
            error_output.flush()
            if (completed + errors) % 1 == 0:
                print(
                    f"completed={completed} errors={errors} total={len(records)}",
                    file=sys.stderr,
                )

    print(f"results={output_path}", file=sys.stderr)
    print(f"errors={errors_path}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
