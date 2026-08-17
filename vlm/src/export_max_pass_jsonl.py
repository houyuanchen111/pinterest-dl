#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


JsonObject = dict[str, Any]
Selector = Callable[[JsonObject, Path], bool]


@dataclass(frozen=True)
class CategoryConfig:
    name: str
    stages: tuple[str, ...]
    final_file: str = "summary.jsonl"
    selector: Selector | None = None
    metadata_globs: tuple[str, ...] = ()
    selection_description: str = "all_pass == true"


def select_all_pass(record: JsonObject, _: Path) -> bool:
    return record.get("all_pass") is True


def select_strict_bokeh(record: JsonObject, _: Path) -> bool:
    return (
        record.get("all_pass") is True
        and record.get("visual_match_score", -1) >= 88
    )


CATEGORIES = (
    CategoryConfig(
        name="autumn_forest",
        stages=("gpt_5_6_luna_autumn_forest_merged_strong_light",),
        selector=select_all_pass,
        metadata_globs=(
            "light_repo/visual_keywords/autumn_forest_*/pinterest_images.json",
        ),
    ),
    CategoryConfig(
        name="cyberpunk_neon_portrait",
        stages=(
            "gpt_5_6_luna_cyberpunk_photorealistic",
            "gpt_5_6_luna_cyberpunk_neon_light_no_green",
            "gpt_5_6_luna_cyberpunk_obvious_background_light_shadow",
        ),
        selector=select_all_pass,
        metadata_globs=(
            "light_repo/visual_keywords/cyberpunk_neon_portrait_*/pinterest_images.json",
        ),
    ),
    CategoryConfig(
        name="lush_forest",
        stages=(
            "gpt_5_6_luna_lush_forest",
            "gpt_5_6_luna_lush_forest_light_score",
        ),
        final_file="top_200.jsonl",
        selector=lambda _record, _run_dir: True,
        metadata_globs=(
            "light_repo/visual_keywords/lush_forest_*/pinterest_images.json",
        ),
        selection_description="top 200 by light_shadow_score",
    ),
    CategoryConfig(
        name="ocean_shimmer",
        stages=("gpt_5_6_luna_ocean_shimmer",),
        selector=select_all_pass,
        metadata_globs=(
            "light_repo/ocean_shimmer/merged_unique/pinterest_images.json",
        ),
    ),
    CategoryConfig(
        name="night_city_bokeh_lights",
        stages=(
            "gpt_5_6_luna_pin_657595983150761272_related_night_bokeh_lights",
            "gpt_5_6_luna_pin_657595983150761272_related_night_city_bokeh_lights_strict",
        ),
        selector=select_strict_bokeh,
        metadata_globs=(
            "light_repo/pin_657595983150761272_related/pinterest_images.json",
        ),
        selection_description="all_pass == true and visual_match_score >= 88",
    ),
    CategoryConfig(
        name="sheer_curtain_light",
        stages=(
            "gpt_5_6_luna_pin_996421486310676075_related_sheer_curtain_light",
        ),
        selector=select_all_pass,
        metadata_globs=(
            "light_repo/pin_996421486310676075_related/pinterest_images.json",
        ),
    ),
    CategoryConfig(
        name="sky",
        stages=("gpt_5_6_luna_sky_subject_sun",),
        selector=select_all_pass,
        metadata_globs=(
            "light_repo/sky_en/pinterest_images.json",
            "light_repo/sky_zh/pinterest_images.json",
        ),
        selection_description=(
            "all_pass == true in the stricter sky_subject_sun rerun"
        ),
    ),
    CategoryConfig(
        name="sky_sea",
        stages=(
            "gpt_5_6_luna_sky_sea",
            "gpt_5_6_luna_sky_has_sun",
        ),
        selector=select_all_pass,
        metadata_globs=(
            "light_repo/sky_sea/pinterest_images.json",
            "light_repo/sky_sea_en/pinterest_images.json",
        ),
    ),
    CategoryConfig(
        name="sunlight_mountain",
        stages=("gpt_5_6_luna_sunlight_mountain",),
        selector=select_all_pass,
        metadata_globs=(
            "light_repo/sunlight_mountain/sunlight_mountain_*/pinterest_images.json",
        ),
    ),
)


def read_jsonl(path: Path) -> list[JsonObject]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            records.append(record)
    return records


def image_basename(record: JsonObject) -> str:
    image = record.get("image")
    if not isinstance(image, str) or not image:
        raise ValueError(f"VLM record has no image path: {record}")
    return Path(image).name


