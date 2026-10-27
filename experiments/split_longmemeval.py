"""Create a deterministic, stratified split manifest for the public LongMemEval-S set.

The manifest contains question IDs and hashes only, never benchmark text. The
held-out partition is a project-local protocol holdout; LongMemEval itself is
public, so it is not a hidden or organizer-sealed test set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


SEED = "eviepoch-p2-split-v1-20261027"
PER_SPLIT = 10
EXPECTED_CATEGORIES = {
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_manifest(dataset_path: Path) -> dict:
    with dataset_path.open("r", encoding="utf-8") as stream:
        rows = json.load(stream)
    if not isinstance(rows, list) or not rows:
        raise ValueError("LongMemEval input must be a non-empty JSON array")

    groups: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("every LongMemEval row must be an object")
        question_id = row.get("question_id")
        category = row.get("question_type")
        if not isinstance(question_id, str) or not question_id or not isinstance(category, str):
            raise ValueError("every row needs a string question_id and question_type")
        if question_id in seen:
            raise ValueError(f"duplicate question_id: {question_id}")
        seen.add(question_id)
        groups[category].append(question_id)

    if set(groups) != EXPECTED_CATEGORIES:
        raise ValueError(f"unexpected LongMemEval categories: {sorted(groups)}")

    splits = {name: [] for name in ("dev", "validation", "held_out")}
    per_category = {}
    for category in sorted(groups):
        ordered = sorted(
            groups[category],
            key=lambda question_id: hashlib.sha256(
                f"{SEED}\0{category}\0{question_id}".encode("utf-8")
            ).hexdigest(),
        )
        if len(ordered) < PER_SPLIT * 2:
            raise ValueError(f"not enough questions in category {category}")
        splits["dev"].extend(ordered[:PER_SPLIT])
        splits["validation"].extend(ordered[PER_SPLIT : PER_SPLIT * 2])
        splits["held_out"].extend(ordered[PER_SPLIT * 2 :])
        per_category[category] = {
            "total": len(ordered),
            "dev": PER_SPLIT,
            "validation": PER_SPLIT,
            "held_out": len(ordered) - PER_SPLIT * 2,
        }

    for values in splits.values():
        values.sort()
    payload = {
        "protocol": "eviepoch-p2-longmemeval-s-split-v1",
        "split_seed": SEED,
        "source": {
            "dataset": "LongMemEval-S cleaned",
            "authors": "Di Wu, Hongwei Wang, Wenhao Yu, Yuwei Zhang, Kai-Wei Chang, Dong Yu",
            "paper": "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory, ICLR 2025",
            "repository": "https://github.com/xiaowu0162/LongMemEval",
            "dataset_url": "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
            "license": "MIT",
        },
        "dataset": {
            "filename": dataset_path.name,
            "sha256": sha256_file(dataset_path),
            "questions": len(rows),
            "question_types": dict(sorted(Counter(row["question_type"] for row in rows).items())),
        },
        "partition_rule": (
            "Within each question_type, sort by SHA-256(seed + NUL + question_type + NUL + question_id); "
            "first 10 dev, next 10 validation, remainder held_out. No benchmark text is copied."
        ),
        "partition_semantics": (
            "Project-local holdout only. The public question set is visible; held_out does not mean hidden or organizer-sealed."
        ),
        "per_category": per_category,
        "splits": splits,
    }
    payload["manifest_sha256"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).parent / "datasets" / ".cache" / "longmemeval_s_cleaned.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "datasets" / "longmemeval_split_v1.json",
    )
    args = parser.parse_args()
    manifest = build_manifest(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "manifest_sha256": manifest["manifest_sha256"],
        "dataset_sha256": manifest["dataset"]["sha256"],
        "split_sizes": {name: len(ids) for name, ids in manifest["splits"].items()},
        "per_category": manifest["per_category"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
