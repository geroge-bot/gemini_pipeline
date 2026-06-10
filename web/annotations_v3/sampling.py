from __future__ import annotations

import random
import time
from copy import deepcopy
from typing import Any

from web.annotations_v3 import datasets, records, storage


MISSING_VALUE = "__missing__"


def utc_now() -> float:
    return time.time()


def get_nested(value: dict[str, Any], path: list[str]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return MISSING_VALUE
        current = current[part]
    return current


def bucket_key(item_values: dict[str, Any], selected_paths: list[list[str]]) -> str:
    parts = []
    for path in selected_paths:
        value = get_nested(item_values, path)
        if isinstance(value, list):
            value = ",".join(str(entry) for entry in sorted(value))
        parts.append(f"{'/'.join(path)}={value}")
    return "|".join(parts)


def item_values_for_sampling(
    dataset_id: str,
    item: dict[str, Any],
    records_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item_records = (records_doc or records.load_records(dataset_id)).get(item["item_id"], {})
    values = {"labels": deepcopy(item.get("labels", {})), "quality": {}}
    for stage in ("rough", "fine", "label"):
        record = item_records.get(stage)
        if record and record.get("status") == "effective":
            values.update(deepcopy(record.get("values", {})))
    return values


def sample_buckets(dataset_id: str, selected_paths: list[list[str]]) -> dict[str, Any]:
    records_doc = records.load_records(dataset_id)
    counts: dict[str, dict[str, Any]] = {}
    for item in datasets.load_items(dataset_id):
        key = bucket_key(item_values_for_sampling(dataset_id, item, records_doc), selected_paths)
        entry = counts.setdefault(key, {"bucket": key, "count": 0, "sampled_count": 0})
        entry["count"] += 1
        if records_doc.get(item["item_id"], {}).get("sample", {}).get("sampled") is True:
            entry["sampled_count"] += 1
    return {
        "selected_label_paths": selected_paths,
        "buckets": [counts[key] for key in sorted(counts)],
    }


def current_sample_version(records_doc: dict[str, Any]) -> int:
    versions = [
        int(value.get("sample", {}).get("sample_version", 0) or 0)
        for value in records_doc.values()
        if isinstance(value, dict)
    ]
    return max(versions or [0])


def release_stale_label_assignments(dataset_id: str, records_doc: dict[str, Any], sample_version: int) -> int:
    assignments_path = storage.dataset_dir(dataset_id) / "assignments.json"
    assignments_doc = storage.read_json(assignments_path, {"version": 1, "blocks": []})
    released = 0
    for block in assignments_doc.get("blocks", []):
        if block.get("stage") != "label" or block.get("status") != "claimed":
            continue
        remaining = [
            item_id
            for item_id in block.get("item_ids", [])
            if records_doc.get(item_id, {}).get("sample", {}).get("sample_version") == sample_version
            and records_doc.get(item_id, {}).get("sample", {}).get("sampled") is True
        ]
        if remaining:
            block["item_ids"] = remaining
            block["total_count"] = len(remaining)
            continue
        block["status"] = "released"
        block["released_at"] = utc_now()
        block["released_by"] = "sampling"
        block["released_reason"] = "sample_version_changed"
        released += 1
    storage.write_json_atomic(assignments_path, assignments_doc)
    return released


def run_sample(
    dataset_id: str,
    username: str,
    selected_paths: list[list[str]],
    per_bucket: int,
    seed: str | None = None,
) -> dict[str, Any]:
    if per_bucket < 1:
        raise ValueError("per_bucket 必须大于 0")
    with storage.dataset_lock(dataset_id):
        records_doc = records.load_records(dataset_id)
        sample_version = current_sample_version(records_doc) + 1
        previous_sample_version = sample_version - 1
        items = datasets.load_items(dataset_id)
        by_bucket: dict[str, list[str]] = {}
        for item in items:
            key = bucket_key(item_values_for_sampling(dataset_id, item, records_doc), selected_paths)
            by_bucket.setdefault(key, []).append(item["item_id"])

        rng = random.Random(seed or f"{dataset_id}-{sample_version}")
        selected: set[str] = set()
        selected_bucket: dict[str, str] = {}
        for key in sorted(by_bucket):
            ordered = list(by_bucket[key])
            rng.shuffle(ordered)
            for item_id in ordered[:per_bucket]:
                selected.add(item_id)
                selected_bucket[item_id] = key

        sampled_at = utc_now()
        carried_label_records = 0
        for item in items:
            item_id = item["item_id"]
            item_doc = records_doc.setdefault(item_id, {})
            previous_sample = item_doc.get("sample")
            if previous_sample:
                item_doc.setdefault("sample_history", []).append(deepcopy(previous_sample))
            was_sampled = previous_sample and previous_sample.get("sampled") is True
            now_sampled = item_id in selected
            if now_sampled and was_sampled and item_doc.get("label", {}).get("status") == "effective":
                item_doc["label"]["carried_from_sample_version"] = previous_sample.get(
                    "sample_version",
                    previous_sample_version,
                )
                carried_label_records += 1
            item_doc["sample"] = {
                "sample_version": sample_version,
                "sampled": now_sampled,
                "sample_bucket": selected_bucket.get(item_id),
                "selected_label_paths": selected_paths,
                "sampled_at": sampled_at,
                "sampled_by": username,
            }

        released = release_stale_label_assignments(dataset_id, records_doc, sample_version)
        records.save_records(dataset_id, records_doc)
        return {
            "sample_version": sample_version,
            "sampled_count": len(selected),
            "released_label_assignments": released,
            "carried_label_records": carried_label_records,
        }
