from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PATH_FIELDS = ("root_dir", "jsonl_path", "label_dir", "generation_prompt_dir", "data_dir")
STATE_PATH_ENV = "ANNOTATIONS_V2_STATE_PATH"
DEFAULT_STATE_PATH = PROJECT_ROOT / "web" / "annotations_v2" / "data" / "state.json"


def normalized_path_text(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").rstrip("/")


def parse_path_mapping(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("路径映射必须是 OLD=NEW")
    old_root, new_root = value.split("=", 1)
    old_root = normalized_path_text(old_root)
    new_root = str(new_root or "").strip()
    if not old_root or not new_root:
        raise argparse.ArgumentTypeError("路径映射的 OLD 和 NEW 都不能为空")
    return old_root, new_root


def remap_path(value: Any, mappings: list[tuple[str, str]]) -> str:
    original = str(value or "").strip()
    normalized = normalized_path_text(original)
    if not normalized:
        return original
    normalized_lower = normalized.casefold()
    for old_root, new_root in mappings:
        old_normalized = normalized_path_text(old_root)
        old_lower = old_normalized.casefold()
        if normalized_lower != old_lower and not normalized_lower.startswith(f"{old_lower}/"):
            continue
        suffix = normalized[len(old_normalized):].lstrip("/")
        return str(Path(new_root).expanduser().joinpath(*([part for part in suffix.split("/") if part])))
    return original


def migrate_state(
    state: dict[str, Any],
    mappings: list[tuple[str, str]],
    data_root: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    migrated = json.loads(json.dumps(state, ensure_ascii=False))
    changes: list[dict[str, str]] = []
    configured_data_root = Path(data_root).expanduser() if data_root else None
    for task in migrated.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        for field in PATH_FIELDS:
            old_value = str(task.get(field) or "")
            if field == "data_dir" and configured_data_root is not None and task_id:
                new_value = str(configured_data_root / task_id)
            else:
                new_value = remap_path(old_value, mappings)
            if new_value == old_value:
                continue
            task[field] = new_value
            changes.append(
                {
                    "task_id": task_id,
                    "field": field,
                    "old": old_value,
                    "new": new_value,
                }
            )
    return migrated, changes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="迁移 annotations v2 任务中的绝对路径")
    parser.add_argument(
        "--state-path",
        default=os.environ.get(STATE_PATH_ENV) or str(DEFAULT_STATE_PATH),
        help="annotations v2 state.json 路径",
    )
    parser.add_argument(
        "--map",
        dest="mappings",
        action="append",
        default=[],
        type=parse_path_mapping,
        metavar="OLD=NEW",
        help="旧路径根到新路径根的映射，可重复指定",
    )
    parser.add_argument("--data-root", help="将所有 task.data_dir 重写到该目录下的 <task_id>")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预览变化")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.mappings and not args.data_root:
        raise SystemExit("至少需要一个 --map 或 --data-root")
    state_path = Path(args.state_path).expanduser()
    os.environ[STATE_PATH_ENV] = str(state_path)
    from web.annotations_v2.app import path_transaction_lock, read_json_file, write_json_file

    with path_transaction_lock(state_path):
        state = read_json_file(state_path, {"tasks": []})
        migrated, changes = migrate_state(state, args.mappings, args.data_root)
        print(json.dumps({"state_path": str(state_path), "change_count": len(changes), "changes": changes}, ensure_ascii=False, indent=2))
        if not args.apply or not changes:
            return 0
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = state_path.with_name(f"{state_path.name}.bak-{timestamp}")
        shutil.copy2(state_path, backup_path)
        write_json_file(state_path, migrated)
        print(f"已写入，备份：{backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
