#!/usr/bin/env python3
"""Atomic GPT Image generation and editing CLI."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


PROJECT_DIR = Path(__file__).resolve().parents[1]
PROMPT_DIR = PROJECT_DIR / "prompt"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_API_CONFIG = PROJECT_DIR / "api" / "api.json"


class CliError(RuntimeError):
    """Raised for user-correctable CLI errors."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Send exactly one GPT Image API request, then save every returned image."
        )
    )
    parser.add_argument(
        "--user-prompt",
        required=True,
        help="User prompt Markdown path, or a name from prompt/user without .md.",
    )
    system_group = parser.add_mutually_exclusive_group()
    system_group.add_argument(
        "--sys-prompt",
        default="diversity",
        help="System-guidance Markdown path, or a name from prompt/sys without .md.",
    )
    system_group.add_argument(
        "--no-system-prompt",
        action="store_true",
        help="Use only the user prompt.",
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="PATH",
        help="Input image for editing. Repeat for multiple reference images.",
    )
    parser.add_argument(
        "--mask",
        metavar="PATH",
        help="Optional PNG mask for editing; transparent areas are replaced.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2"),
        help="Image model. Defaults to OPENAI_IMAGE_MODEL or gpt-image-2.",
    )
    parser.add_argument(
        "--api-config",
        type=Path,
        default=DEFAULT_API_CONFIG,
        help=f"API JSON config path (default: {DEFAULT_API_CONFIG}).",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=1,
        help="Number of images returned by the single API request (default: 1).",
    )
    parser.add_argument(
        "--size",
        choices=("auto", "1024x1024", "1024x1536", "1536x1024"),
        default="auto",
    )
    parser.add_argument(
        "--quality",
        choices=("auto", "low", "medium", "high"),
        default="auto",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("png", "jpeg", "webp"),
        default="jpeg",
        help="Output image format (default: jpeg).",
    )
    parser.add_argument(
        "--compression",
        type=int,
        help="JPEG/WebP compression level from 0 to 100.",
    )
    parser.add_argument(
        "--background",
        choices=("auto", "opaque", "transparent"),
        default="auto",
    )
    parser.add_argument(
        "--moderation",
        choices=("auto", "low"),
        default="auto",
        help="Generation moderation strictness. Not sent for edit requests.",
    )
    parser.add_argument(
        "--input-fidelity",
        choices=("low", "high"),
        default="low",
        help="How strongly edits preserve input-image details.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--prefix",
        default="image",
        help="Output filename prefix (default: image).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the request without calling the API.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show a traceback instead of a concise error.",
    )
    return parser


def resolve_prompt(value: str, category: str) -> Path:
    requested = Path(value).expanduser()
    candidates = [requested]
    if not requested.is_absolute():
        candidates.append(PROMPT_DIR / category / requested)
    if requested.suffix == "":
        candidates.extend(candidate.with_suffix(".md") for candidate in list(candidates))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    checked = ", ".join(str(candidate) for candidate in candidates)
    raise CliError(f"prompt not found; checked: {checked}")


def read_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise CliError(f"prompt is empty: {path}")
    return text


def normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    if not base_url:
        raise CliError("API config URL is empty")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return base_url


