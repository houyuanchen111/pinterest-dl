"""Search Pinterest for the keyword "氛围感" and download images.

Run from the repository root:
    python search_atmosphere_images.py

Common options:
    python search_atmosphere_images.py --num 50 --output downloads/氛围感
    python search_atmosphere_images.py --resolution 800x800 --caption txt
"""

from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_QUERY = "氛围感"
DEFAULT_OUTPUT_DIR = Path("downloads") / DEFAULT_QUERY
DEFAULT_CACHE_PATH = Path("cache") / "atmosphere_images.json"


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
        description='Search Pinterest for "氛围感" images and download them.'
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
        help="Number of images to download. Default: 30",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}",
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
        default=DEFAULT_CACHE_PATH,
        help=f"Save scraped metadata JSON here. Default: {DEFAULT_CACHE_PATH}",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed logs from pinterest-dl.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        from pinterest_dl import PinterestDL
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

    images = PinterestDL.with_api(verbose=args.verbose).search_and_download(
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
