"""Download Pinterest images for a keyword or a pin's related results.

Examples:
    python crawl_pinterest_related_images.py "portrait atmosphere" -n 50
    python crawl_pinterest_related_images.py 123456789012345678 -n 50
    python crawl_pinterest_related_images.py "2026" --mode keyword
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm


DEFAULT_OUTPUT_DIR = Path("/mnt/aigc/houyuanchen/pinterest-dl/pinterest")
DEFAULT_JSON_NAME = "pinterest_images.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".ts"}
PIN_URL_RE = re.compile(r"/pin/(\d+)")


def parse_resolution(value: str | None) -> tuple[int, int]:
    """Parse a resolution string like 800x800."""
    if not value:
        return (0, 0)

    try:
        width, height = value.lower().split("x", 1)
        return int(width), int(height)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Resolution must look like 800x800") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Pinterest images from a keyword or related images from a pin UID."
    )
    parser.add_argument(
        "input",
        help="Search keyword, Pinterest pin UID, or a Pinterest pin URL.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "keyword", "pin"),
        default="auto",
        help="How to interpret input. Auto treats numeric input or pin URLs as pin UID. Default: auto",
    )
    parser.add_argument(
        "-n",
        "--num",
        type=int,
        default=30,
        help="Number of results to scrape. Default: 30",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save images directly into. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--json-path",
        type=Path,
        default=None,
        help=f"Metadata JSON path. Default: <output>/{DEFAULT_JSON_NAME}",
    )
    parser.add_argument(
        "-r",
        "--resolution",
        type=parse_resolution,
        default=(0, 0),
        help="Minimum resolution, for example 800x800. Default: no filter",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.4,
        help="Delay between Pinterest API requests. Default: 0.4",
    )
    parser.add_argument(
        "--bookmark-count",
        type=int,
        default=3,
        choices=(1, 2, 3, 4),
        help="How many Pinterest pagination bookmarks to send for keyword search. Default: 3",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Concurrent image download workers. Default: 16",
    )
    parser.add_argument(
        "--download-timeout",
        type=float,
        default=20.0,
        help="Per-request download timeout in seconds. Default: 20",
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        default=None,
        help="Optional Pinterest cookies JSON file.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed logs from pinterest-dl.",
    )
    return parser


def extract_pin_uid(value: str) -> str | None:
    text = value.strip()
    if text.isdigit():
        return text

    match = PIN_URL_RE.search(text)
    if match:
        return match.group(1)

    return None


def resolve_source(raw_input: str, mode: str) -> tuple[str, str, str]:
    """Return (source_type, keyword, search_pin_uid)."""
    value = raw_input.strip()
    pin_uid = extract_pin_uid(value)

    if mode == "pin":
        if not pin_uid:
            raise ValueError("--mode pin requires a numeric pin UID or Pinterest pin URL.")
        return "pin", "", pin_uid

    if mode == "keyword":
        return "keyword", value, ""

    if pin_uid:
        return "pin", "", pin_uid
    return "keyword", value, ""


def pin_url(pin_uid: str) -> str:
    return f"https://www.pinterest.com/pin/{pin_uid}/"


def load_records(json_path: Path) -> list[dict[str, Any]]:
    if not json_path.exists():
        return []

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Metadata JSON is not valid JSON: {json_path}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Metadata JSON must be a list of records: {json_path}")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Metadata JSON item #{index} is not an object: {json_path}")
        records.append(item)
    return records


def write_records(records: list[dict[str, Any]], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = json_path.with_name(f"{json_path.name}.tmp")
    tmp_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(json_path)


def normalize_resolution(value: Any) -> dict[str, int | None]:
    if isinstance(value, dict):
        x = value.get("x")
        y = value.get("y")
        return {
            "x": int(x) if isinstance(x, int) else None,
            "y": int(y) if isinstance(y, int) else None,
        }

    if isinstance(value, (tuple, list)) and len(value) >= 2:
        x, y = value[0], value[1]
        if x and y:
            return {"x": int(x), "y": int(y)}

    return {"x": None, "y": None}


def normalize_like_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def media_like_count(media: object) -> int | None:
    return normalize_like_count(getattr(media, "like_count", None))


def record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("pin_uid", "")),
        str(record.get("keyword", "")),
        str(record.get("search_pin_uid", "")),
    )


def build_indexes(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str, str]]]:
    by_pin_uid: dict[str, dict[str, Any]] = {}
    by_source: set[tuple[str, str, str]] = set()

    for record in records:
        key = record_key(record)
        pin_uid = key[0]
        if pin_uid and pin_uid not in by_pin_uid:
            by_pin_uid[pin_uid] = record
        if pin_uid:
            by_source.add(key)

    return by_pin_uid, by_source


def existing_record_path(record: dict[str, Any] | None) -> Path | None:
    if not record:
        return None

    value = record.get("image_path")
    if not isinstance(value, str) or not value:
        return None

    path = Path(value).expanduser()
    if path.exists():
        return path.resolve()
    return None


def find_existing_file(output_dir: Path, pin_uid: str) -> Path | None:
    for candidate in output_dir.glob(f"{pin_uid}.*"):
        if candidate.suffix.lower() in IMAGE_SUFFIXES and candidate.is_file():
            return candidate.resolve()
    return None


def make_record(
    image_path: Path,
    pin_uid: str,
    keyword: str,
    search_pin_uid: str,
    resolution: Any,
    like_count: Any,
) -> dict[str, Any]:
    return {
        "image_path": str(image_path.resolve()),
        "pin_uid": pin_uid,
        "keyword": keyword,
        "search_pin_uid": search_pin_uid,
        "resolution": normalize_resolution(resolution),
        "like_count": normalize_like_count(like_count),
    }


def update_records_for_pin(
    records: list[dict[str, Any]],
    pin_uid: str,
    image_path: Path | None = None,
    resolution: Any = None,
    like_count: Any = None,
) -> None:
    normalized_resolution = normalize_resolution(resolution) if resolution is not None else None
    normalized_like_count = normalize_like_count(like_count)
    for record in records:
        if str(record.get("pin_uid", "")) == pin_uid:
            if image_path is not None:
                record["image_path"] = str(image_path.resolve())
            if normalized_resolution is not None:
                record["resolution"] = normalized_resolution
            if like_count is not None or "like_count" not in record:
                record["like_count"] = normalized_like_count


def set_local_resolution(media_storage: object, media: object, path: Path) -> None:
    if getattr(media, "resolution", None) not in (None, (0, 0)):
        return

    try:
        media_storage.set_local_resolution(media, path)
    except Exception:
        return


def download_one(media_storage: object, downloader: object, item: object, output_dir: Path) -> object:
    path = downloader.download(item, output_dir, download_streams=False, skip_remux=False)
    item.set_local_path(path)
    set_local_resolution(media_storage, item, path)
    return item


def download_missing(
    items: list[object],
    output_dir: Path,
    workers: int,
    timeout: float,
) -> list[object]:
    if not items:
        return []

    from pinterest_dl.download import USER_AGENT
    from pinterest_dl.download.downloader import MediaDownloader
    from pinterest_dl.storage import media as media_storage

    downloader = MediaDownloader(
        user_agent=USER_AGENT,
        timeout=timeout,
        max_retries=3,
    )

    results: list[object | None] = [None] * len(items)
    failures: list[tuple[object, Exception]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_one, media_storage, downloader, item, output_dir): index
            for index, item in enumerate(items)
        }
        with tqdm(total=len(futures), desc="Downloading Media") as pbar:
            for future in as_completed(futures):
                index = futures[future]
                item = items[index]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    failures.append((item, exc))
                finally:
                    pbar.update(1)

    if failures:
        print(f"Skipped {len(failures)} failed item(s).")
        for item, exc in failures:
            pin_uid = getattr(item, "id", "unknown")
            print(f"  - pin {pin_uid}: {exc}")

    return [item for item in results if item is not None]


def scrape_items(args: argparse.Namespace, source_type: str, keyword: str, search_pin_uid: str) -> list[object]:
    from pinterest_dl import PinterestDL

    scraper = PinterestDL.with_api(verbose=args.verbose).with_cookies_path(args.cookies)

    if source_type == "keyword":
        print(f'Searching keyword: "{keyword}"')
        return scraper.search(
            keyword,
            args.num,
            min_resolution=args.resolution,
            delay=args.delay,
            bookmarksCount=args.bookmark_count,
        )

    print(f"Searching related pins for pin_uid: {search_pin_uid}")
    return scraper.related(
        pin_url(search_pin_uid),
        args.num,
        min_resolution=args.resolution,
        delay=args.delay,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.num < 1:
        parser.error("--num must be greater than 0")
    if args.workers < 1:
        parser.error("--workers must be greater than 0")
    if args.download_timeout <= 0:
        parser.error("--download-timeout must be greater than 0")

    try:
        source_type, keyword, search_pin_uid = resolve_source(args.input, args.mode)
    except ValueError as exc:
        parser.error(str(exc))

    args.output = args.output.expanduser().resolve()
    args.json_path = (args.json_path or args.output / DEFAULT_JSON_NAME).expanduser().resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    args.json_path.parent.mkdir(parents=True, exist_ok=True)

    records = load_records(args.json_path)
    records_by_pin_uid, existing_source_keys = build_indexes(records)

    scraped_items = scrape_items(args, source_type, keyword, search_pin_uid)
    items_to_download: list[object] = []
    queued_pin_uids: set[str] = set()
    skipped_same_source = 0
    reused_existing = 0

    for item in scraped_items:
        pin_uid = str(getattr(item, "id", ""))
        if not pin_uid:
            continue

        source_key = (pin_uid, keyword, search_pin_uid)
        if source_key in existing_source_keys:
            update_records_for_pin(
                records,
                pin_uid,
                resolution=getattr(item, "resolution", None),
                like_count=media_like_count(item),
            )
            skipped_same_source += 1
            continue

        existing_record = records_by_pin_uid.get(pin_uid)
        existing_path = existing_record_path(existing_record)
        if existing_path is None:
            existing_path = find_existing_file(args.output, pin_uid)

        if existing_path is not None:
            item.set_local_path(existing_path)
            resolution = (
                existing_record.get("resolution")
                if existing_record is not None
                else getattr(item, "resolution", None)
            )
            like_count = media_like_count(item)
            if like_count is None and existing_record is not None:
                like_count = normalize_like_count(existing_record.get("like_count"))
            update_records_for_pin(
                records,
                pin_uid,
                image_path=existing_path,
                resolution=resolution,
                like_count=like_count,
            )
            new_record = make_record(
                existing_path,
                pin_uid,
                keyword,
                search_pin_uid,
                resolution,
                like_count,
            )
            records.append(new_record)
            records_by_pin_uid.setdefault(pin_uid, new_record)
            existing_source_keys.add(source_key)
            reused_existing += 1
            continue

        if pin_uid not in queued_pin_uids:
            items_to_download.append(item)
            queued_pin_uids.add(pin_uid)

    downloaded_items = download_missing(
        items_to_download,
        args.output,
        args.workers,
        args.download_timeout,
    )

    for item in downloaded_items:
        pin_uid = str(getattr(item, "id", ""))
        local_path = getattr(item, "local_path", None)
        if not pin_uid or local_path is None:
            continue

        image_path = Path(local_path).resolve()
        like_count = media_like_count(item)
        update_records_for_pin(
            records,
            pin_uid,
            image_path=image_path,
            resolution=getattr(item, "resolution", None),
            like_count=like_count,
        )
        new_record = make_record(
            image_path,
            pin_uid,
            keyword,
            search_pin_uid,
            getattr(item, "resolution", None),
            like_count,
        )
        records.append(new_record)
        records_by_pin_uid[pin_uid] = new_record
        existing_source_keys.add((pin_uid, keyword, search_pin_uid))

    write_records(records, args.json_path)

    print(f"Done. Scraped: {len(scraped_items)}")
    print(f"Downloaded new images: {len(downloaded_items)}")
    print(f"Reused existing images and appended source records: {reused_existing}")
    print(f"Skipped existing records for same source: {skipped_same_source}")
    print(f"Metadata JSON: {args.json_path}")
    print(f"Image directory: {args.output}")


if __name__ == "__main__":
    main()
