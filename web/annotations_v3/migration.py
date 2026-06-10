from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from web.annotations_v3 import datasets, records, storage


def load_v2_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def migrate_v2_items(v2_task_dir: Path, temp_jsonl: Path) -> int:
    raw_items = load_v2_json(v2_task_dir / "items.json", [])
    rows = []
    for index, item in enumerate(raw_items):
        rows.append(
            {
                "source_row_id": item.get("source_row_id") or f"v2-row-{index + 1:06d}",
                "external_id": item.get("external_id"),
                "src_image": item.get("src_image") or item.get("original_image"),
                "dst_image": item.get("dst_image") or item.get("generated_image"),
                "labels": item.get("labels") or item.get("object_labels") or {},
                "prompt": item.get("prompt"),
                "prompt_path": item.get("prompt_path"),
            }
        )
    storage.write_jsonl(temp_jsonl, rows)
    return len(rows)


def _effective_record(stage: str, values: dict[str, Any], username: str = "migration") -> dict[str, Any]:
    record = {
        "record_id": f"migration-{stage}",
        "assignment_id": "migration",
        "username": username or "migration",
        "values": values,
        "version": "",
        "status": "effective",
        "updated_at": time.time(),
    }
    record["version"] = records.record_version(record)
    return record


def write_migration_assignment(dataset_id: str, records_doc: dict[str, Any]) -> None:
    touched_item_ids = [
        item_id
        for item_id, item_doc in records_doc.items()
        if any(item_doc.get(stage, {}).get("assignment_id") == "migration" for stage in ("rough", "fine", "label"))
    ]
    if not touched_item_ids:
        return
    path = storage.dataset_dir(dataset_id) / "assignments.json"
    doc = storage.read_json(path, {"version": 1, "blocks": []})
    now = time.time()
    doc["blocks"].append(
        {
            "assignment_id": "migration",
            "dataset_id": dataset_id,
            "stage": "migration",
            "order_version": 1,
            "candidate_snapshot_id": None,
            "candidate_hash": None,
            "block_index": 0,
            "item_ids": touched_item_ids,
            "username": "migration",
            "status": "completed",
            "claimed_at": now,
            "completed_at": now,
            "completed_count": len(touched_item_ids),
            "total_count": len(touched_item_ids),
            "audit_only": True,
        }
    )
    storage.write_json_atomic(path, doc)


def write_migration_schema_snapshot(v2_task_dir: Path, dataset_id: str) -> None:
    explicit_schema = load_v2_json(v2_task_dir / "labels_schema_snapshot.json", None) or load_v2_json(
        v2_task_dir / "label_schema.json",
        None,
    )
    if explicit_schema:
        schema_doc = explicit_schema
        schema_doc.setdefault("version", 1)
        schema_doc.setdefault("source", "migration_v2")
    else:
        schema_doc = {
            "version": 1,
            "source": "migration_v2_inferred",
            "fields": [],
            "path_aliases": {},
            "value_aliases": {},
            "unknown_policy": "keep_raw",
        }
    storage.write_json_atomic(storage.dataset_dir(dataset_id) / "labels_schema_snapshot.json", schema_doc)


def migrate_v2_records(v2_task_dir: Path, dataset_id: str) -> dict[str, Any]:
    v2_records = load_v2_json(v2_task_dir / "records.json", {})
    v3_items = datasets.load_items(dataset_id)
    records_doc: dict[str, Any] = {}
    for item in v3_items:
        source = v2_records.get(str(item["item_index"]), {}) or v2_records.get(item["item_id"], {})
        item_doc: dict[str, Any] = {}
        if "rough" in source:
            rough = source["rough"]
            item_doc["rough"] = _effective_record(
                "rough",
                rough.get("values", rough) if isinstance(rough, dict) else rough,
                rough.get("username", "migration") if isinstance(rough, dict) else "migration",
            )
        if "fine" in source:
            fine = source["fine"]
            item_doc["fine"] = _effective_record(
                "fine",
                fine.get("values", fine) if isinstance(fine, dict) else fine,
                fine.get("username", "migration") if isinstance(fine, dict) else "migration",
            )
        legacy = {}
        if source.get("rough_annotations"):
            legacy["rough"] = source["rough_annotations"]
        if source.get("fine_annotations"):
            legacy["fine"] = source["fine_annotations"]
        if legacy:
            item_doc["legacy_annotations"] = legacy
        if source.get("sampled") is not None:
            item_doc["sample"] = {
                "sample_version": 1,
                "sampled": bool(source.get("sampled")),
                "sample_bucket": source.get("sample_bucket"),
                "sampled_at": time.time(),
                "sampled_by": "migration",
            }
        if source.get("label") or source.get("corrected_labels"):
            item_doc["label"] = _effective_record(
                "label",
                {"labels": source.get("corrected_labels") or source.get("label", {})},
                source.get("username", "migration"),
            )
        if item_doc:
            records_doc[item["item_id"]] = item_doc
    records.save_records(dataset_id, records_doc)
    write_migration_assignment(dataset_id, records_doc)
    write_migration_schema_snapshot(v2_task_dir, dataset_id)
    return {"migrated_records": len(records_doc)}


def migrate_v2_task(v2_task_dir: str, name: str | None = None) -> dict[str, Any]:
    task_dir = Path(v2_task_dir)
    temp_jsonl = task_dir / ".annotations_v3_migration_items.jsonl"
    migrated_items = migrate_v2_items(task_dir, temp_jsonl)
    dataset_doc = datasets.create_dataset(
        {
            "name": name or task_dir.name,
            "source_jsonl": str(temp_jsonl),
            "root_dir": str(task_dir),
            "order": {"mode": "natural"},
        }
    )
    record_counts = migrate_v2_records(task_dir, dataset_doc["dataset_id"])
    report = {
        "status": "completed",
        "dataset_id": dataset_doc["dataset_id"],
        "migrated_items": migrated_items,
        **record_counts,
        "warnings": [],
        "errors": [],
    }
    storage.write_json_atomic(storage.dataset_dir(dataset_doc["dataset_id"]) / "migration_report.json", report)
    return report
