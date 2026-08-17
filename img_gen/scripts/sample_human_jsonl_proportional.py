#!/usr/bin/env python3
"""Sample a fixed number of records proportionally from AOSS JSONL files."""

from __future__ import annotations

import argparse
import configparser
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.config import Config


S3_PATH_RE = re.compile(
    r"^\s*path:\s*s3://(?P<bucket>[^/]+)/(?P<key>.+?_(?P<count>\d+)k\.jsonl)\s*$"
)


@dataclass(frozen=True)
class Source:
    language_file: str
    bucket: str
    key: str
    declared_count: int
    quota: int = 0

    @property
    def label(self) -> str:
        return f"{self.language_file}:{self.key.rsplit('/', 1)[-1]}"


def parse_sources(data_dir: Path) -> list[Source]:
    sources: list[Source] = []
    for language_file in ("human_zh.yaml", "human_en.yaml"):
        path = data_dir / language_file
        for line in path.read_text(encoding="utf-8").splitlines():
            match = S3_PATH_RE.match(line)
            if match is None:
                continue
            sources.append(
                Source(
                    language_file=language_file,
                    bucket=match.group("bucket"),
                    key=match.group("key"),
                    declared_count=int(match.group("count")) * 1000,
                )
            )
    if not sources:
        raise RuntimeError(f"No S3 JSONL sources found in {data_dir}")
    return sources


def allocate_quotas(sources: list[Source], target_count: int) -> list[Source]:
    total_weight = sum(source.declared_count for source in sources)
    if total_weight <= 0:
        raise RuntimeError("Total declared source count must be positive")

    raw_quotas = [
        target_count * source.declared_count / total_weight for source in sources
    ]
    quotas = [int(raw_quota) for raw_quota in raw_quotas]
    remainder = target_count - sum(quotas)
    ranked_indices = sorted(
        range(len(sources)),
        key=lambda index: raw_quotas[index] - quotas[index],
        reverse=True,
    )
    for index in ranked_indices[:remainder]:
        quotas[index] += 1
    return [
        Source(
            language_file=source.language_file,
            bucket=source.bucket,
            key=source.key,
            declared_count=source.declared_count,
            quota=quota,
        )
        for source, quota in zip(sources, quotas)
    ]


def make_s3_client(config_path: Path, cluster: str):
    config = configparser.ConfigParser()
    config.read(config_path)
    if cluster not in config:
        raise RuntimeError(f"Cluster section not found in {config_path}: {cluster}")
    section = config[cluster]
    endpoint = section.get("host_base")
    access_key = section.get("access_key")
    secret_key = section.get("secret_key")
    if not endpoint or not access_key or not secret_key:
        raise RuntimeError(f"Incomplete S3 configuration for cluster {cluster}")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            connect_timeout=30,
            read_timeout=120,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def sample_source(
    client,
    source: Source,
    rng: random.Random,
) -> tuple[list[bytes], int]:
    if source.quota == 0:
        return [], 0

    response = client.get_object(Bucket=source.bucket, Key=source.key)
    body: BinaryIO = response["Body"]
    reservoir: list[bytes] = []
    seen = 0
    try:
        for line in body.iter_lines():
            if not line:
                continue
            seen += 1
            if seen <= source.quota:
                reservoir.append(line)
                continue
            replacement_index = rng.randrange(seen)
            if replacement_index < source.quota:
                reservoir[replacement_index] = line
    finally:
        body.close()

    if seen < source.quota:
        raise RuntimeError(
            f"{source.label} contains only {seen} records, "
            f"but its proportional quota is {source.quota}"
        )
    return reservoir, seen


def write_output(output_path: Path, records: list[bytes]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temp_path.open("wb") as output:
        for record in records:
            output.write(record)
            output.write(b"\n")
    temp_path.replace(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("/mnt/aigc/houyuanchen/pinterest-dl/img_gen/data"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/mnt/aigc/houyuanchen/aoss_v2.conf"),
    )
    parser.add_argument("--cluster", default="malai")
    parser.add_argument("--target-count", type=int, default=50_000)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/mnt/aigc/houyuanchen/pinterest-dl/img_gen/data/human_sample_50k.jsonl"
        ),
    )
    parser.add_argument("--seed", type=int, default=20260817)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.target_count <= 0:
        raise ValueError("--target-count must be positive")

    sources = allocate_quotas(parse_sources(args.data_dir), args.target_count)
    client = make_s3_client(args.config, args.cluster)
    all_records: list[bytes] = []
    actual_counts: dict[str, int] = {}

    print(
        f"Sampling {args.target_count} records from {len(sources)} sources "
        f"with seed {args.seed}",
        file=sys.stderr,
    )
    for source_index, source in enumerate(sources):
        source_rng = random.Random(f"{args.seed}:{source_index}:{source.key}")
        sampled, seen = sample_source(client, source, source_rng)
        all_records.extend(sampled)
        actual_counts[source.label] = seen
        print(
            f"[{source_index + 1}/{len(sources)}] {source.label} "
            f"records={seen} quota={source.quota}",
            file=sys.stderr,
        )

    if len(all_records) != args.target_count:
        raise RuntimeError(
            f"Sampled {len(all_records)} records, expected {args.target_count}"
        )

    random.Random(args.seed).shuffle(all_records)
    write_output(args.output, all_records)
    print(f"Wrote {len(all_records)} records to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