def load_api_config(path: Path) -> dict[str, str]:
    config_path = path.expanduser().resolve()
    if not config_path.is_file():
        raise CliError(f"API config not found: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"invalid API config JSON: {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise CliError(f"API config must be a JSON object: {config_path}")

    api_key = raw.get("key") or raw.get("api_key")
    api_url = raw.get("url") or raw.get("base_url")
    if not isinstance(api_key, str) or not api_key.strip():
        raise CliError(f"API config is missing 'key': {config_path}")
    if not isinstance(api_url, str) or not api_url.strip():
        raise CliError(f"API config is missing 'url': {config_path}")

    return {
        "api_key": api_key.strip(),
        "base_url": normalize_base_url(api_url),
        "path": str(config_path),
    }


def combine_prompts(system_prompt: str | None, user_prompt: str) -> str:
    if not system_prompt:
        return user_prompt
    return (
        "# SYSTEM GUIDANCE\n\n"
        f"{system_prompt}\n\n"
        "# USER REQUEST\n\n"
        f"{user_prompt}"
    )


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.n <= 10:
        raise CliError("--n must be between 1 and 10")
    if args.mask and not args.image:
        raise CliError("--mask requires at least one --image")
    if args.compression is not None:
        if not 0 <= args.compression <= 100:
            raise CliError("--compression must be between 0 and 100")
        if args.output_format == "png":
            raise CliError("--compression is only valid with --format jpeg or webp")
    if args.background == "transparent" and args.output_format == "jpeg":
        raise CliError("transparent backgrounds require --format png or webp")

    for image_value in args.image:
        image_path = Path(image_value).expanduser()
        if not image_path.is_file():
            raise CliError(f"input image not found: {image_path}")
    if args.mask:
        mask_path = Path(args.mask).expanduser()
        if not mask_path.is_file():
            raise CliError(f"mask not found: {mask_path}")


def request_preview(
    args: argparse.Namespace,
    api_config: dict[str, str],
    system_path: Path | None,
    user_path: Path,
    prompt: str,
) -> dict[str, Any]:
    operation = "edit" if args.image else "generate"
    preview: dict[str, Any] = {
        "operation": operation,
        "model": args.model,
        "api": {
            "config": api_config["path"],
            "base_url": api_config["base_url"],
        },
        "prompt": prompt,
        "prompt_files": {
            "system": str(system_path) if system_path else None,
            "user": str(user_path),
        },
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format,
        "background": args.background,
        "output_dir": str(args.output_dir.expanduser().resolve()),
        "prefix": args.prefix,
    }
    if args.compression is not None:
        preview["output_compression"] = args.compression
    if operation == "generate":
        preview["moderation"] = args.moderation
    else:
        preview["images"] = [
            str(Path(value).expanduser().resolve()) for value in args.image
        ]
        preview["mask"] = (
            str(Path(args.mask).expanduser().resolve()) if args.mask else None
        )
        preview["input_fidelity"] = args.input_fidelity
    return preview


def build_common_api_args(
    args: argparse.Namespace,
    prompt: str,
) -> dict[str, Any]:
    api_args: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format,
        "background": args.background,
    }
    if args.compression is not None:
        api_args["output_compression"] = args.compression
    return api_args


def call_image_api(
    args: argparse.Namespace,
    api_config: dict[str, str],
    prompt: str,
) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise CliError(
            "missing dependency 'openai'; run: "
            "python -m pip install -r img_gen/requirements.txt"
        ) from exc

    client = OpenAI(
        api_key=api_config["api_key"],
        base_url=api_config["base_url"],
    )
    api_args = build_common_api_args(args, prompt)

    if not args.image:
        api_args["moderation"] = args.moderation
        return client.images.generate(**api_args)

    with ExitStack() as stack:
        images = [
            stack.enter_context(open(Path(value).expanduser(), "rb"))
            for value in args.image
        ]
        api_args["image"] = images
        api_args["input_fidelity"] = args.input_fidelity
        if args.mask:
            api_args["mask"] = stack.enter_context(
                open(Path(args.mask).expanduser(), "rb")
            )
        return client.images.edit(**api_args)


def save_response_images(
    response: Any,
    output_dir: Path,
    prefix: str,
    output_format: str,
) -> list[Path]:
    data = getattr(response, "data", None)
    if not data:
        raise CliError("the API response did not contain image data")

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    extension = "jpg" if output_format == "jpeg" else output_format
    output_paths: list[Path] = []

    for index, image_data in enumerate(data, start=1):
        encoded = getattr(image_data, "b64_json", None)
        image_url = getattr(image_data, "url", None)
        if encoded:
            content = base64.b64decode(encoded)
        elif image_url:
            with urllib.request.urlopen(image_url, timeout=120) as remote:
                content = remote.read()
        else:
            raise CliError(f"image {index} has neither b64_json nor url")

        output_path = output_dir / f"{prefix}_{timestamp}_{index:02d}.{extension}"
        output_path.write_bytes(content)
        output_paths.append(output_path)

    return output_paths


def write_metadata(
    preview: dict[str, Any],
    response: Any,
    output_paths: Sequence[Path],
) -> Path:
    metadata = dict(preview)
    metadata["created_at"] = datetime.now(timezone.utc).isoformat()
    metadata["request_id"] = getattr(response, "_request_id", None)
    metadata["outputs"] = [str(path) for path in output_paths]
    metadata_path = output_paths[0].with_suffix(".json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata_path


def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    api_config = load_api_config(args.api_config)
    user_path = resolve_prompt(args.user_prompt, "user")
    system_path = (
        None if args.no_system_prompt else resolve_prompt(args.sys_prompt, "sys")
    )
    user_prompt = read_prompt(user_path)
    system_prompt = read_prompt(system_path) if system_path else None
    prompt = combine_prompts(system_prompt, user_prompt)
    preview = request_preview(args, api_config, system_path, user_path, prompt)

    if args.dry_run:
        return {"dry_run": True, "request": preview}

    response = call_image_api(args, api_config, prompt)
    output_paths = save_response_images(
        response=response,
        output_dir=args.output_dir,
        prefix=args.prefix,
        output_format=args.output_format,
    )
    metadata_path = write_metadata(preview, response, output_paths)
    return {
        "dry_run": False,
        "operation": preview["operation"],
        "request_id": getattr(response, "_request_id", None),
        "outputs": [str(path) for path in output_paths],
        "metadata": str(metadata_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        if args.debug:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
