from __future__ import annotations

import hashlib
import json
import time
import uuid
from copy import deepcopy
from typing import Any

from web.annotations_v3 import datasets, schema, storage
from web.annotations_v3.transactions import dataset_transaction


def utc_now() -> float:
    return time.time()


def load_records(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(storage.dataset_dir(dataset_id) / "records.json", {})


def save_records(dataset_id: str, records: dict[str, Any]) -> None:
    storage.write_json_atomic(storage.dataset_dir(dataset_id) / "records.json", records)


def record_version(record: dict[str, Any]) -> str:
    payload = json.dumps(record.get("values", {}), ensure_ascii=False, sort_keys=True)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def item_by_id(dataset_id: str, item_id: str) -> dict[str, Any]:
    for item in datasets.load_items(dataset_id):
        if item["item_id"] == item_id:
            return item
    raise FileNotFoundError(item_id)


def _set_nested(values: dict[str, Any], path: list[str], value: Any) -> None:
    target = values
    for part in path[:-1]:
        target = target.setdefault(part, {})
    target[path[-1]] = deepcopy(value)


def merged_draft(
    dataset_id: str,
    item: dict[str, Any],
    item_records: dict[str, Any],
    stage: str,
    user_record: dict[str, Any],
) -> dict[str, Any]:
    draft: dict[str, Any] = {"quality": {}, "labels": deepcopy(item.get("labels", {}))}
    for field in schema.fields_for_stage(dataset_id, stage):
        if "default_value" in field and field["default_value"] is not None:
            _set_nested(draft, field["path"], field["default_value"])
    if stage == "fine" and item_records.get("rough", {}).get("status") == "effective":
        draft.update(deepcopy(item_records["rough"].get("values", {})))
    if stage == "label" and item_records.get("fine", {}).get("status") == "effective":
        draft.update(deepcopy(item_records["fine"].get("values", {})))
    if user_record:
        draft.update(deepcopy(user_record.get("values", {})))
    return draft


def annotation_context(dataset_id: str, item_id: str, stage: str, username: str | None = None) -> dict[str, Any]:
    item = item_by_id(dataset_id, item_id)
    records = load_records(dataset_id)
    item_records = records.get(item_id, {})
    user_record = item_records.get(stage, {}) if item_records.get(stage, {}).get("username") == username else {}
    return {
        "annotation_schema_version": schema.load_schema_snapshot(dataset_id).get("version", 1),
        "fields": schema.fields_for_stage(dataset_id, stage),
        "values": {
            "original": {"labels": item.get("labels", {}), "quality": {}},
            "stage_results": {
                key: deepcopy(value)
                for key, value in item_records.items()
                if key in {"rough", "fine", "label"} and value.get("status") == "effective"
            },
            "user_record": deepcopy(user_record),
            "draft": merged_draft(dataset_id, item, item_records, stage, user_record),
        },
        "version": user_record.get("version"),
    }


class RecordServiceError(ValueError):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _load_assignments(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(storage.dataset_dir(dataset_id) / "assignments.json", {"version": 1, "blocks": []})


def _save_assignments(dataset_id: str, doc: dict[str, Any]) -> None:
    storage.write_json_atomic(storage.dataset_dir(dataset_id) / "assignments.json", doc)


def _load_snapshots(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(storage.dataset_dir(dataset_id) / "candidate_snapshots.json", {"version": 1, "snapshots": []})


def _snapshot_for_assignment(dataset_id: str, assignment: dict[str, Any]) -> dict[str, Any]:
    snapshots = _load_snapshots(dataset_id).get("snapshots", [])
    snapshot = next(
        (entry for entry in snapshots if entry.get("snapshot_id") == assignment.get("candidate_snapshot_id")),
        None,
    )
    if snapshot is None or snapshot.get("candidate_hash") != assignment.get("candidate_hash"):
        raise RecordServiceError("ASSIGNMENT_SNAPSHOT_MISMATCH", "assignment 候选快照不匹配", 409)
    return snapshot


def _stage_gate_open(records_doc: dict[str, Any], item_id: str, stage: str) -> bool:
    if stage == "rough":
        return True
    if stage == "fine":
        rough = records_doc.get(item_id, {}).get("rough", {})
        return rough.get("status") == "effective" and rough.get("values", {}).get("quality", {}).get(
            "has_issue"
        ) is False
    if stage == "label":
        sample = records_doc.get(item_id, {}).get("sample", {})
        return sample.get("sampled") is True
    return False


def save_annotation_patch(dataset_id: str, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    with dataset_transaction(dataset_id) as tx:
        stage = str(payload.get("stage") or "")
        username = str(payload.get("username") or "")
        assignment_id = str(payload.get("assignment_id") or "")
        assignments_doc = _load_assignments(dataset_id)
        assignment = next((block for block in assignments_doc["blocks"] if block.get("assignment_id") == assignment_id), None)
        if assignment is None or item_id not in assignment.get("item_ids", []):
            raise RecordServiceError("ITEM_NOT_IN_ASSIGNMENT", "item 不属于 assignment", 409)
        if assignment.get("username") != username:
            raise RecordServiceError("ASSIGNMENT_NOT_OWNED", "assignment 不属于当前用户", 403)
        if assignment.get("status") != "claimed" or assignment.get("expires_at", 0) <= utc_now():
            raise RecordServiceError("ASSIGNMENT_EXPIRED", "assignment 已过期", 410)
        snapshot = _snapshot_for_assignment(dataset_id, assignment)
        if item_id not in snapshot.get("item_ids", []):
            raise RecordServiceError("ITEM_NOT_IN_ASSIGNMENT", "item 不属于候选快照", 409)
        records_doc = load_records(dataset_id)
        if not _stage_gate_open(records_doc, item_id, stage):
            assignment.setdefault("skipped_item_ids", [])
            if item_id not in assignment["skipped_item_ids"]:
                assignment["skipped_item_ids"].append(item_id)
                assignment.setdefault("skipped_items", []).append({"item_id": item_id, "reason": "stage_gate_closed"})
                _save_assignments(dataset_id, assignments_doc)
            raise RecordServiceError("STAGE_GATE_CLOSED", "item 已不再满足当前阶段门禁", 422)
        item_records = records_doc.setdefault(item_id, {})
        existing = item_records.get(stage)
        if existing and existing.get("status") == "effective" and existing.get("assignment_id") != assignment_id:
            raise RecordServiceError("ITEM_ALREADY_COMPLETED", "item 已有有效记录", 409)
        base_version = payload.get("base_version")
        if existing and existing.get("version") != base_version:
            error = RecordServiceError("VERSION_CONFLICT", "base_version 与服务端版本不一致", 409)
            error.latest = existing
            raise error
        base_values = deepcopy(existing.get("values", {"quality": {}, "labels": {}})) if existing else {"quality": {}, "labels": {}}
        values = schema.apply_changes(base_values, schema.fields_for_stage(dataset_id, stage), payload.get("changes") or [])
        record = {
            "record_id": existing.get("record_id") if existing else str(uuid.uuid4()),
            "assignment_id": assignment_id,
            "username": username,
            "values": values,
            "version": "",
            "status": "effective",
            "updated_at": utc_now(),
        }
        record["version"] = record_version(record)
        item_records[stage] = record
        completed = 0
        for block_item_id in assignment.get("item_ids", []):
            if records_doc.get(block_item_id, {}).get(stage, {}).get("status") == "effective":
                completed += 1
        assignment["completed_count"] = completed
        assignment["status"] = "completed" if completed >= assignment.get("total_count", len(assignment.get("item_ids", []))) else "claimed"
        tx.stage_json(storage.dataset_dir(dataset_id) / "records.json", records_doc)
        tx.stage_json(storage.dataset_dir(dataset_id) / "assignments.json", assignments_doc)
        result = {
            "record": record,
            "assignment": {key: value for key, value in assignment.items() if key != "item_ids"},
        }
    assert result is not None
    result["annotation_context"] = annotation_context(dataset_id, item_id, stage, username)
    return result
