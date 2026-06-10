from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from web.annotations_v3 import assets, datasets, storage
from web.annotations_v3 import records


VALID_STAGES = {"rough", "fine", "label"}


def utc_now() -> float:
    return time.time()


def candidate_hash(item_ids: list[str]) -> str:
    payload = "\n".join(item_ids)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _records(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(storage.dataset_dir(dataset_id) / "records.json", {})


def _assignments_doc(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(storage.dataset_dir(dataset_id) / "assignments.json", {"version": 1, "blocks": []})


def _assignment_path(dataset_id: str):
    return storage.dataset_dir(dataset_id) / "assignments.json"


def _save_assignments(dataset_id: str, doc: dict[str, Any]) -> None:
    storage.write_json_atomic(_assignment_path(dataset_id), doc)


def stage_candidate_item_ids(dataset_id: str, stage: str) -> list[str]:
    if stage not in VALID_STAGES:
        raise ValueError("stage 必须是 rough、fine 或 label")
    items = datasets.load_items(dataset_id)
    ranks = datasets.item_rank_map(dataset_id)
    records = _records(dataset_id)
    if stage == "rough":
        candidates = [item["item_id"] for item in items]
    elif stage == "fine":
        candidates = [
            item["item_id"]
            for item in items
            if records.get(item["item_id"], {}).get("rough", {}).get("status") == "effective"
            and records.get(item["item_id"], {}).get("rough", {}).get("values", {}).get("quality", {}).get(
                "has_issue"
            )
            is False
        ]
    else:
        candidates = [
            item["item_id"]
            for item in items
            if records.get(item["item_id"], {}).get("sample", {}).get("sampled") is True
        ]
    return sorted(candidates, key=lambda item_id: ranks[item_id])


def _snapshots_path(dataset_id: str):
    return storage.dataset_dir(dataset_id) / "candidate_snapshots.json"


def _load_snapshots(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(_snapshots_path(dataset_id), {"version": 1, "snapshots": []})


def _completed_or_active_block_indexes(doc: dict[str, Any], stage: str, snapshot_id: str) -> set[int]:
    now = utc_now()
    indexes: set[int] = set()
    for block in doc.get("blocks", []):
        if block.get("stage") != stage or block.get("candidate_snapshot_id") != snapshot_id:
            continue
        if block.get("status") == "completed":
            indexes.add(int(block["block_index"]))
        if block.get("status") == "claimed" and block.get("expires_at", 0) > now:
            indexes.add(int(block["block_index"]))
    return indexes


def effective_stage_record(records_doc: dict[str, Any], item_id: str, stage: str) -> bool:
    return records_doc.get(item_id, {}).get(stage, {}).get("status") == "effective"


def stage_gate_open(dataset_id: str, records_doc: dict[str, Any], item_id: str, stage: str) -> bool:
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


def claimable_item_ids(dataset_id: str, item_ids: list[str], stage: str) -> tuple[list[str], list[dict[str, Any]]]:
    records_doc = _records(dataset_id)
    claimable: list[str] = []
    skipped: list[dict[str, Any]] = []
    for item_id in item_ids:
        if effective_stage_record(records_doc, item_id, stage):
            skipped.append({"item_id": item_id, "reason": "already_effective_record"})
            continue
        if not stage_gate_open(dataset_id, records_doc, item_id, stage):
            skipped.append({"item_id": item_id, "reason": "stage_gate_closed"})
            continue
        claimable.append(item_id)
    return claimable, skipped


def _snapshot_has_remaining_claimable_work(dataset_id: str, snapshot: dict[str, Any]) -> bool:
    doc = _assignments_doc(dataset_id)
    blocked_indexes = _completed_or_active_block_indexes(doc, snapshot["stage"], snapshot["snapshot_id"])
    item_ids = snapshot["item_ids"]
    block_size = int(snapshot["block_size"])
    for block_index, start in enumerate(range(0, len(item_ids), block_size)):
        if block_index in blocked_indexes:
            continue
        claimable, _ = claimable_item_ids(dataset_id, item_ids[start : start + block_size], snapshot["stage"])
        if claimable:
            return True
    return False


def get_or_create_candidate_snapshot(
    dataset_id: str,
    stage: str,
    block_size: int = 20,
    force_refresh: bool = False,
) -> dict[str, Any]:
    doc = _load_snapshots(dataset_id)
    stage_snapshots = [snap for snap in doc["snapshots"] if snap.get("stage") == stage]
    item_ids = stage_candidate_item_ids(dataset_id, stage)
    current_hash = candidate_hash(item_ids)
    if stage_snapshots and not force_refresh:
        latest = stage_snapshots[-1]
        if latest.get("candidate_hash") == current_hash or _snapshot_has_remaining_claimable_work(dataset_id, latest):
            return latest
    snapshot = {
        "snapshot_id": f"{stage}-snap-{len(stage_snapshots) + 1:04d}",
        "dataset_id": dataset_id,
        "stage": stage,
        "order_version": 1,
        "sample_version": None,
        "candidate_hash": current_hash,
        "created_at": utc_now(),
        "item_count": len(item_ids),
        "item_ids": item_ids,
        "block_size": block_size,
    }
    doc["snapshots"].append(snapshot)
    storage.write_json_atomic(_snapshots_path(dataset_id), doc)
    return snapshot


def _active_assignment_for_user(doc: dict[str, Any], stage: str, username: str) -> dict[str, Any] | None:
    now = utc_now()
    for block in doc.get("blocks", []):
        if (
            block.get("stage") == stage
            and block.get("username") == username
            and block.get("status") == "claimed"
            and block.get("expires_at", 0) > now
        ):
            return block
    return None


def _item_by_id(dataset_id: str) -> dict[str, dict[str, Any]]:
    return {item["item_id"]: item for item in datasets.load_items(dataset_id)}


def assignment_response(dataset_id: str, assignment: dict[str, Any]) -> dict[str, Any]:
    ranks = datasets.item_rank_map(dataset_id)
    items_by_id = _item_by_id(dataset_id)
    items = []
    for item_id in assignment["item_ids"]:
        item = dict(items_by_id[item_id])
        item["order_rank"] = ranks[item_id]
        item["image_assets"] = assets.asset_entry_for_item(dataset_id, item_id)
        item["annotation_context"] = records.annotation_context(
            dataset_id,
            item_id,
            assignment["stage"],
            assignment["username"],
        )
        items.append(item)
    items.sort(key=lambda item: item["order_rank"])
    next_index = assignment["block_index"] + 1
    return {
        "assignment": {key: value for key, value in assignment.items() if key != "item_ids"},
        "items": items,
        "next_prefetch": {"candidate_block_hint": f"{assignment['stage']}-b{next_index:04d}", "image_asset_urls": []},
    }


def claim_assignment(dataset_id: str, stage: str, username: str) -> dict[str, Any]:
    username = username.strip()
    if not username:
        raise ValueError("username 不能为空")
    with storage.dataset_lock(dataset_id):
        snapshot = get_or_create_candidate_snapshot(dataset_id, stage)
        doc = _assignments_doc(dataset_id)
        active = _active_assignment_for_user(doc, stage, username)
        if active is not None:
            return assignment_response(dataset_id, active)
        block_size = int(snapshot["block_size"])
        item_ids = snapshot["item_ids"]
        blocked_indexes = _completed_or_active_block_indexes(doc, stage, snapshot["snapshot_id"])
        for block_index, start in enumerate(range(0, len(item_ids), block_size)):
            if block_index in blocked_indexes:
                continue
            snapshot_item_ids = item_ids[start : start + block_size]
            block_item_ids, skipped_items = claimable_item_ids(dataset_id, snapshot_item_ids, stage)
            if not block_item_ids:
                continue
            ranks = datasets.item_rank_map(dataset_id)
            claimed_at = utc_now()
            assignment = {
                "assignment_id": f"{stage}-b{block_index:04d}-{uuid.uuid4().hex}",
                "dataset_id": dataset_id,
                "stage": stage,
                "order_version": 1,
                "candidate_snapshot_id": snapshot["snapshot_id"],
                "candidate_hash": snapshot["candidate_hash"],
                "block_index": block_index,
                "candidate_offset_start": start,
                "candidate_offset_end": start + len(snapshot_item_ids) - 1,
                "rank_start": min(ranks[item_id] for item_id in block_item_ids),
                "rank_end": max(ranks[item_id] for item_id in block_item_ids),
                "item_ids": block_item_ids,
                "username": username,
                "status": "claimed",
                "claimed_at": claimed_at,
                "expires_at": claimed_at + datasets.DEFAULT_CLAIM_TTL_SECONDS,
                "completed_count": 0,
                "total_count": len(block_item_ids),
                "skipped_item_ids": [entry["item_id"] for entry in skipped_items],
                "skipped_items": skipped_items,
            }
            doc["blocks"].append(assignment)
            _save_assignments(dataset_id, doc)
            return assignment_response(dataset_id, assignment)
    raise ValueError("没有可领取的 assignment block")


def get_assignment_items(dataset_id: str, assignment_id: str) -> dict[str, Any]:
    for block in _assignments_doc(dataset_id).get("blocks", []):
        if block.get("assignment_id") == assignment_id:
            return assignment_response(dataset_id, block)
    raise FileNotFoundError(assignment_id)


def release_assignment(dataset_id: str, assignment_id: str, username: str) -> dict[str, Any]:
    with storage.dataset_lock(dataset_id):
        doc = _assignments_doc(dataset_id)
        for block in doc.get("blocks", []):
            if block.get("assignment_id") == assignment_id:
                block["status"] = "released"
                block["released_at"] = utc_now()
                block["released_by"] = username
                _save_assignments(dataset_id, doc)
                return {"assignment_id": assignment_id, "status": "released"}
    raise FileNotFoundError(assignment_id)
