import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web.annotations.app import AnnotationStore, normalize_annotation_tags, utc_now


DEFAULT_SOURCE_TASK = "202604-美食数据-空标注"
DEFAULT_TARGET_TASK = "202604-美食数据"
PATH_ANCHORS = ("原始图片", "生成图片")


def normalize_image_path(value: Any, root_dir: Any = "") -> str:
    path = str(value or "").replace("\\", "/").strip().strip('"').strip("'")
    path = path.lstrip("./")
    root = str(root_dir or "").replace("\\", "/").strip().strip('"').strip("'").rstrip("/")
    if root and path.lower().startswith(root.lower() + "/"):
        path = path[len(root) + 1:]
    lowered = path.lower()
    for anchor in PATH_ANCHORS:
        marker = f"/{anchor.lower()}/"
        index = lowered.find(marker)
        if index >= 0:
            path = path[index + 1:]
            break
        if lowered.startswith(anchor.lower() + "/"):
            break
    return path.lstrip("./").lower()


def image_pair_key(item: dict[str, Any], root_dir: Any = "") -> tuple[str, str]:
    return (
        normalize_image_path(item.get("src_image"), root_dir),
        normalize_image_path(item.get("dst_image"), root_dir),
    )


def deep_merge_tags(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_tags(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def resolve_task(state: dict[str, Any], task_ref: str) -> dict[str, Any]:
    matches = [
        task
        for task in state.get("tasks", [])
        if str(task.get("id")) == task_ref or str(task.get("name")) == task_ref
    ]
    if not matches:
        raise ValueError(f"找不到任务: {task_ref}")
    if len(matches) > 1:
        raise ValueError(f"任务名称不唯一，请改用 task id: {task_ref}")
    return matches[0]


def build_target_index(
    items: list[dict[str, Any]],
    root_dir: Any = "",
) -> tuple[dict[tuple[str, str], int], int]:
    index: dict[tuple[str, str], int] = {}
    duplicate_keys: set[tuple[str, str]] = set()
    for item_index, item in enumerate(items):
        key = image_pair_key(item, root_dir)
        if key in duplicate_keys:
            continue
        if key in index:
            duplicate_keys.add(key)
            index.pop(key, None)
            continue
        index[key] = item_index
    return index, len(duplicate_keys)


def refresh_target_progress(
    store: AnnotationStore,
    state: dict[str, Any],
    target_task: dict[str, Any],
) -> None:
    annotations = store._read_annotations(target_task)
    annotated_indexes = {int(key) for key in annotations.keys()}
    for subtask in target_task.get("subtasks", []):
        subtask_annotations = [
            annotations[str(int(item_index))]
            for item_index in subtask.get("item_indexes", [])
            if int(item_index) in annotated_indexes
        ]
        count = len(subtask_annotations)
        subtask["completed_count"] = count
        if count > 0 and not subtask.get("assigned_to"):
            username = next(
                (
                    str(annotation.get("username")).strip()
                    for annotation in subtask_annotations
                    if str(annotation.get("username") or "").strip()
                ),
                "迁移导入",
            )
            subtask["assigned_to"] = username
            subtask["assigned_at"] = subtask.get("assigned_at") or utc_now()
        if count >= len(subtask.get("item_indexes", [])) and count > 0:
            subtask["completed_at"] = subtask.get("completed_at") or utc_now()
    target_task["annotation_count"] = len(annotations)
    target_task["annotations_storage"] = "items"
    store._write_state(state)


def transfer_annotations(
    state_path: str | os.PathLike[str],
    source_task: str = DEFAULT_SOURCE_TASK,
    target_task: str = DEFAULT_TARGET_TASK,
    apply: bool = False,
    overwrite: bool = False,
    *,
    source_state_path: str | os.PathLike[str] | None = None,
    target_state_path: str | os.PathLike[str] | None = None,
) -> dict[str, int]:
    target_state_path = target_state_path or state_path
    source_state_path = source_state_path or state_path
    source_store = AnnotationStore(source_state_path)
    target_store = AnnotationStore(target_state_path)
    source_state = source_store._read_state()
    target_state = target_store._read_state()
    source = resolve_task(source_state, source_task)
    target = resolve_task(target_state, target_task)
    source_items = source_store._read_items(source)
    target_items = target_store._read_items(target)
    source_annotations = source_store._read_annotations(source)
    target_annotations = target_store._read_annotations(target)
    target_index, duplicate_target_pairs = build_target_index(target_items, target.get("root_dir", ""))

    stats = {
        "source_annotations": len(source_annotations),
        "target_annotations_before": len(target_annotations),
        "transferred": 0,
        "skipped_existing": 0,
        "unmatched": 0,
        "duplicate_target_pairs": duplicate_target_pairs,
        "dry_run": 0 if apply else 1,
    }

    pending: dict[int, dict[str, Any]] = {}
    for source_key, source_annotation in sorted(source_annotations.items(), key=lambda pair: int(pair[0])):
        source_index = int(source_key)
        try:
            source_item = source_items[source_index]
        except IndexError:
            stats["unmatched"] += 1
            continue

        target_index_for_item = target_index.get(image_pair_key(source_item, source.get("root_dir", "")))
        if target_index_for_item is None:
            stats["unmatched"] += 1
            continue
        if not overwrite and str(target_index_for_item) in target_annotations:
            stats["skipped_existing"] += 1
            continue

        target_item = target_items[target_index_for_item]
        merged_tags = deep_merge_tags(target_item.get("labels", {}), source_annotation.get("tags", {}))
        annotation = deepcopy(source_annotation)
        annotation["item_index"] = target_index_for_item
        annotation["subtask_id"] = (
            target_store._subtask_for_item_index(target, target_index_for_item) or {}
        ).get("id", annotation.get("subtask_id"))
        annotation["tags"] = normalize_annotation_tags(merged_tags)
        annotation["transferred_from"] = {
            "task_id": source.get("id"),
            "task_name": source.get("name"),
            "item_index": source_index,
            "subtask_id": source_annotation.get("subtask_id"),
        }
        annotation["transferred_at"] = utc_now()
        pending[target_index_for_item] = annotation
        stats["transferred"] += 1

    if apply:
        for item_index, annotation in pending.items():
            target_store._write_json_file(target_store._annotation_item_path(target, item_index), annotation)
        refresh_target_progress(target_store, target_state, target)

    if apply:
        after_keys = set(target_annotations.keys()) | {str(item_index) for item_index in pending}
        stats["target_annotations_after"] = len(after_keys)
    else:
        stats["target_annotations_after"] = len(target_annotations)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Transfer annotation results between web annotation tasks.")
    parser.add_argument(
        "--state-path",
        default=str(Path("web") / "annotations" / "data" / "state.json"),
        help="Path to annotations state.json. Used for both source and target unless overridden.",
    )
    parser.add_argument(
        "--source-state-path",
        default="",
        help="Path to source annotations state.json, for example D:\\workspace\\data_tmp\\state.json.",
    )
    parser.add_argument(
        "--target-state-path",
        default="",
        help="Path to target annotations state.json. Defaults to --state-path.",
    )
    parser.add_argument("--source-task", default=DEFAULT_SOURCE_TASK, help="Source task name or id.")
    parser.add_argument("--target-task", default=DEFAULT_TARGET_TASK, help="Target task name or id.")
    parser.add_argument("--apply", action="store_true", help="Write transferred annotations. Without this, dry-run only.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing target annotations.")
    args = parser.parse_args()

    stats = transfer_annotations(
        state_path=args.state_path,
        source_state_path=args.source_state_path or None,
        target_state_path=args.target_state_path or None,
        source_task=args.source_task,
        target_task=args.target_task,
        apply=args.apply,
        overwrite=args.overwrite,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if not args.apply:
        print("Dry-run only. Re-run with --apply to write changes.")


if __name__ == "__main__":
    main()
