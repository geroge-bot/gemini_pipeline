"""Batch migrate local annotation v1 tasks into annotation v2 tasks.

This tool reads v1 task state directly, creates matching v2 tasks, and writes
v2 per-item records through AnnotationV2Store so records land in the current
gzip shard format.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web.annotations.app import AnnotationStore
from web.annotations_v2.app import AnnotationV2Store, utc_now


PATH_ANCHORS = ("原始图片", "生成图片")
DEFAULT_V1_STATE = Path("web/annotations/data/state.json")
DEFAULT_V2_STATE = Path("web/annotations_v2/data/state.json")


def normalize_image_path(value: Any, root_dir: Any = "") -> str:
    path = str(value or "").replace("\\", "/").strip().strip('"').strip("'")
    path = path.lstrip("./")
    root = str(root_dir or "").replace("\\", "/").strip().strip('"').strip("'").rstrip("/")
    if root and path.lower().startswith(root.lower() + "/"):
        path = path[len(root) + 1 :]
    lowered = path.lower()
    for anchor in PATH_ANCHORS:
        marker = f"/{anchor.lower()}/"
        index = lowered.find(marker)
        if index >= 0:
            path = path[index + 1 :]
            break
        if lowered.startswith(anchor.lower() + "/"):
            break
    return path.lstrip("./").lower()


def image_pair_key(item: dict[str, Any], root_dir: Any = "") -> tuple[str, str]:
    return (
        normalize_image_path(item.get("src_image"), root_dir),
        normalize_image_path(item.get("dst_image"), root_dir),
    )


def resolve_task_refs(state: dict[str, Any], task_refs: list[str] | None) -> list[dict[str, Any]]:
    refs = task_refs or ["all"]
    if any(ref == "all" for ref in refs):
        return [task for task in state.get("tasks", []) if isinstance(task, dict)]
    tasks = []
    for task_ref in refs:
        matches = [
            task
            for task in state.get("tasks", [])
            if str(task.get("id")) == str(task_ref) or str(task.get("name")) == str(task_ref)
        ]
        if not matches:
            raise ValueError(f"找不到 v1 任务: {task_ref}")
        if len(matches) > 1:
            raise ValueError(f"v1 任务名称不唯一，请改用 task id: {task_ref}")
        tasks.append(matches[0])
    return tasks


def build_target_index(items: list[dict[str, Any]], root_dir: Any = "") -> tuple[dict[tuple[str, str], int], int]:
    index: dict[tuple[str, str], int] = {}
    duplicate_keys: set[tuple[str, str]] = set()
    for item in items:
        try:
            item_index = int(item.get("item_index"))
        except (TypeError, ValueError):
            continue
        key = image_pair_key(item, root_dir)
        if key in duplicate_keys:
            continue
        if key in index:
            duplicate_keys.add(key)
            index.pop(key, None)
            continue
        index[key] = item_index
    return index, len(duplicate_keys)


def v2_task_for_source(v2_state: dict[str, Any], v1_task: dict[str, Any]) -> dict[str, Any] | None:
    source_id = str(v1_task.get("id") or "")
    for task in v2_state.get("tasks", []):
        if str(task.get("source_v1_task_id") or "") == source_id:
            return task
    return None


def clean_mos(value: Any) -> int:
    try:
        mos = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("MOS 分必须在 1-5 之间") from exc
    if mos < 1 or mos > 5:
        raise ValueError("MOS 分必须在 1-5 之间")
    return mos


def timestamp_or_now(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return utc_now()


def screen_annotation_from_v1(annotation: dict[str, Any]) -> dict[str, Any]:
    username = str(annotation.get("username") or annotation.get("annotator") or annotation.get("labeler") or "imported").strip()
    return {
        "username": username or "imported",
        "mos": clean_mos(annotation.get("mos")),
        "has_defect": False,
        "primary_issue": "",
        "issues": [],
        "other_issue": "",
        "note": "",
        "updated_at": timestamp_or_now(annotation.get("updated_at")),
    }


def mark_v2_task_source(
    v2_store: AnnotationV2Store,
    v2_task_id: str,
    v1_task: dict[str, Any],
) -> dict[str, Any]:
    with v2_store._state_lock:
        state = v2_store._read_state()
        task = v2_store._find_task(state, v2_task_id)
        if not task:
            raise KeyError("v2 task not found after creation")
        task["source"] = "annotations_v1_batch_migration"
        task["source_v1_task_id"] = str(v1_task.get("id") or "")
        task["source_v1_task_name"] = str(v1_task.get("name") or "")
        task["migrated_at"] = utc_now()
        v2_store._write_state(state)
        return deepcopy(task)


def create_v2_task_from_v1(
    v2_store: AnnotationV2Store,
    v1_task: dict[str, Any],
    mos_pass_min: int,
) -> dict[str, Any]:
    created = v2_store.create_task(
        {
            "name": str(v1_task.get("name") or "").strip() or "annotations_v1_migrated",
            "root_dir": str(v1_task.get("root_dir") or ""),
            "jsonl_path": str(v1_task.get("jsonl_path") or ""),
            "label_dir": str(v1_task.get("annotation_dir") or ""),
            "rough": {
                "min_mos": mos_pass_min,
                "annotator_count": 1,
                "require_no_defect": True,
            },
            "fine": {
                "min_mos": mos_pass_min,
                "annotator_count": 1,
                "enable_defect": False,
            },
            "selected_label_paths": [],
        }
    )
    return mark_v2_task_source(v2_store, created["id"], v1_task)


def write_v2_record_from_v1_annotation(
    v2_store: AnnotationV2Store,
    v2_task: dict[str, Any],
    item_index: int,
    annotation: dict[str, Any],
) -> None:
    screen_annotation = screen_annotation_from_v1(annotation)

    def mutate(item_record: dict[str, Any]) -> None:
        item_record["rough_annotations"] = [deepcopy(screen_annotation)]
        item_record["rough"] = v2_store._aggregate_screen_annotations(v2_task, "rough", [screen_annotation])
        for field in ("fine_annotations", "fine", "sampled", "sample_bucket", "label", "label_revisions"):
            item_record.pop(field, None)

    v2_store._update_record(v2_task, item_index, mutate)


def migrate_one_task(
    v1_store: AnnotationStore,
    v2_store: AnnotationV2Store,
    v1_task: dict[str, Any],
    *,
    apply: bool,
    mos_pass_min: int,
    target_v2_task: dict[str, Any] | None = None,
) -> dict[str, int]:
    annotations = v1_store._read_annotations(v1_task)
    stats = {
        "annotations_seen": len(annotations),
        "annotations_migrated": 0,
        "unmatched_annotations": 0,
        "duplicate_target_pairs": 0,
    }
    if not apply:
        return stats

    v2_task = target_v2_task or create_v2_task_from_v1(v2_store, v1_task, mos_pass_min)
    v1_items = v1_store._read_items(v1_task)
    v2_items = v2_store._read_items(v2_task)
    target_index, duplicate_target_pairs = build_target_index(v2_items, v2_task.get("root_dir", ""))
    stats["duplicate_target_pairs"] = duplicate_target_pairs

    for source_key, annotation in sorted(annotations.items(), key=lambda pair: int(pair[0])):
        try:
            source_item = v1_items[int(source_key)]
        except (IndexError, ValueError):
            stats["unmatched_annotations"] += 1
            continue
        target_item_index = target_index.get(image_pair_key(source_item, v1_task.get("root_dir", "")))
        if target_item_index is None:
            stats["unmatched_annotations"] += 1
            continue
        write_v2_record_from_v1_annotation(
            v2_store,
            v2_task,
            target_item_index,
            annotation,
        )
        stats["annotations_migrated"] += 1
    return stats


def migrate_v1_tasks_to_v2(
    v1_state_path: str | os.PathLike[str] = DEFAULT_V1_STATE,
    v2_state_path: str | os.PathLike[str] = DEFAULT_V2_STATE,
    task_refs: list[str] | None = None,
    *,
    apply: bool = False,
    mos_pass_min: int = 4,
    repair_existing: bool = False,
) -> dict[str, int]:
    v1_store = AnnotationStore(v1_state_path)
    v2_store = AnnotationV2Store(v2_state_path)
    v1_state = v1_store._read_state()
    v2_state = v2_store._read_state()
    tasks = resolve_task_refs(v1_state, task_refs)
    stats = {
        "tasks_seen": len(tasks),
        "tasks_migrated": 0,
        "tasks_repaired_existing": 0,
        "tasks_skipped_existing": 0,
        "annotations_seen": 0,
        "annotations_migrated": 0,
        "unmatched_annotations": 0,
        "duplicate_target_pairs": 0,
        "dry_run": 0 if apply else 1,
    }

    for v1_task in tasks:
        task_stats = {
            "annotations_seen": len(v1_store._read_annotations(v1_task)),
            "annotations_migrated": 0,
            "unmatched_annotations": 0,
            "duplicate_target_pairs": 0,
        }
        existing_v2_task = v2_task_for_source(v2_state, v1_task)
        if existing_v2_task:
            if apply and repair_existing:
                task_stats = migrate_one_task(
                    v1_store,
                    v2_store,
                    v1_task,
                    apply=apply,
                    mos_pass_min=mos_pass_min,
                    target_v2_task=existing_v2_task,
                )
                stats["tasks_repaired_existing"] += 1
            else:
                stats["tasks_skipped_existing"] += 1
        else:
            task_stats = migrate_one_task(
                v1_store,
                v2_store,
                v1_task,
                apply=apply,
                mos_pass_min=mos_pass_min,
            )
            if apply:
                stats["tasks_migrated"] += 1
                v2_state = v2_store._read_state()
        stats["annotations_seen"] += task_stats["annotations_seen"]
        stats["annotations_migrated"] += task_stats["annotations_migrated"]
        stats["unmatched_annotations"] += task_stats["unmatched_annotations"]
        stats["duplicate_target_pairs"] += task_stats["duplicate_target_pairs"]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch migrate annotation v1 tasks into annotation v2.")
    parser.add_argument("--v1-state", default=str(DEFAULT_V1_STATE), help="Path to annotation v1 state.json.")
    parser.add_argument("--v2-state", default=str(DEFAULT_V2_STATE), help="Path to annotation v2 state.json.")
    parser.add_argument("--task", action="append", default=None, help="v1 task id/name to migrate, or all. Can be repeated.")
    parser.add_argument("--apply", action="store_true", help="Write v2 tasks and records. Without this flag, runs a dry-run.")
    parser.add_argument("--mos-pass-min", type=int, default=4, help="MOS pass threshold stored in the created v2 task config.")
    parser.add_argument(
        "--repair-existing",
        action="store_true",
        help="Rewrite already migrated v1-source v2 tasks as rough-only records instead of skipping them.",
    )
    args = parser.parse_args()
    stats = migrate_v1_tasks_to_v2(
        v1_state_path=args.v1_state,
        v2_state_path=args.v2_state,
        task_refs=args.task,
        apply=args.apply,
        mos_pass_min=args.mos_pass_min,
        repair_existing=args.repair_existing,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
