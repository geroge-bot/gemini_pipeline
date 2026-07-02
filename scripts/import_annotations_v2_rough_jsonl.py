"""Import rough-screening annotations into an existing annotations_v2 task.

Input JSONL rows must contain Chinese field names:

    原图, 生成图, MOS评分, 是否有质量问题, 评分人

The script dry-runs by default. Pass --apply to write rough records through
AnnotationV2Store so aggregation and gzip record shards stay consistent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web.annotations_v2.app import AnnotationV2Store, image_relative_path


DEFAULT_STATE_PATH = Path("web/annotations_v2/data/state.json")
REQUIRED_FIELDS = ("原图", "生成图", "MOS评分", "是否有质量问题", "评分人")


def normalize_task_ref(value: Any) -> str:
    return str(value or "").strip()


def task_display_name(task: dict[str, Any]) -> str:
    return f"{task.get('name', '')} ({task.get('id', '')})"


def format_available_tasks(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "当前 state 中没有注册任务"
    return "\n".join(f"- {task_display_name(task)}" for task in tasks)


def normalize_image_path(value: Any, root_dir: Any = "") -> str:
    raw = str(value or "").replace("\\", "/").strip().strip('"').strip("'")
    raw = raw.lstrip("./")
    root = str(root_dir or "").replace("\\", "/").strip().strip('"').strip("'").rstrip("/")
    if root and raw.lower().startswith(root.lower() + "/"):
        raw = raw[len(root) + 1 :]
    return raw.lstrip("./").lower()


def image_pair_keys(src_path: Any, dst_path: Any, root_dir: Any = "") -> set[tuple[str, str]]:
    keys = {
        (
            normalize_image_path(src_path, root_dir),
            normalize_image_path(dst_path, root_dir),
        )
    }
    keys.add(
        (
            normalize_image_path(image_relative_path(root_dir, src_path), root_dir),
            normalize_image_path(image_relative_path(root_dir, dst_path), root_dir),
        )
    )
    return {key for key in keys if key[0] and key[1]}


def resolve_task(store: AnnotationV2Store, task_ref: str) -> dict[str, Any]:
    normalized_ref = normalize_task_ref(task_ref)
    state = store._read_state()
    tasks = state.get("tasks", [])
    matches = [
        task
        for task in tasks
        if normalize_task_ref(task.get("id")) == normalized_ref or normalize_task_ref(task.get("name")) == normalized_ref
    ]
    if not matches:
        raise ValueError(
            "找不到 annotations_v2 任务: "
            f"{task_ref}\n"
            f"读取的 state: {store.state_path}\n"
            "可用任务:\n"
            f"{format_available_tasks(tasks)}"
        )
    if len(matches) > 1:
        raise ValueError(
            "任务名称不唯一，请改用 task id: "
            f"{task_ref}\n"
            f"{format_available_tasks(matches)}"
        )
    return matches[0]


def list_tasks(state_path: str | os.PathLike[str] = DEFAULT_STATE_PATH) -> list[dict[str, Any]]:
    store = AnnotationV2Store(state_path)
    return store._read_state().get("tasks", [])


def build_task_pair_index(
    items: list[dict[str, Any]],
    root_dir: Any = "",
) -> tuple[dict[tuple[str, str], int], int]:
    index: dict[tuple[str, str], int] = {}
    duplicate_keys: set[tuple[str, str]] = set()
    for item in items:
        try:
            item_index = int(item.get("item_index"))
        except (TypeError, ValueError):
            continue
        for key in image_pair_keys(item.get("src_image"), item.get("dst_image"), root_dir):
            if key in duplicate_keys:
                continue
            if key in index and index[key] != item_index:
                duplicate_keys.add(key)
                index.pop(key, None)
                continue
            index[key] = item_index
    return index, len(duplicate_keys)


def load_jsonl_rows(path: str | os.PathLike[str]) -> list[tuple[int, dict[str, Any]]]:
    jsonl_path = Path(path).expanduser()
    if not jsonl_path.exists() or not jsonl_path.is_file():
        raise ValueError(f"jsonl 文件不存在: {jsonl_path}")
    rows = []
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                rows.append((line_number, {"__error__": f"不是合法 JSON: {exc}"}))
                continue
            if not isinstance(row, dict):
                rows.append((line_number, {"__error__": "每行必须是 JSON 对象"}))
                continue
            rows.append((line_number, row))
    return rows


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "有", "是"}:
            return True
        if normalized in {"false", "0", "no", "n", "无", "否"}:
            return False
    raise ValueError("是否有质量问题必须是 bool 或 true/false 字符串")


def parse_mos(value: Any) -> int:
    try:
        mos = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("MOS评分必须是 1-5 的整数") from exc
    if mos < 1 or mos > 5:
        raise ValueError("MOS评分必须是 1-5 的整数")
    return mos


def parse_import_row(line_number: int, row: dict[str, Any]) -> dict[str, Any]:
    if "__error__" in row:
        raise ValueError(str(row["__error__"]))
    missing = [field for field in REQUIRED_FIELDS if row.get(field) in (None, "")]
    if missing:
        raise ValueError(f"缺少字段: {', '.join(missing)}")
    username = str(row.get("评分人") or "").strip()
    if not username:
        raise ValueError("评分人不能为空")
    return {
        "line": line_number,
        "src_image": str(row.get("原图") or "").strip(),
        "dst_image": str(row.get("生成图") or "").strip(),
        "username": username,
        "mos": parse_mos(row.get("MOS评分")),
        "has_defect": parse_bool(row.get("是否有质量问题")),
    }


def empty_stats(apply: bool) -> dict[str, Any]:
    return {
        "dry_run": not apply,
        "rows_seen": 0,
        "matched": 0,
        "imported": 0,
        "unmatched": 0,
        "invalid_rows": 0,
        "capacity_skipped": 0,
        "duplicate_input_pairs": 0,
        "duplicate_task_pairs": 0,
        "errors": [],
        "unmatched_rows": [],
    }


def append_limited(rows: list[dict[str, Any]], value: dict[str, Any], limit: int = 20) -> None:
    if len(rows) < limit:
        rows.append(value)


def import_rough_jsonl(
    jsonl_path: str | os.PathLike[str],
    task_ref: str,
    *,
    state_path: str | os.PathLike[str] = DEFAULT_STATE_PATH,
    apply: bool = False,
) -> dict[str, Any]:
    store = AnnotationV2Store(state_path)
    task = resolve_task(store, str(task_ref))
    items = store._read_items(task)
    pair_index, duplicate_task_pairs = build_task_pair_index(items, task.get("root_dir", ""))
    stats = empty_stats(apply)
    stats["duplicate_task_pairs"] = duplicate_task_pairs
    seen_input_pairs: set[tuple[str, str]] = set()

    for line_number, row in load_jsonl_rows(jsonl_path):
        stats["rows_seen"] += 1
        try:
            parsed = parse_import_row(line_number, row)
        except ValueError as exc:
            stats["invalid_rows"] += 1
            append_limited(stats["errors"], {"line": line_number, "error": str(exc)})
            continue

        keys = image_pair_keys(parsed["src_image"], parsed["dst_image"], task.get("root_dir", ""))
        canonical_key = sorted(keys)[0] if keys else ("", "")
        if canonical_key in seen_input_pairs:
            stats["duplicate_input_pairs"] += 1
        seen_input_pairs.add(canonical_key)

        item_index = next((pair_index[key] for key in keys if key in pair_index), None)
        if item_index is None:
            stats["unmatched"] += 1
            append_limited(
                stats["unmatched_rows"],
                {"line": line_number, "原图": parsed["src_image"], "生成图": parsed["dst_image"]},
            )
            continue

        stats["matched"] += 1
        if not apply:
            continue

        try:
            store.save_rough(
                task["id"],
                item_index,
                {
                    "username": parsed["username"],
                    "mos": parsed["mos"],
                    "has_defect": parsed["has_defect"],
                    "issues": [],
                    "primary_issue": "",
                    "other_issue": "",
                    "note": "",
                },
            )
        except ValueError as exc:
            if "标注人数已达到上限" in str(exc):
                stats["capacity_skipped"] += 1
                append_limited(stats["errors"], {"line": line_number, "error": str(exc)})
                continue
            raise
        stats["imported"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Import rough-screening JSONL rows into an annotations_v2 task.")
    parser.add_argument("--jsonl", help="Input JSONL path.")
    parser.add_argument("--task", help="Target annotations_v2 task id or unique name.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH), help="Path to annotations_v2 state.json.")
    parser.add_argument("--apply", action="store_true", help="Write records. Defaults to dry-run.")
    parser.add_argument("--list-tasks", action="store_true", help="List registered tasks from state.json and exit.")
    args = parser.parse_args()

    if args.list_tasks:
        print(format_available_tasks(list_tasks(args.state_path)))
        return
    if not args.jsonl or not args.task:
        parser.error("--jsonl and --task are required unless --list-tasks is used")

    result = import_rough_jsonl(args.jsonl, args.task, state_path=args.state_path, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print("Dry-run only. Re-run with --apply to write rough records.")


if __name__ == "__main__":
    main()
