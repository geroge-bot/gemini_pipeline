from __future__ import annotations

import hashlib
import random
import time
import uuid
from pathlib import Path
from typing import Any

from web.annotations_v3 import storage


ORDER_VERSION = 1
DATASET_VERSION = 1
DEFAULT_BLOCK_SIZE = 20
DEFAULT_CLAIM_TTL_SECONDS = 7200


def utc_now() -> float:
    return time.time()


def stable_item_id(src_image: str, dst_image: str, source_row_id: str) -> str:
    payload = "\n".join([src_image.strip(), dst_image.strip(), source_row_id.strip()])
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def normalize_items(raw_rows: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
    for item_index, (line_number, row) in enumerate(raw_rows):
        src_image = str(row.get("src_image") or "").strip()
        dst_image = str(row.get("dst_image") or "").strip()
        if not src_image or not dst_image:
            raise ValueError(f"第 {line_number} 行缺少 src_image 或 dst_image")
        source_row_id = str(row.get("source_row_id") or f"row-{item_index + 1:06d}")
        item_id = str(row.get("item_id") or stable_item_id(src_image, dst_image, source_row_id))
        if item_id in seen_item_ids:
            raise ValueError(f"重复 item_id: {item_id}")
        seen_item_ids.add(item_id)
        item = {
            "item_id": item_id,
            "item_index": item_index,
            "source_row_id": source_row_id,
            "external_id": row.get("external_id"),
            "src_image": src_image,
            "dst_image": dst_image,
        }
        for optional_key in ("labels", "prompt", "prompt_path"):
            if optional_key in row:
                item[optional_key] = row[optional_key]
        items.append(item)
    if not items:
        raise ValueError("jsonl 文件为空")
    return items


def normalize_order_config(order_config: dict[str, Any] | None) -> dict[str, Any]:
    raw = order_config or {}
    mode = str(raw.get("mode") or "natural")
    if mode not in {"natural", "shuffled"}:
        raise ValueError("order.mode 必须是 natural 或 shuffled")
    seed = raw.get("seed")
    if mode == "shuffled" and not seed:
        seed = uuid.uuid4().hex
    return {
        "mode": mode,
        "seed": str(seed) if seed is not None else None,
        "scope": "dataset",
        "persist_manifest": True,
        "version": ORDER_VERSION,
    }


def build_order_manifest(
    dataset_id: str,
    items: list[dict[str, Any]],
    order_config: dict[str, Any],
    created_at: float,
) -> dict[str, Any]:
    ordered_items = list(items)
    if order_config["mode"] == "shuffled":
        rng = random.Random(order_config["seed"])
        rng.shuffle(ordered_items)
    order = [
        {"rank": rank, "item_id": item["item_id"], "item_index": item["item_index"]}
        for rank, item in enumerate(ordered_items)
    ]
    return {
        "version": ORDER_VERSION,
        "dataset_id": dataset_id,
        "mode": order_config["mode"],
        "seed": order_config["seed"],
        "created_at": created_at,
        "item_count": len(items),
        "order": order,
    }


def default_stage_config() -> dict[str, Any]:
    assignment = {
        "strategy": "candidate_ordered_blocks",
        "block_size": DEFAULT_BLOCK_SIZE,
        "claim_ttl_seconds": DEFAULT_CLAIM_TTL_SECONDS,
    }
    return {
        "rough": {"assignment": dict(assignment)},
        "fine": {"assignment": dict(assignment)},
        "label": {"assignment": dict(assignment)},
    }


def dataset_summary(dataset_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": dataset_doc["dataset_id"],
        "name": dataset_doc["name"],
        "created_at": dataset_doc["created_at"],
        "updated_at": dataset_doc["updated_at"],
        "item_count": dataset_doc["item_count"],
        "order": dataset_doc["order"],
    }


def load_state() -> dict[str, Any]:
    state = storage.read_json(storage.state_path(), {"version": 1, "datasets": []})
    if "datasets" not in state or not isinstance(state["datasets"], list):
        state = {"version": 1, "datasets": []}
    return state


def save_state_with_dataset(dataset_doc: dict[str, Any]) -> None:
    state = load_state()
    summary = dataset_summary(dataset_doc)
    state["datasets"] = [
        existing
        for existing in state["datasets"]
        if existing.get("dataset_id") != dataset_doc["dataset_id"]
    ]
    state["datasets"].append(summary)
    storage.write_json_atomic(storage.state_path(), state)


def create_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    source_jsonl = Path(str(payload.get("source_jsonl") or "")).expanduser()
    if not source_jsonl.exists() or not source_jsonl.is_file():
        raise ValueError(f"source_jsonl 不存在: {source_jsonl}")
    root_dir = Path(str(payload.get("root_dir") or source_jsonl.parent)).expanduser()
    raw_rows = storage.read_jsonl_objects(source_jsonl)
    items = normalize_items(raw_rows)
    order_config = normalize_order_config(payload.get("order"))
    dataset_id = str(payload.get("dataset_id") or uuid.uuid4())
    created_at = utc_now()
    dataset_doc = {
        "version": DATASET_VERSION,
        "dataset_id": dataset_id,
        "name": name,
        "created_at": created_at,
        "updated_at": created_at,
        "root_dir": str(root_dir.resolve()),
        "source_jsonl": str(source_jsonl.resolve()),
        "item_count": len(items),
        "order": order_config,
        "stages": default_stage_config(),
    }
    manifest = build_order_manifest(dataset_id, items, order_config, created_at)
    ds_dir = storage.dataset_dir(dataset_id)
    storage.write_json_atomic(ds_dir / "dataset.json", dataset_doc)
    storage.write_jsonl(ds_dir / "items.jsonl", items)
    storage.write_json_atomic(ds_dir / "order_manifest.json", manifest)
    storage.write_json_atomic(ds_dir / "candidate_snapshots.json", {"version": 1, "snapshots": []})
    storage.write_json_atomic(
        ds_dir / "labels_schema_snapshot.json",
        {"version": 1, "fields": [], "path_aliases": {}, "value_aliases": {}, "unknown_policy": "reject"},
    )
    storage.write_json_atomic(ds_dir / "records.json", {})
    storage.write_json_atomic(ds_dir / "assignments.json", {"version": 1, "blocks": []})
    storage.write_json_atomic(
        ds_dir / "preview_cache" / "manifest.json",
        {"version": 1, "dataset_id": dataset_id, "assets": {}},
    )
    save_state_with_dataset(dataset_doc)
    return dataset_doc


def _existing_dataset_path(dataset_id: str, filename: str) -> Path:
    path = storage.dataset_dir(dataset_id) / filename
    if not path.exists():
        raise FileNotFoundError(dataset_id)
    return path


def list_datasets() -> list[dict[str, Any]]:
    return load_state()["datasets"]


def get_dataset(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(_existing_dataset_path(dataset_id, "dataset.json"), {})


def get_order_manifest(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(_existing_dataset_path(dataset_id, "order_manifest.json"), {})


def load_items(dataset_id: str) -> list[dict[str, Any]]:
    return [row for _, row in storage.read_jsonl_objects(storage.dataset_dir(dataset_id) / "items.jsonl")]


def load_order_manifest(dataset_id: str) -> dict[str, Any]:
    return get_order_manifest(dataset_id)


def item_rank_map(dataset_id: str) -> dict[str, int]:
    manifest = load_order_manifest(dataset_id)
    return {entry["item_id"]: entry["rank"] for entry in manifest["order"]}
