#!/usr/bin/env python3
"""Batch-classify an image directory with a VLM prompt profile."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

from classify_image import (
    ClassificationError,
    SUPPORTED_SUFFIXES,
    classify_with_retries,
    image_data_url,
    load_api_config,
    load_prompt_profile,
    validate_image,
)


SCRIPT_DIR = Path(__file__).resolve().parent
VLM_DIR = SCRIPT_DIR.parent
DEFAULT_API_CONFIG = VLM_DIR / "api" / "qwen_3_5_plus.json"
MAX_DIRECT_IMAGE_BYTES = 9_500_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify all images in a directory, write resumable per-image JSON "
            "results, and optionally copy passing images."
        )
    )
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--prompt", default="backlit_floral_portrait")
    parser.add_argument("--api-config", type=Path, default=DEFAULT_API_CONFIG)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=VLM_DIR / "output" / "backlit_floral_portrait",
    )
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N images. Use 0 for all images.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=75,
        help="Minimum visual_match_score required when copying passing images.",
    )
    parser.add_argument(
        "--copy-passed",
        action="store_true",
        help="Copy passing images into OUTPUT_DIR/passed.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore valid cached per-image results and classify again.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {args.input_dir}")
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.retries <= 0:
        raise ValueError("--retries must be positive")
    if args.limit < 0:
        raise ValueError("--limit must be zero or positive")
    if not 0 <= args.min_score <= 100:
        raise ValueError("--min-score must be between 0 and 100")


def discover_images(root: Path, recursive: bool) -> list[Path]:
    paths = root.rglob("*") if recursive else root.iterdir()
    return sorted(
        path
        for path in paths
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def result_key(image_path: Path, input_dir: Path) -> str:
    relative = image_path.relative_to(input_dir).as_posix()
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]
    safe_name = relative.replace("/", "__")
    return f"{safe_name}.{digest}.json"


def load_cached_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def classifier_signature(api: Any, profile: Any) -> str:
    payload = json.dumps(
        {
            "model": api.model,
            "profile_id": profile.id,
            "system_prompt": profile.system_prompt,
            "user_prompt": profile.user_prompt,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def api_image_data_url(image_path: Path) -> str:
    media_type = validate_image(image_path)
    if image_path.stat().st_size < MAX_DIRECT_IMAGE_BYTES:
        return image_data_url(image_path, media_type)

    from PIL import Image

    with Image.open(image_path) as image:
        image.seek(0)
        image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")

        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90, optimize=True)

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def classify_one(
    image_path: Path,
    input_dir: Path,
    results_dir: Path,
    api: Any,
    profile: Any,
    timeout: float,
    retries: int,
    refresh: bool,
    signature: str,
) -> tuple[dict[str, Any], bool]:
    result_path = results_dir / result_key(image_path, input_dir)
    if not refresh:
        cached = load_cached_result(result_path)
        if cached is not None and cached.get("classifier_signature") == signature:
            return cached, True

    result = classify_with_retries(
        api=api,
        profile=profile,
        image_url=api_image_data_url(image_path),
        user_prompt=profile.user_prompt,
        timeout=timeout,
        retries=retries,
    )
    output = {
        **result,
        "image": str(image_path.resolve()),
        "model": api.model,
        "prompt": profile.id,
        "classifier_signature": signature,
    }
    write_json_atomic(result_path, output)
    return output, False


def score_of(result: dict[str, Any]) -> int:
    score = result.get("visual_match_score", 0)
    if type(score) is int and 0 <= score <= 100:
        return score
    return 0


def is_selected(result: dict[str, Any], min_score: int) -> bool:
    return result.get("all_pass") is True and score_of(result) >= min_score


def copy_selected(
    results: list[dict[str, Any]],
    input_dir: Path,
    passed_dir: Path,
    min_score: int,
) -> int:
    if passed_dir.exists():
        shutil.rmtree(passed_dir)
    passed_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for result in results:
        if not is_selected(result, min_score):
            continue
        source = Path(result["image"])
        relative = source.relative_to(input_dir.resolve())
        destination = passed_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    return copied


def write_summary(
    output_dir: Path,
    results: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> None:
    ranked = sorted(
        results,
        key=lambda item: (-score_of(item), str(item.get("image", ""))),
    )
    summary_path = output_dir / "summary.jsonl"
    summary_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ranked),
        encoding="utf-8",
    )
    errors_path = output_dir / "errors.jsonl"
    errors_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in errors),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        input_dir = args.input_dir.resolve()
        output_dir = args.output_dir.resolve()
        results_dir = output_dir / "results"
        output_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)

        images = discover_images(input_dir, args.recursive)
        if args.limit:
            images = images[: args.limit]
        if not images:
            raise ValueError(f"No supported images found in: {input_dir}")

        api = load_api_config(args.api_config)
        profile = load_prompt_profile(args.prompt)
        signature = classifier_signature(api, profile)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        cached_count = 0

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    classify_one,
                    image,
                    input_dir,
                    results_dir,
                    api,
                    profile,
                    args.timeout,
                    args.retries,
                    args.refresh,
                    signature,
                ): image
                for image in images
            }
            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Classifying",
            ):
                image = futures[future]
                try:
                    result, was_cached = future.result()
                    results.append(result)
                    cached_count += int(was_cached)
                except (OSError, ValueError, json.JSONDecodeError, ClassificationError) as exc:
                    errors.append({"image": str(image.resolve()), "error": str(exc)})

        write_summary(output_dir, results, errors)
        selected_count = sum(
            is_selected(result, args.min_score) for result in results
        )
        copied_count = 0
        if args.copy_passed:
            copied_count = copy_selected(
                results,
                input_dir,
                output_dir / "passed",
                args.min_score,
            )

        print(f"Images: {len(images)}")
        print(f"Completed: {len(results)}")
        print(f"Cached: {cached_count}")
        print(f"Failed: {len(errors)}")
        print(f"Selected (score >= {args.min_score}): {selected_count}")
        if args.copy_passed:
            print(f"Copied: {copied_count}")
        print(f"Summary: {output_dir / 'summary.jsonl'}")
        return 1 if errors else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
