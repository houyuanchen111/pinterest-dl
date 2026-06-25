"""Search Pinterest for a keyword and download images.

Run from the repository root:
    python search_atmosphere_images.py

Common options:
    python search_atmosphere_images.py --query 氛围感人像 --num 50
    python search_atmosphere_images.py --query 氛围感人像 --all --max-items 1000
    python search_atmosphere_images.py --resolution 800x800 --caption txt
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm


DEFAULT_QUERY = "氛围感人像"


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
        description="Search Pinterest for images and download them."
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=f"Search keyword. Default: {DEFAULT_QUERY}",
    )
    parser.add_argument(
        "-n",
        "--num",
        type=int,
        default=30,
        help="Number of images to download in normal mode. Default: 30",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Keep paging through Pinterest search until it ends or max-items is reached.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Hard stop for --all mode. Use 0 for no limit. Default: 0",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="How many scraped items to download per batch in --all mode. Default: 200",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=32,
        help="Concurrent image download workers. Default: 32",
    )
    parser.add_argument(
        "--download-timeout",
        type=float,
        default=20.0,
        help="Per-request download timeout in seconds. Default: 20",
    )
    parser.add_argument(
        "--bookmark-count",
        type=int,
        default=3,
        choices=(1, 2, 3, 4),
        help="How many Pinterest pagination bookmarks to send. Default: 3",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output directory. Default: downloads/<query>",
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
        "--caption",
        choices=("txt", "json", "metadata", "none"),
        default="none",
        help="How to save captions/alt text. Default: none",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Save scraped metadata JSON here. Default: cache/<query>_images.json",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not write scraped metadata JSON.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed logs from pinterest-dl.",
    )
    return parser


def media_to_dict(media: object) -> dict:
    data = media.to_dict()
    local_path = getattr(media, "local_path", None)
    if local_path is not None:
        data["local_path"] = str(local_path)
    return data


def write_cache(items: list[object], cache_path: Path | None) -> None:
    if not cache_path:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps([media_to_dict(item) for item in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_captions(operations: object, items: list[object], output_dir: Path, caption: str, verbose: bool) -> None:
    if not items:
        return
    if caption in ("txt", "json"):
        operations.add_captions_to_file(items, output_dir, caption, verbose)
    elif caption == "metadata":
        operations.add_captions_to_meta(items, verbose)


def download_one(media_storage: object, downloader: object, item: object, output_dir: Path) -> object:
    path = downloader.download(item, output_dir, download_streams=False, skip_remux=False)
    item.set_local_path(path)
    if getattr(item, "resolution", None) in (None, (0, 0)):
        try:
            media_storage.set_local_resolution(item, path)
        except Exception:
            pass
    return item


def download_batch(operations: object, items: list[object], args: argparse.Namespace) -> list[object]:
    if not items:
        return []

    from pinterest_dl.download import USER_AGENT
    from pinterest_dl.download.downloader import MediaDownloader
    from pinterest_dl.storage import media as media_storage

    args.output.mkdir(parents=True, exist_ok=True)
    downloader = MediaDownloader(
        user_agent=USER_AGENT,
        timeout=args.download_timeout,
        max_retries=3,
    )

    downloaded_items: list[object] = []
    failures: list[tuple[object, Exception]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                download_one,
                media_storage,
                downloader,
                item,
                args.output,
            ): item
            for item in items
        }
        with tqdm(total=len(futures), desc="Downloading Media") as pbar:
            for future in as_completed(futures):
                item = futures[future]
                try:
                    downloaded_items.append(future.result())
                except Exception as exc:
                    failures.append((item, exc))
                finally:
                    pbar.update(1)

    if failures:
        print(f"Skipped {len(failures)} failed item(s) in this batch.")
        if args.verbose:
            for item, exc in failures:
                pin_id = getattr(item, "id", "unknown")
                print(f"  - pin {pin_id}: {exc}")

    kept = operations.prune_images(downloaded_items, args.resolution, args.verbose)
    add_captions(operations, kept, args.output, args.caption, args.verbose)
    return kept


def download_all_available(PinterestDL: object, operations: object, args: argparse.Namespace) -> list[object]:
    scraper = PinterestDL.with_api(verbose=args.verbose).with_cookies([])
    source = scraper.iter_search(
        args.query,
        min_resolution=args.resolution,
        delay=args.delay,
        bookmarksCount=args.bookmark_count,
    )

    downloaded_items: list[object] = []
    batch: list[object] = []
    scraped_count = 0
    limit = args.max_items if args.max_items > 0 else None

    for media in source:
        if limit is not None and len(downloaded_items) >= limit:
            break

        scraped_count += 1
        batch.append(media)

        should_flush = len(batch) >= args.batch_size
        if limit is not None and len(downloaded_items) + len(batch) >= limit:
            should_flush = True

        if should_flush:
            downloaded_items.extend(download_batch(operations, batch, args))
            write_cache(downloaded_items, args.cache)
            print(f"Scraped: {scraped_count}; downloaded: {len(downloaded_items)}")
            batch = []

    if batch:
        downloaded_items.extend(download_batch(operations, batch, args))
        write_cache(downloaded_items, args.cache)

    print(f"Search stream ended or stopped. Scraped: {scraped_count}; downloaded: {len(downloaded_items)}")
    return downloaded_items


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.num < 1:
        parser.error("--num must be greater than 0")
    if args.max_items < 0:
        parser.error("--max-items cannot be negative")
    if args.batch_size < 1:
        parser.error("--batch-size must be greater than 0")
    if args.workers < 1:
        parser.error("--workers must be greater than 0")
    if args.download_timeout <= 0:
        parser.error("--download-timeout must be greater than 0")

    args.output = args.output or Path("downloads") / args.query
    args.cache = None if args.no_cache else (args.cache or Path("cache") / f"{args.query}_images.json")

    try:
        from pinterest_dl import PinterestDL
        from pinterest_dl.scrapers import operations
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"Missing Python dependency: {exc.name}. "
            "Install this project first with: pip install -e ."
        ) from exc

    args.output.mkdir(parents=True, exist_ok=True)
    if args.cache:
        args.cache.parent.mkdir(parents=True, exist_ok=True)

    print(f'Searching Pinterest for "{args.query}"...')
    print(f"Output directory: {args.output}")

    if args.all:
        images = download_all_available(PinterestDL, operations, args)
    else:
        images = PinterestDL.with_api(verbose=args.verbose).with_cookies([]).search_and_download(
            query=args.query,
            output_dir=args.output,
            num=args.num,
            min_resolution=args.resolution,
            cache_path=args.cache,
            caption=args.caption,
            delay=args.delay,
        )

    count = len(images or [])
    print(f"Done. Downloaded {count} image(s).")
    if args.cache:
        print(f"Scraped metadata saved to: {args.cache}")


if __name__ == "__main__":
    main()
