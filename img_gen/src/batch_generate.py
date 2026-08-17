#!/usr/bin/env python3
"""Batch runner for the numbered wall-projection prompts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


PROJECT_DIR = Path(__file__).resolve().parents[1]
MAIN_SCRIPT = PROJECT_DIR / "src" / "main.py"
DEFAULT_PROMPT_DIR = PROJECT_DIR / "prompt" / "user"
DEFAULT_API_CONFIG = PROJECT_DIR / "api" / "api.json"
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "output"


class BatchError(RuntimeError):
    """Raised for invalid batch configuration."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate images for every numbered wall-projection prompt by "
            "calling main.py once per prompt."
        )
    )
    parser.add_argument(
        "--prompt-dir",
        type=Path,
        default=DEFAULT_PROMPT_DIR,
        help=f"Directory containing prompt Markdown files (default: {DEFAULT_PROMPT_DIR}).",
    )
    parser.add_argument(
        "--prompt-glob",
        default="[0-9][0-9]_*.md",
        help="Prompt filename glob (default: [0-9][0-9]_*.md).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process only the first N matched prompts.",
    )
    system_group = parser.add_mutually_exclusive_group()
    system_group.add_argument(
        "--sys-prompt",
        default="diversity",
        help="System prompt name or Markdown path passed to main.py.",
    )
    system_group.add_argument(
        "--no-system-prompt",
        action="store_true",
        help="Do not use a system prompt.",
    )
    parser.add_argument(
        "--images-per-prompt",
        type=int,
        default=2,
        help="Images returned by each prompt request (default: 2).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2"),
    )
    parser.add_argument(
        "--api-config",
        type=Path,
        default=DEFAULT_API_CONFIG,
    )
    parser.add_argument(
        "--size",
        choices=("auto", "1024x1024", "1024x1536", "1536x1024"),
        default="1536x1024",
    )
    parser.add_argument(
        "--quality",
        choices=("auto", "low", "medium", "high"),
        default="medium",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("png", "jpeg", "webp"),
        default="jpeg",
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
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Batch output root. Defaults to a timestamped directory under img_gen/output.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between API requests (default: 1.0).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Generate again even when a prompt directory already has enough images.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with later prompts after a failed request.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate every request through main.py without calling the API.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.images_per_prompt <= 10:
        raise BatchError("--images-per-prompt must be between 1 and 10")
    if args.limit is not None and args.limit < 1:
        raise BatchError("--limit must be at least 1")
    if args.delay < 0:
        raise BatchError("--delay cannot be negative")
    if args.compression is not None:
        if not 0 <= args.compression <= 100:
            raise BatchError("--compression must be between 0 and 100")
        if args.output_format == "png":
            raise BatchError("--compression is only valid with jpeg or webp")
    if args.background == "transparent" and args.output_format == "jpeg":
        raise BatchError("transparent backgrounds require png or webp")


def discover_prompts(
    prompt_dir: Path,
    prompt_glob: str,
    limit: int | None,
) -> list[Path]:
    resolved_dir = prompt_dir.expanduser().resolve()
    if not resolved_dir.is_dir():
        raise BatchError(f"prompt directory not found: {resolved_dir}")

    prompts = sorted(path.resolve() for path in resolved_dir.glob(prompt_glob))
    prompts = [path for path in prompts if path.is_file()]
    if limit is not None:
        prompts = prompts[:limit]
    if not prompts:
        raise BatchError(
            f"no prompt files matched {prompt_glob!r} in {resolved_dir}"
        )
    return prompts


def default_output_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUTPUT_ROOT / f"wall_projection_batch_{timestamp}"


def output_extension(output_format: str) -> str:
    return "jpg" if output_format == "jpeg" else output_format


def existing_output_count(
    prompt_output_dir: Path,
    prompt_stem: str,
    output_format: str,
) -> int:
    extension = output_extension(output_format)
    return sum(1 for _ in prompt_output_dir.glob(f"{prompt_stem}_*.{extension}"))


def build_command(
    args: argparse.Namespace,
    prompt_path: Path,
    prompt_output_dir: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(MAIN_SCRIPT),
        "--user-prompt",
        str(prompt_path),
        "--api-config",
        str(args.api_config.expanduser().resolve()),
        "--model",
        args.model,
        "--n",
        str(args.images_per_prompt),
        "--size",
        args.size,
        "--quality",
        args.quality,
        "--format",
        args.output_format,
        "--background",
        args.background,
        "--moderation",
        args.moderation,
        "--output-dir",
        str(prompt_output_dir),
        "--prefix",
        prompt_path.stem,
    ]
    if args.no_system_prompt:
        command.append("--no-system-prompt")
    else:
        command.extend(["--sys-prompt", args.sys_prompt])
    if args.compression is not None:
        command.extend(["--compression", str(args.compression)])
    if args.dry_run:
        command.append("--dry-run")
    return command


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_batch(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    validate_args(args)
    prompts = discover_prompts(args.prompt_dir, args.prompt_glob, args.limit)
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else default_output_dir().resolve()
    )
    manifest_path = output_dir / "batch_manifest.json"
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "prompt_glob": args.prompt_glob,
        "prompt_count": len(prompts),
        "images_per_prompt": args.images_per_prompt,
        "expected_image_count": len(prompts) * args.images_per_prompt,
        "model": args.model,
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format,
        "output_dir": str(output_dir),
        "results": [],
    }

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    failed = False
    for index, prompt_path in enumerate(prompts, start=1):
        prompt_output_dir = output_dir / prompt_path.stem
        print(
            f"[{index}/{len(prompts)}] {prompt_path.name}",
            file=sys.stderr,
            flush=True,
        )

        if (
            not args.dry_run
            and not args.force
            and existing_output_count(
                prompt_output_dir,
                prompt_path.stem,
                args.output_format,
            )
            >= args.images_per_prompt
        ):
            manifest["results"].append(
                {
                    "prompt": str(prompt_path),
                    "status": "skipped",
                    "reason": "enough output images already exist",
                    "output_dir": str(prompt_output_dir),
                }
            )
            write_manifest(manifest_path, manifest)
            continue

        command = build_command(args, prompt_path, prompt_output_dir)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            result = json.loads(completed.stdout)
            manifest["results"].append(
                {
                    "prompt": str(prompt_path),
                    "status": "dry-run" if args.dry_run else "completed",
                    "result": result,
                }
            )
        else:
            failed = True
            manifest["results"].append(
                {
                    "prompt": str(prompt_path),
                    "status": "failed",
                    "returncode": completed.returncode,
                    "error": completed.stderr.strip(),
                }
            )
            if not args.dry_run:
                write_manifest(manifest_path, manifest)
            if not args.continue_on_error:
                break

        if not args.dry_run:
            write_manifest(manifest_path, manifest)
        if index < len(prompts) and args.delay and not args.dry_run:
            time.sleep(args.delay)

    completed_count = sum(
        result["status"] == "completed" for result in manifest["results"]
    )
    skipped_count = sum(
        result["status"] == "skipped" for result in manifest["results"]
    )
    failed_count = sum(
        result["status"] == "failed" for result in manifest["results"]
    )
    manifest["summary"] = {
        "completed_prompts": completed_count,
        "skipped_prompts": skipped_count,
        "failed_prompts": failed_count,
        "processed_prompts": len(manifest["results"]),
    }
    if not args.dry_run:
        write_manifest(manifest_path, manifest)
        manifest["manifest"] = str(manifest_path)
    return manifest, 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result, returncode = run_batch(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
