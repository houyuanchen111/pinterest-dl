#!/usr/bin/env python3
"""Classify one image against sky/sea, lighting, and realism criteria."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
VLM_DIR = SCRIPT_DIR.parent
DEFAULT_API_CONFIG = VLM_DIR / "api" / "qwen_3_5_plus.json"
DEFAULT_SYSTEM_PROMPT = VLM_DIR / "prompt" / "image_scene_classifier_zh.md"
SUPPORTED_SUFFIXES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
REQUIRED_BOOLEAN_FIELDS = (
    "has_sky_and_sea",
    "extreme_light_shadow",
    "photorealistic",
)
REQUIRED_REASON_FIELDS = (
    "sky_and_sea",
    "light_shadow",
    "photorealistic",
)


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    api_key: str
    model: str

    @property
    def chat_completions_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"


class ClassificationError(RuntimeError):
    """Raised when the VLM response cannot be accepted."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use a vision-language model to judge whether one image contains both "
            "sky and sea, has extremely obvious light/shadow, and is photorealistic."
        )
    )
    parser.add_argument("image", type=Path, help="Input PNG, JPG, or JPEG image.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path. The result is always printed to stdout.",
    )
    parser.add_argument("--api-config", type=Path, default=DEFAULT_API_CONFIG)
    parser.add_argument("--system-prompt", type=Path, default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def load_api_config(path: Path) -> ApiConfig:
    with path.open(encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        raise ValueError(f"API config must be a JSON object: {path}")
    missing = [key for key in ("base_url", "api_key", "model") if not raw.get(key)]
    if missing:
        raise ValueError(f"Missing API config fields in {path}: {', '.join(missing)}")
    api_key = os.environ.get("VLM_API_KEY", raw["api_key"])
    return ApiConfig(
        base_url=str(raw["base_url"]),
        api_key=str(api_key),
        model=str(raw["model"]),
    )


def validate_image(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"Unsupported image suffix {suffix!r}; expected: {supported}")
    if path.stat().st_size == 0:
        raise ValueError(f"Image is empty: {path}")
    return SUPPORTED_SUFFIXES[suffix]


def image_data_url(path: Path, media_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def build_payload(
    api: ApiConfig,
    system_prompt: str,
    image_url: str,
) -> dict[str, Any]:
    return {
        "model": api.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请严格检查这张图片，并只按系统要求返回一个 JSON 对象。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            },
        ],
        "temperature": 0,
    }


def request_completion(
    api: ApiConfig,
    system_prompt: str,
    image_url: str,
    timeout: float,
) -> str:
    payload = build_payload(api, system_prompt, image_url)
    request = urllib.request.Request(
        api.chat_completions_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ClassificationError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ClassificationError(f"Request failed: {exc}") from exc

    try:
        response_json = json.loads(response_body)
        content = response_json["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise ClassificationError(
            f"Unexpected API response: {response_body[:1000]}"
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise ClassificationError("API returned empty message content")
    return content


def extract_json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    candidates = [stripped]
    fenced_match = re.fullmatch(
        r"```(?:json)?\s*(\{.*\})\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced_match:
        candidates.insert(0, fenced_match.group(1))
    object_match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ClassificationError(f"Model did not return a valid JSON object: {content[:1000]}")


def validate_result(raw: dict[str, Any]) -> dict[str, Any]:
    for field in REQUIRED_BOOLEAN_FIELDS:
        if type(raw.get(field)) is not bool:
            raise ClassificationError(f"{field!r} must be a JSON boolean")

    reasons = raw.get("reasons")
    if not isinstance(reasons, dict):
        raise ClassificationError("'reasons' must be a JSON object")
    for field in REQUIRED_REASON_FIELDS:
        value = reasons.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ClassificationError(f"reasons.{field} must be a non-empty string")

    all_pass = all(raw[field] for field in REQUIRED_BOOLEAN_FIELDS)
    if "all_pass" in raw and type(raw["all_pass"]) is not bool:
        raise ClassificationError("'all_pass' must be a JSON boolean")

    return {
        "has_sky_and_sea": raw["has_sky_and_sea"],
        "extreme_light_shadow": raw["extreme_light_shadow"],
        "photorealistic": raw["photorealistic"],
        "all_pass": all_pass,
        "reasons": {
            "sky_and_sea": reasons["sky_and_sea"].strip(),
            "light_shadow": reasons["light_shadow"].strip(),
            "photorealistic": reasons["photorealistic"].strip(),
        },
    }


def classify_with_retries(
    api: ApiConfig,
    system_prompt: str,
    image_url: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            content = request_completion(api, system_prompt, image_url, timeout)
            return validate_result(extract_json_object(content))
        except ClassificationError as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise ClassificationError(
        f"Classification failed after {attempts} attempt(s): {last_error}"
    )


def main() -> int:
    args = parse_args()
    try:
        media_type = validate_image(args.image)
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        if args.retries <= 0:
            raise ValueError("--retries must be positive")

        api = load_api_config(args.api_config)
        system_prompt = args.system_prompt.read_text(encoding="utf-8").strip()
        if not system_prompt:
            raise ValueError(f"System prompt is empty: {args.system_prompt}")

        result = classify_with_retries(
            api=api,
            system_prompt=system_prompt,
            image_url=image_data_url(args.image, media_type),
            timeout=args.timeout,
            retries=args.retries,
        )
        output = {
            "image": str(args.image.resolve()),
            "model": api.model,
            **result,
        }
        serialized = json.dumps(output, ensure_ascii=False, indent=2)
        print(serialized)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, ClassificationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
