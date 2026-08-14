#!/usr/bin/env python3
"""Classify one image with a selectable prompt profile."""

from __future__ import annotations

import argparse
import base64
import copy
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
PROMPTS_DIR = VLM_DIR / "prompts"
DEFAULT_PROMPT = "sky_sea"
DEFAULT_USER_PROMPT = "请严格检查这张图片，并只按系统要求返回一个 JSON 对象。"
SUPPORTED_SUFFIXES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


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


@dataclass(frozen=True)
class PromptProfile:
    id: str
    description: str
    system_prompt: str
    user_prompt: str
    source: Path
    required_boolean_fields: tuple[str, ...] = ()
    required_nonempty_string_fields: tuple[str, ...] = ()
    pass_field: str | None = None
    pass_from: tuple[str, ...] = ()


class ClassificationError(RuntimeError):
    """Raised when the VLM response cannot be accepted."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use a vision-language model to classify one image with a prompt profile."
    )
    parser.add_argument(
        "image",
        type=Path,
        nargs="?",
        help="Input PNG, JPG, or JPEG image.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help=(
            "Prompt profile name under prompts/, a profile JSON path, or a raw Markdown "
            f"prompt path. Default: {DEFAULT_PROMPT}"
        ),
    )
    parser.add_argument(
        "--list-prompts",
        action="store_true",
        help="List available prompt profiles and exit.",
    )
    parser.add_argument(
        "--system-prompt",
        type=Path,
        help="Deprecated compatibility option: use a raw Markdown prompt file.",
    )
    parser.add_argument(
        "--user-prompt",
        help="Override the user instruction defined by the selected prompt profile.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path. The result is always printed to stdout.",
    )
    parser.add_argument("--api-config", type=Path, default=DEFAULT_API_CONFIG)
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


def require_string_list(
    value: Any,
    field_name: str,
    profile_path: Path,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(
            f"{field_name!r} must be a list of non-empty strings: {profile_path}"
        )
    return tuple(item.strip() for item in value)


def load_profile_json(path: Path) -> PromptProfile:
    with path.open(encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        raise ValueError(f"Prompt profile must be a JSON object: {path}")

    profile_id = raw.get("id")
    prompt_file = raw.get("system_prompt_file")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError(f"Prompt profile requires a non-empty 'id': {path}")
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        raise ValueError(
            f"Prompt profile requires a non-empty 'system_prompt_file': {path}"
        )

    system_prompt_path = (path.parent / prompt_file).resolve()
    system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()
    if not system_prompt:
        raise ValueError(f"System prompt is empty: {system_prompt_path}")

    description = raw.get("description", "")
    user_prompt = raw.get("user_prompt", DEFAULT_USER_PROMPT)
    if not isinstance(description, str):
        raise ValueError(f"'description' must be a string: {path}")
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        raise ValueError(f"'user_prompt' must be a non-empty string: {path}")

    validation = raw.get("validation", {})
    if not isinstance(validation, dict):
        raise ValueError(f"'validation' must be a JSON object: {path}")

    boolean_fields = require_string_list(
        validation.get("required_boolean_fields"),
        "validation.required_boolean_fields",
        path,
    )
    string_fields = require_string_list(
        validation.get("required_nonempty_string_fields"),
        "validation.required_nonempty_string_fields",
        path,
    )
    pass_from = require_string_list(
        validation.get("pass_from"),
        "validation.pass_from",
        path,
    )
    pass_field = validation.get("pass_field")
    if pass_field is not None and (
        not isinstance(pass_field, str) or not pass_field.strip()
    ):
        raise ValueError(f"'validation.pass_field' must be a non-empty string: {path}")
    if pass_from and not pass_field:
        raise ValueError(
            f"'validation.pass_field' is required when 'pass_from' is set: {path}"
        )

    return PromptProfile(
        id=profile_id.strip(),
        description=description.strip(),
        system_prompt=system_prompt,
        user_prompt=user_prompt.strip(),
        source=path.resolve(),
        required_boolean_fields=boolean_fields,
        required_nonempty_string_fields=string_fields,
        pass_field=pass_field.strip() if pass_field else None,
        pass_from=pass_from,
    )


def load_raw_prompt(path: Path) -> PromptProfile:
    system_prompt = path.read_text(encoding="utf-8").strip()
    if not system_prompt:
        raise ValueError(f"System prompt is empty: {path}")
    return PromptProfile(
        id=path.stem,
        description="Raw prompt without profile-level output validation.",
        system_prompt=system_prompt,
        user_prompt=DEFAULT_USER_PROMPT,
        source=path.resolve(),
    )


def resolve_prompt_path(prompt: str) -> Path:
    candidate = Path(prompt).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    profile_candidate = PROMPTS_DIR / f"{prompt}.json"
    if profile_candidate.is_file():
        return profile_candidate.resolve()

    available = ", ".join(profile.id for profile in discover_prompt_profiles())
    suffix = f" Available profiles: {available}" if available else ""
    raise FileNotFoundError(f"Prompt profile or file does not exist: {prompt}.{suffix}")


def load_prompt_profile(prompt: str) -> PromptProfile:
    path = resolve_prompt_path(prompt)
    if path.suffix.lower() == ".json":
        return load_profile_json(path)
    return load_raw_prompt(path)


def discover_prompt_profiles() -> list[PromptProfile]:
    profiles = []
    if not PROMPTS_DIR.is_dir():
        return profiles
    for path in sorted(PROMPTS_DIR.glob("*.json")):
        profiles.append(load_profile_json(path))
    return profiles


def print_prompt_profiles() -> None:
    profiles = discover_prompt_profiles()
    if not profiles:
        print(f"No prompt profiles found in {PROMPTS_DIR}")
        return
    for profile in profiles:
        description = f" - {profile.description}" if profile.description else ""
        print(f"{profile.id}{description}")


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
    profile: PromptProfile,
    image_url: str,
    user_prompt: str,
) -> dict[str, Any]:
    return {
        "model": api.model,
        "messages": [
            {"role": "system", "content": profile.system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
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
    profile: PromptProfile,
    image_url: str,
    user_prompt: str,
    timeout: float,
) -> str:
    payload = build_payload(api, profile, image_url, user_prompt)
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


def get_nested_value(data: dict[str, Any], field_path: str) -> Any:
    value: Any = data
    for part in field_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ClassificationError(f"Missing required field: {field_path!r}")
        value = value[part]
    return value


def set_nested_value(data: dict[str, Any], field_path: str, value: Any) -> None:
    parts = field_path.split(".")
    target = data
    for part in parts[:-1]:
        child = target.get(part)
        if child is None:
            child = {}
            target[part] = child
        if not isinstance(child, dict):
            raise ClassificationError(
                f"Cannot set {field_path!r}; {part!r} is not a JSON object"
            )
        target = child
    target[parts[-1]] = value


def validate_result(
    raw: dict[str, Any],
    profile: PromptProfile,
) -> dict[str, Any]:
    result = copy.deepcopy(raw)

    for field in profile.required_boolean_fields:
        if type(get_nested_value(result, field)) is not bool:
            raise ClassificationError(f"{field!r} must be a JSON boolean")

    for field in profile.required_nonempty_string_fields:
        value = get_nested_value(result, field)
        if not isinstance(value, str) or not value.strip():
            raise ClassificationError(f"{field!r} must be a non-empty string")
        set_nested_value(result, field, value.strip())

    if profile.pass_field:
        if profile.pass_from:
            pass_values = []
            for field in profile.pass_from:
                value = get_nested_value(result, field)
                if type(value) is not bool:
                    raise ClassificationError(f"{field!r} must be a JSON boolean")
                pass_values.append(value)
            pass_value = all(pass_values)
            existing = result
            try:
                existing = get_nested_value(result, profile.pass_field)
            except ClassificationError:
                pass
            if existing is not result and type(existing) is not bool:
                raise ClassificationError(
                    f"{profile.pass_field!r} must be a JSON boolean"
                )
            set_nested_value(result, profile.pass_field, pass_value)
        elif type(get_nested_value(result, profile.pass_field)) is not bool:
            raise ClassificationError(
                f"{profile.pass_field!r} must be a JSON boolean"
            )

    return result


def classify_with_retries(
    api: ApiConfig,
    profile: PromptProfile,
    image_url: str,
    user_prompt: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            content = request_completion(
                api,
                profile,
                image_url,
                user_prompt,
                timeout,
            )
            return validate_result(extract_json_object(content), profile)
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
        if args.list_prompts:
            print_prompt_profiles()
            return 0
        if args.image is None:
            raise ValueError("IMAGE_PATH is required unless --list-prompts is used")
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        if args.retries <= 0:
            raise ValueError("--retries must be positive")

        media_type = validate_image(args.image)
        api = load_api_config(args.api_config)
        if args.system_prompt:
            profile = load_raw_prompt(args.system_prompt)
        else:
            profile = load_prompt_profile(args.prompt)
        user_prompt = args.user_prompt or profile.user_prompt
        if not user_prompt.strip():
            raise ValueError("--user-prompt must not be empty")

        result = classify_with_retries(
            api=api,
            profile=profile,
            image_url=image_data_url(args.image, media_type),
            user_prompt=user_prompt.strip(),
            timeout=args.timeout,
            retries=args.retries,
        )
        output = {
            **result,
            "image": str(args.image.resolve()),
            "model": api.model,
            "prompt": profile.id,
        }
        serialized = json.dumps(output, ensure_ascii=False, indent=2)
        print(serialized)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ClassificationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