def index_stage(records: list[JsonObject], path: Path) -> dict[str, JsonObject]:
    indexed: dict[str, JsonObject] = {}
    for record in records:
        basename = image_basename(record)
        if basename in indexed:
            raise ValueError(f"Duplicate image basename {basename!r} in {path}")
        indexed[basename] = record
    return indexed


def metadata_identity(record: JsonObject) -> str | None:
    for key in ("id", "pin_uid"):
        value = record.get(key)
        if value is not None:
            return str(value)
    for key in ("local_path", "image_path"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return Path(value).stem
    return None


def load_metadata(
    repo_root: Path, globs: tuple[str, ...]
) -> dict[str, JsonObject]:
    indexed: dict[str, JsonObject] = {}
    for pattern in globs:
        for path in sorted(repo_root.glob(pattern)):
            with path.open("r", encoding="utf-8") as handle:
                records = json.load(handle)
            if not isinstance(records, list):
                raise ValueError(f"{path} must contain a JSON array")
            for record in records:
                if not isinstance(record, dict):
                    continue
                identity = metadata_identity(record)
                if identity is not None and identity not in indexed:
                    indexed[identity] = record
    return indexed


def original_image_path(
    basename: str, first_stage: dict[str, JsonObject]
) -> str:
    record = first_stage.get(basename)
    if record is None:
        raise ValueError(f"{basename} is missing from the first screening stage")
    image = record["image"]
    if "/light_repo/" not in image:
        raise ValueError(f"First-stage image is not in light_repo: {image}")
    return image


def export_category(
    repo_root: Path,
    output_dir: Path,
    config: CategoryConfig,
) -> JsonObject:
    run_root = repo_root / "vlm" / "output"
    stage_records: list[list[JsonObject]] = []
    stage_indexes: list[dict[str, JsonObject]] = []

    for stage in config.stages:
        summary_path = run_root / stage / "summary.jsonl"
        records = read_jsonl(summary_path)
        stage_records.append(records)
        stage_indexes.append(index_stage(records, summary_path))

    final_run_dir = run_root / config.stages[-1]
    final_path = final_run_dir / config.final_file
    final_records = read_jsonl(final_path)
    selector = config.selector or select_all_pass
    selected = [
        record for record in final_records if selector(record, final_run_dir)
    ]

    metadata = load_metadata(repo_root, config.metadata_globs)
    output_path = output_dir / f"{config.name}.jsonl"
    missing_metadata = 0

    with output_path.open("w", encoding="utf-8") as handle:
        for selection_rank, final_record in enumerate(selected, start=1):
            basename = image_basename(final_record)
            image_id = Path(basename).stem
            original_image = original_image_path(basename, stage_indexes[0])
            if not Path(original_image).is_file():
                raise FileNotFoundError(original_image)

            history = []
            for round_number, (stage, stage_index) in enumerate(
                zip(config.stages, stage_indexes), start=1
            ):
                result = stage_index.get(basename)
                if result is None:
                    raise ValueError(
                        f"{basename} is missing from screening stage {stage}"
                    )
                history.append(
                    {
                        "round": round_number,
                        "run": stage,
                        "result": result,
                    }
                )

            pinterest = metadata.get(image_id)
            if pinterest is None:
                missing_metadata += 1

            output_record = {
                "category": config.name,
                "image_id": image_id,
                "image": original_image,
                "screening_rounds": len(config.stages),
                "selection": config.selection_description,
                "selection_rank": selection_rank,
                "pinterest": pinterest,
                "screening_history": history,
            }
            handle.write(
                json.dumps(output_record, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )

    return {
        "category": config.name,
        "output": str(output_path),
        "screening_rounds": len(config.stages),
        "stages": list(config.stages),
        "selection": config.selection_description,
        "selected_count": len(selected),
        "missing_pinterest_metadata": missing_metadata,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the deepest passed VLM cases per light_repo category."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else repo_root / "light_repo" / "jsonl"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = [
        export_category(repo_root, output_dir, config) for config in CATEGORIES
    ]
    manifest = {
        "output_dir": str(output_dir),
        "category_count": len(summaries),
        "total_selected_count": sum(
            item["selected_count"] for item in summaries
        ),
        "categories": summaries,
        "ignored_runs": {
            "gpt_5_6_luna_autumn_forest_strong_light": (
                "Used the autumn-forest prompt on atmospheric_portrait_merged "
                "and produced zero passes."
            ),
            "gpt_5_6_luna_sky": (
                "Superseded by the stricter sky_subject_sun rerun on the same "
                "source directory."
            ),
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for item in summaries:
        print(
            f"{item['category']}: {item['selected_count']} cases, "
            f"{item['screening_rounds']} round(s)"
        )
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
