#!/usr/bin/env python3
"""Convert successful fourth-batch Kupasi cases to Zoe multiturn JSON files."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


CATEGORY_OUTPUTS = {
    "累积编辑": "0718_kupasi_step_wise.json",
    "累积指代": "0718_kupasi_textual_reference.json",
    # Keep the historical repository spelling for compatibility.
    "隐式回滚": "0718_kupasi_implict_reference.json",
    "短指令": "0718_kupasi_short_instruction.json",
    "规则约束": "0718_kupasi_rule_constraint.json",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def task_from_record(root: Path, batch: Path, record: dict[str, Any]) -> Path | None:
    workflow = str(record.get("workflow", ""))
    original = record.get("moved_dir") or record.get("run_dir") or ""
    task_name = Path(str(original)).name
    if not workflow or not task_name:
        return None
    return root / batch.name / workflow / task_name


def s3_uri(path: Path, mount_root: Path, prefix: str) -> str:
    return prefix.rstrip("/") + "/" + path.relative_to(mount_root).as_posix()


def candidate_id(category: str, task: Path) -> str:
    return f"{category}|{task.as_posix()}"


def convert_candidate(
    job: tuple[str, Path, Path, str, Path],
) -> tuple[str, dict[str, Any] | None, str | None]:
    category, task, mount_root, prefix, _root = job
    item_id = candidate_id(category, task)
    prompts = read_json(task / "final_prompts.json")
    rounds = prompts.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        return item_id, None, "missing_or_invalid_final_prompts"

    files = prompts.get("files") if isinstance(prompts.get("files"), dict) else {}
    seed_name = files.get("seed_original") or "seed.jpg"
    seed = task / str(seed_name)
    try:
        with Image.open(seed) as image:
            width, height = image.size
    except Exception as exc:  # Pillow reports several format-specific exception types.
        return item_id, None, f"seed_open_failed:{type(exc).__name__}"

    edit_types: list[dict[str, str]] = []
    text_annots: list[list[str]] = []
    clip_paths: list[str] = []
    for position, round_info in enumerate(rounds, 1):
        if not isinstance(round_info, dict):
            return item_id, None, f"invalid_round:{position}"
        output_image = round_info.get("output_image")
        prompt_zh = round_info.get("prompt_zh_short")
        prompt_en = round_info.get("prompt_en_short")
        if not all(isinstance(value, str) and value.strip() for value in (output_image, prompt_zh, prompt_en)):
            return item_id, None, f"incomplete_round:{position}"
        edit_types.append(
            {
                "key": str(round_info.get("edit_type_key") or ""),
                "zh": str(round_info.get("edit_type_zh") or ""),
            }
        )
        text_annots.append([prompt_zh.strip(), prompt_en.strip()])
        clip_paths.append(s3_uri(task / output_image, mount_root, prefix))

    length = len(rounds)
    record = {
        "edit_category": category,
        "edit_type": edit_types,
        "reference_image": [[s3_uri(seed, mount_root, prefix)]] + [[] for _ in range(length - 1)],
        "width": width,
        "height": height,
        "length": length,
        "text_annot": text_annots,
        "clip_path": clip_paths,
    }
    return item_id, record, None


def load_candidates(root: Path, categories: set[str], include_early: bool) -> dict[str, list[Path]]:
    candidates: dict[str, list[Path]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for batch in sorted(path for path in root.iterdir() if path.is_dir() and path.name.startswith("batch_")):
        success_manifest = batch / "success_manifest.json"
        resume_plan = batch / "batch_resume_plan.json"
        if success_manifest.is_file():
            payload = read_json(success_manifest)
            records = payload.get("records", [])
        elif include_early and resume_plan.is_file():
            payload = read_json(resume_plan)
            records = payload.get("jobs", [])
        else:
            continue
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            category = str(record.get("workflow", ""))
            if category not in categories:
                continue
            task = task_from_record(root, batch, record)
            if task is None:
                continue
            key = (category, task.as_posix())
            if key not in seen:
                seen.add(key)
                candidates[category].append(task)
    return candidates


def load_checkpoint(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    completed: dict[str, dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    if not path.is_file():
        return completed, skipped
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_id = entry.get("id")
            if not isinstance(item_id, str):
                continue
            if isinstance(entry.get("record"), dict):
                completed[item_id] = entry["record"]
            elif isinstance(entry.get("error"), str):
                skipped[item_id] = entry["error"]
    return completed, skipped


def atomic_write_json(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(list(records), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="Extracted directory containing batch_* folders")
    parser.add_argument("--mount-root", type=Path, default=Path("/mnt/aoss"))
    parser.add_argument("--s3-prefix", default="s3://multi_turn_image_editing/")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--include-early", action="store_true", help="Validate and include early resume-plan jobs")
    parser.add_argument("--limit", type=int, default=0, help="Per-category test limit; 0 means all")
    parser.add_argument(
        "--categories",
        nargs="+",
        choices=tuple(CATEGORY_OUTPUTS),
        help="Only convert these categories; default converts every configured category",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    mount_root = args.mount_root.resolve()
    selected_outputs = {
        category: filename
        for category, filename in CATEGORY_OUTPUTS.items()
        if args.categories is None or category in args.categories
    }
    categories = set(selected_outputs)
    candidates = load_candidates(root, categories, args.include_early)
    if args.limit > 0:
        candidates = {category: tasks[: args.limit] for category, tasks in candidates.items()}

    print(
        "CANDIDATES "
        + " ".join(f"{category}={len(candidates.get(category, []))}" for category in selected_outputs),
        flush=True,
    )

    for category, filename in selected_outputs.items():
        tasks = candidates.get(category, [])
        output = args.output_dir / filename
        checkpoint = args.output_dir / f".{filename}.checkpoint.jsonl"
        completed, skipped = load_checkpoint(checkpoint)
        pending = [task for task in tasks if candidate_id(category, task) not in completed and candidate_id(category, task) not in skipped]
        print(
            f"START category={category} total={len(tasks)} resumed={len(completed)} "
            f"skipped={len(skipped)} pending={len(pending)} output={output}",
            flush=True,
        )
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        with checkpoint.open("a", encoding="utf-8", buffering=1) as checkpoint_handle:
            jobs = [(category, task, mount_root, args.s3_prefix, root) for task in pending]
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                for index, (item_id, record, error) in enumerate(pool.map(convert_candidate, jobs), 1):
                    if record is not None:
                        completed[item_id] = record
                        checkpoint_handle.write(json.dumps({"id": item_id, "record": record}, ensure_ascii=False) + "\n")
                    else:
                        skipped[item_id] = error or "unknown"
                        checkpoint_handle.write(json.dumps({"id": item_id, "error": skipped[item_id]}, ensure_ascii=False) + "\n")
                    if index % 100 == 0 or index == len(pending):
                        print(
                            f"PROGRESS category={category} processed={index}/{len(pending)} "
                            f"valid={len(completed)} skipped={len(skipped)}",
                            flush=True,
                        )

        ordered = [completed[candidate_id(category, task)] for task in tasks if candidate_id(category, task) in completed]
        atomic_write_json(output, ordered)
        print(
            f"DONE category={category} output={output} records={len(ordered)} skipped={len(skipped)}",
            flush=True,
        )

    print("ALL_DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("INTERRUPTED", file=sys.stderr, flush=True)
        raise
