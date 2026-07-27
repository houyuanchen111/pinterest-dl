#!/usr/bin/env python3
"""Collect exact Pinterest pin images from a pasted URL list into one directory."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import socket
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PIN_RE = re.compile(r"(?:https?://)?(?:www\.)?pinterest\.com/pin/(\d+)")
IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"
}


class OpenGraphImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_url: str | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "meta" or self.image_url is not None:
            return
        values = {key.lower(): value or "" for key, value in attrs}
        image_type = values.get("property") or values.get("name")
        if image_type in {"og:image", "twitter:image"}:
            self.image_url = html.unescape(values.get("content", ""))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-text", type=Path, required=True)
    parser.add_argument("--catalog-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cookies", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.3)
    return parser.parse_args()


def extract_uids(path: Path) -> list[str]:
    values = PIN_RE.findall(path.read_text(encoding="utf-8"))
    return list(dict.fromkeys(values))


def find_output(output_dir: Path, uid: str) -> Path | None:
    for candidate in sorted(output_dir.glob(f"{uid}.*")):
        if (
            candidate.is_file()
            and candidate.suffix.lower() in IMAGE_SUFFIXES
            and candidate.stat().st_size > 0
        ):
            return candidate
    return None


def catalog_paths(path: Path, requested_uids: set[str]) -> dict[str, Path]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("catalog JSON must contain an array")
    result: dict[str, Path] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        uid = str(record.get("pin_uid", ""))
        if uid not in requested_uids or uid in result:
            continue
        image = Path(str(record.get("image_path", "")))
        if image.is_file():
            result[uid] = image
    return result


def write_text_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_manifest(
    path: Path, uids: list[str], results: dict[str, dict[str, Any]]
) -> None:
    payload = {
        "requested_count": len(uids),
        "completed_count": sum(
            result.get("status") == "ok" for result in results.values()
        ),
        "failed_count": sum(
            result.get("status") == "error" for result in results.values()
        ),
        "pending_count": len(uids) - len(results),
        "results": [results[uid] for uid in uids if uid in results],
    }
    write_text_atomic(
        path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def pin_page_image_url(session: Any, uid: str, timeout: int) -> str:
    response = session.get(
        f"https://www.pinterest.com/pin/{uid}/",
        timeout=(timeout, timeout),
    )
    response.raise_for_status()
    parser = OpenGraphImageParser()
    parser.feed(response.text)
    if parser.image_url and "i.pinimg.com/" in parser.image_url:
        return parser.image_url
    match = re.search(
        r'https://i\.pinimg\.com/(?:736x|originals)/[^"\' <>()]+',
        response.text,
    )
    if match:
        return html.unescape(match.group(0))
    raise RuntimeError("Pinterest page contains no pinimg image URL")


def download_pin_page_image(
    session: Any,
    uid: str,
    output_dir: Path,
    timeout: int,
    retries: int = 5,
) -> Path:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            preview_url = pin_page_image_url(session, uid, timeout)
            candidates = [preview_url]
            if "/736x/" in preview_url:
                candidates.insert(0, preview_url.replace("/736x/", "/originals/"))
            for image_url in candidates:
                try:
                    response = session.get(
                        image_url, timeout=(timeout, timeout)
                    )
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith("image/"):
                        raise RuntimeError(
                            f"unexpected content type {content_type!r}"
                        )
                    if len(response.content) < 1024:
                        raise RuntimeError(
                            f"image response is too small: {len(response.content)} bytes"
                        )
                    suffix = Path(urlparse(image_url).path).suffix.lower()
                    if suffix not in IMAGE_SUFFIXES:
                        suffix = {
                            "image/jpeg": ".jpg",
                            "image/png": ".png",
                            "image/webp": ".webp",
                            "image/gif": ".gif",
                        }.get(content_type.split(";", 1)[0], ".jpg")
                    destination = output_dir / f"{uid}{suffix}"
                    temporary = destination.with_name(destination.name + ".tmp")
                    temporary.write_bytes(response.content)
                    os.replace(temporary, destination)
                    return destination
                except Exception as exc:
                    last_error = exc
            raise last_error or RuntimeError("no image candidate succeeded")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 8))
    assert last_error is not None
    raise last_error


def main() -> None:
    args = parse_args()
    for path, label in (
        (args.source_text, "source text"),
        (args.catalog_json, "catalog JSON"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
    if args.cookies is not None and not args.cookies.is_file():
        raise FileNotFoundError(f"cookies not found: {args.cookies}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    uids = extract_uids(args.source_text)
    if not uids:
        raise ValueError(f"No Pinterest pin UIDs found in {args.source_text}")

    uid_path = args.output_dir / "uids.txt"
    manifest_path = args.output_dir / "download_manifest.json"
    failed_path = args.output_dir / "failed_uids.txt"
    write_text_atomic(uid_path, "".join(f"{uid}\n" for uid in uids))

    results: dict[str, dict[str, Any]] = {}
    if manifest_path.is_file():
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for result in old_manifest.get("results", []):
            if isinstance(result, dict) and result.get("uid") in uids:
                results[result["uid"]] = result

    existing = catalog_paths(args.catalog_json, set(uids))
    copied = 0
    resumed = 0
    for uid in uids:
        output = find_output(args.output_dir, uid)
        if output is not None:
            results[uid] = {
                "uid": uid,
                "status": "ok",
                "source": results.get(uid, {}).get("source", "existing_output"),
                "image_path": str(output.resolve()),
                "error": None,
            }
            resumed += 1
            continue
        source = existing.get(uid)
        if source is None:
            results.pop(uid, None)
            continue
        destination = args.output_dir / f"{uid}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        results[uid] = {
            "uid": uid,
            "status": "ok",
            "source": "catalog",
            "source_image_path": str(source.resolve()),
            "image_path": str(destination.resolve()),
            "error": None,
        }
        copied += 1
    write_manifest(manifest_path, uids, results)

    pending = [uid for uid in uids if find_output(args.output_dir, uid) is None]
    print(
        f"UIDs={len(uids)} existing_output={resumed} "
        f"copied_from_catalog={copied} pending_download={len(pending)}",
        flush=True,
    )

    if pending:
        import requests
        import urllib3.util.connection

        urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        for index, uid in enumerate(pending, 1):
            url = f"https://www.pinterest.com/pin/{uid}/"
            try:
                output = download_pin_page_image(
                    session,
                    uid,
                    args.output_dir,
                    args.timeout,
                )
                results[uid] = {
                    "uid": uid,
                    "status": "ok",
                    "source": "pinterest",
                    "url": url,
                    "image_path": str(output.resolve()),
                    "error": None,
                }
                print(
                    f"[{index}/{len(pending)}] OK {uid} -> {output.name}",
                    flush=True,
                )
            except Exception as exc:
                results[uid] = {
                    "uid": uid,
                    "status": "error",
                    "source": "pinterest",
                    "url": url,
                    "image_path": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(
                    f"[{index}/{len(pending)}] ERROR {uid}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            write_manifest(manifest_path, uids, results)
            time.sleep(args.delay)

    failed = [
        uid for uid in uids if results.get(uid, {}).get("status") != "ok"
    ]
    write_text_atomic(failed_path, "".join(f"{uid}\n" for uid in failed))
    write_manifest(manifest_path, uids, results)
    print(
        f"DONE requested={len(uids)} ok={len(uids) - len(failed)} "
        f"failed={len(failed)} output={args.output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
