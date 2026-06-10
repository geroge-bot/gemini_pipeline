from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import platform
import random
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image
from web.annotations.label_options import LABEL_OPTION_GROUPS


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = APP_DIR / "data" / "state.json"
IMAGE_PREVIEW_MAX_EDGE = 1024
PREVIEW_CACHE_WORKERS = 16
STATE_PATH_ENV = "ANNOTATIONS_V2_STATE_PATH"
DATA_DIR_ENV = "ANNOTATIONS_V2_DATA_DIR"
PREVIEW_CACHE_DIR_ENV = "ANNOTATIONS_V2_PREVIEW_CACHE_DIR"
INPUT_GROUP_NAME = "输入图"
OUTPUT_GROUP_NAME = "输出图"
VALID_VISUALIZATION_STAGES = {"rough", "fine", "sample", "label"}
TASK_DELETE_ADMIN_USERNAME = "孙本猿"
LABEL_CLAIM_TTL_SECONDS = 30 * 60
JSON_WRITE_LOCKS: dict[Path, threading.RLock] = {}
JSON_WRITE_LOCKS_GUARD = threading.Lock()
CANONICAL_LABEL_DIMENSIONS = {
    str(group["name"]): {
        str(dimension["name"])
        for dimension in group.get("dimensions", [])
        if isinstance(dimension, dict)
    }
    for group in LABEL_OPTION_GROUPS
}


def default_server_host() -> str:
    system_name = platform.system()
    if system_name == "Linux":
        return "0.0.0.0"
    return "127.0.0.1"


def utc_now() -> float:
    return time.time()


def image_relative_path(root_dir: str | os.PathLike[str] | None, image_path: Any) -> str:
    raw_value = str(image_path or "")
    raw_path = Path(raw_value)
    if not raw_path.is_absolute():
        return raw_value
    try:
        return raw_path.relative_to(Path(root_dir or "")).as_posix()
    except (ValueError, RuntimeError):
        return raw_value


def load_jsonl(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    jsonl_path = Path(path).expanduser()
    if not jsonl_path.exists() or not jsonl_path.is_file():
        raise ValueError(f"jsonl 文件不存在：{jsonl_path}")
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行不是合法 JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"第 {line_number} 行必须是 JSON 对象")
            if "src_image" not in row or "dst_image" not in row:
                raise ValueError(f"第 {line_number} 行缺少 src_image 或 dst_image")
            rows.append(row)
    if not rows:
        raise ValueError("jsonl 文件为空")
    return rows


def load_import_jsonl(path: str | os.PathLike[str]) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    jsonl_path = Path(path).expanduser()
    if not jsonl_path.exists() or not jsonl_path.is_file():
        raise ValueError(f"导入 jsonl 文件不存在：{jsonl_path}")
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"导入文件第 {line_number} 行不是合法 JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"导入文件第 {line_number} 行必须是 JSON 对象")
            rows.append((line_number, row))
    if not rows:
        raise ValueError("导入 jsonl 文件为空")
    return rows


def read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_write_lock(path: Path) -> threading.RLock:
    resolved_path = path.resolve()
    with JSON_WRITE_LOCKS_GUARD:
        lock = JSON_WRITE_LOCKS.get(resolved_path)
        if lock is None:
            lock = threading.RLock()
            JSON_WRITE_LOCKS[resolved_path] = lock
        return lock


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with json_write_lock(path):
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def label_json_path(root_dir: Path, label_dir: Path, image_path: str) -> Path:
    raw_path = Path(str(image_path))
    image_full_path = raw_path if raw_path.is_absolute() else root_dir / raw_path
    try:
        relative_path = image_full_path.relative_to(root_dir)
    except ValueError:
        relative_path = Path(raw_path.name)
    return label_dir / relative_path.with_suffix(".json")


def read_image_labels(root_dir: Path, label_dir: Path, image_path: str) -> dict[str, Any]:
    if not str(label_dir):
        return {}
    path = label_json_path(root_dir, label_dir, image_path)
    if not path.exists() or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return {}
    labels = data.get("labels")
    return labels if isinstance(labels, dict) else data


def sanitize_label_group(group_name: str, labels: dict[str, Any]) -> dict[str, Any]:
    allowed_dimensions = CANONICAL_LABEL_DIMENSIONS.get(str(group_name), set())
    return {
        str(key): value
        for key, value in (labels or {}).items()
        if str(key) in allowed_dimensions
    }


def sanitize_labels(labels: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(labels, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for group_name, group_labels in labels.items():
        if not isinstance(group_labels, dict):
            continue
        group_cleaned = sanitize_label_group(str(group_name), group_labels)
        if group_cleaned:
            cleaned[str(group_name)] = group_cleaned
    return cleaned


def merge_labels(target: dict[str, Any], default_group: str, labels: dict[str, Any]) -> None:
    if not labels:
        return
    known_groups = {INPUT_GROUP_NAME, OUTPUT_GROUP_NAME, "Pair"}
    if any(group in labels for group in known_groups):
        for group, value in labels.items():
            if isinstance(value, dict):
                cleaned = sanitize_label_group(str(group), value)
                if cleaned:
                    target.setdefault(str(group), {}).update(cleaned)
        return
    cleaned = sanitize_label_group(default_group, labels)
    if cleaned:
        target.setdefault(default_group, {}).update(cleaned)


def normalize_label_paths(value: Any) -> list[list[str]]:
    if isinstance(value, str):
        raw_paths = [part.strip() for part in value.replace("\n", ",").split(",")]
        value = [path.split("/") for path in raw_paths if path]
    if not isinstance(value, list):
        return []
    paths = []
    for raw_path in value:
        if isinstance(raw_path, str):
            parts = raw_path.split("/")
        elif isinstance(raw_path, list):
            parts = raw_path
        else:
            continue
        normalized = [str(part).strip() for part in parts if str(part).strip()]
        if normalized and normalized not in paths:
            paths.append(normalized)
    return paths


def normalize_issue_options(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace("\n", ",").split(",")
    if not isinstance(value, list):
        return []
    options = []
    for item in value:
        option = str(item or "").strip()
        if option and option not in options:
            options.append(option)
    return options


def normalize_filter_values(values: Any) -> set[str]:
    if values is None:
        return set()
    if not isinstance(values, list):
        values = [values]
    return {str(value) for value in values if value not in (None, "")}


def nested_get(value: Any, path: list[str]) -> Any:
    cursor = value
    for part in path:
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def stat_values(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    return [str(item) for item in raw_values if item not in (None, "")]


def flatten_label_paths(value: Any, prefix: list[str] | None = None) -> list[list[str]]:
    prefix = prefix or []
    if not isinstance(value, dict):
        return []
    paths = []
    for key, child in value.items():
        current_path = [*prefix, str(key)]
        if isinstance(child, dict):
            paths.extend(flatten_label_paths(child, current_path))
        else:
            paths.append(current_path)
    return paths


def nested_set(target: dict[str, Any], path: list[str], value: Any) -> None:
    cursor = target
    for part in path[:-1]:
        cursor = cursor.setdefault(part, {})
    if path:
        cursor[path[-1]] = value


def nested_overlay(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            child = target.setdefault(str(key), {})
            if isinstance(child, dict):
                nested_overlay(child, value)
            else:
                target[str(key)] = deepcopy(value)
        else:
            target[str(key)] = deepcopy(value)


def clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是"}
    return bool(value)


def clean_mos(value: Any) -> int:
    try:
        mos = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("MOS 分必须在 1-5 之间") from exc
    if mos < 1 or mos > 5:
        raise ValueError("MOS 分必须在 1-5 之间")
    return mos


def clean_positive_int(value: Any, default: int = 1) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(1, result)


def normalized_import_username(payload: dict[str, Any], default: str = "") -> str:
    return str(
        payload.get("username")
        or payload.get("annotator")
        or payload.get("labeler")
        or default
        or ""
    ).strip()


def safe_download_name(name: str, suffix: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in name.strip())
    return f"{safe or 'annotations_v2'}_{suffix}"


def clean_optional_path(value: Any) -> str:
    return str(value or "").strip()


def stringify_generation_prompt(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def generation_prompt_relative_json_path(root_dir: Path, dst_image: Any) -> Path:
    raw_path = Path(str(dst_image or ""))
    image_path = raw_path if raw_path.is_absolute() else root_dir / raw_path
    try:
        relative_path = image_path.relative_to(root_dir)
    except ValueError:
        relative_path = Path(raw_path.name) if raw_path.is_absolute() else raw_path
    return relative_path.with_suffix(".json")


def generation_prompt_candidates(prompt_dir: Path, root_dir: Path, dst_image: Any) -> list[Path]:
    relative_json_path = generation_prompt_relative_json_path(root_dir, dst_image)
    candidates = [prompt_dir / relative_json_path]
    parts = relative_json_path.parts
    if len(parts) > 1:
        candidates.append(prompt_dir.joinpath(*parts[1:]))
    candidates.append(prompt_dir / relative_json_path.name)

    unique_candidates = []
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates


def build_generation_prompt_filename_index(prompt_dir: Path) -> dict[str, Path]:
    if not prompt_dir.exists() or not prompt_dir.is_dir():
        return {}
    index: dict[str, Path] = {}
    for path in prompt_dir.rglob("*.json"):
        index.setdefault(path.name, path)
    return index


def read_generation_prompt_json(path: Path) -> str:
    try:
        data = read_json_file(path, {})
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return stringify_generation_prompt(data.get("original_plan"))


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def preview_cache_key(path: Path, max_edge: int = IMAGE_PREVIEW_MAX_EDGE) -> str:
    resolved = path.resolve()
    stat = resolved.stat()
    raw_key = f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}|{max_edge}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def cached_preview_path(cache_dir: Path, cache_key: str) -> Path | None:
    if not cache_dir.exists():
        return None
    return next(
        (
            candidate
            for candidate in cache_dir.glob(f"{cache_key}.*")
            if candidate.is_file() and candidate.stem == cache_key
        ),
        None,
    )


def preview_cache_index(cache_dir: Path) -> dict[str, Path]:
    if not cache_dir.exists():
        return {}
    return {
        candidate.stem: candidate
        for candidate in cache_dir.iterdir()
        if candidate.is_file()
        and len(candidate.stem) == 64
        and all(char in "0123456789abcdef" for char in candidate.stem)
    }


def preview_cache_status_path(cache_dir: Path) -> Path:
    return cache_dir / "cache_status.json"


def resized_image_file(path: Path, cache_dir: Path, max_edge: int = IMAGE_PREVIEW_MAX_EDGE) -> tuple[Path, str]:
    mimetype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    cache_key = preview_cache_key(path, max_edge)
    cached_path = cached_preview_path(cache_dir, cache_key)
    if cached_path:
        return cached_path.resolve(), mimetypes.guess_type(str(cached_path))[0] or mimetype

    with Image.open(path) as image:
        image.load()
        if max(image.size) <= max_edge:
            return path.resolve(), mimetype

        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        image_format = (image.format or path.suffix.lstrip(".") or "JPEG").upper()
        if image_format == "JPG":
            image_format = "JPEG"
        if image_format == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        suffix = ".jpg" if image_format == "JPEG" else f".{image_format.lower()}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{cache_key}{suffix}"
        if cache_path.exists():
            return cache_path.resolve(), Image.MIME.get(image_format, mimetype)

        tmp_path = cache_path.with_name(f"{cache_path.name}.{threading.get_ident()}.tmp")
        image.save(tmp_path, format=image_format, quality=88, optimize=True)
        os.replace(tmp_path, cache_path)
        return cache_path.resolve(), Image.MIME.get(image_format, mimetype)


def require_task_delete_admin(payload: dict[str, Any]) -> None:
    username = str(payload.get("username") or "").strip()
    if username != TASK_DELETE_ADMIN_USERNAME:
        raise PermissionError(f"只有{TASK_DELETE_ADMIN_USERNAME}可以删除任务")


class AnnotationV2Store:
    def __init__(
        self,
        state_path: str | os.PathLike[str] | None = None,
        data_root: str | os.PathLike[str] | None = None,
        preview_cache_dir: str | os.PathLike[str] | None = None,
    ):
        configured_state_path = state_path or os.environ.get(STATE_PATH_ENV) or DEFAULT_STATE_PATH
        configured_data_root = data_root or os.environ.get(DATA_DIR_ENV)
        configured_preview_cache_dir = preview_cache_dir or os.environ.get(PREVIEW_CACHE_DIR_ENV)

        self.state_path = Path(configured_state_path).expanduser()
        self.task_data_root = (
            Path(configured_data_root).expanduser()
            if configured_data_root
            else self.state_path.parent / "tasks"
        )
        self.preview_cache_root = (
            Path(configured_preview_cache_dir).expanduser()
            if configured_preview_cache_dir
            else None
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write_state({"tasks": []})

    def _read_state(self) -> dict[str, Any]:
        return read_json_file(self.state_path, {"tasks": []})

    def _write_state(self, state: dict[str, Any]) -> None:
        write_json_file(self.state_path, state)

    def _task_data_dir(self, task_id: str) -> Path:
        return self.task_data_root / task_id

    def preview_cache_dir(self, task_id: str) -> Path:
        if self.preview_cache_root:
            return self.preview_cache_root / task_id
        return self._task_data_dir(task_id) / "preview_cache"

    def _items_path(self, task: dict[str, Any]) -> Path:
        return Path(task["data_dir"]) / "items.json"

    def _records_path(self, task: dict[str, Any]) -> Path:
        return Path(task["data_dir"]) / "records.json"

    def _find_task(self, state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
        return next((task for task in state.get("tasks", []) if task.get("id") == task_id), None)

    def _require_task(self, task_id: str) -> dict[str, Any]:
        task = self._find_task(self._read_state(), task_id)
        if not task:
            raise KeyError("task not found")
        return task

    def _read_items(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        items = read_json_file(self._items_path(task), [])
        for item in items:
            if isinstance(item, dict):
                item["labels"] = sanitize_labels(item.get("labels", {}))
                item.setdefault("generation_prompt", "")
                item.setdefault("generation_prompt_json_path", "")
        return items

    def _generation_prompt_for_item(
        self,
        prompt_dir: Path,
        root_dir: Path,
        item: dict[str, Any],
        filename_index: dict[str, Path],
    ) -> tuple[str, str]:
        for candidate in generation_prompt_candidates(prompt_dir, root_dir, item.get("dst_image")):
            if candidate.exists() and candidate.is_file():
                prompt = read_generation_prompt_json(candidate)
                return prompt, str(candidate) if prompt else ""

        fallback_name = generation_prompt_relative_json_path(root_dir, item.get("dst_image")).name
        fallback_path = filename_index.get(fallback_name)
        if fallback_path:
            prompt = read_generation_prompt_json(fallback_path)
            return prompt, str(fallback_path) if prompt else ""
        return "", ""

    def _apply_generation_prompts(self, task: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prompt_dir_value = clean_optional_path(task.get("generation_prompt_dir"))
        if not prompt_dir_value:
            for item in items:
                item["generation_prompt"] = ""
                item["generation_prompt_json_path"] = ""
            return items

        prompt_dir = Path(prompt_dir_value).expanduser()
        root_dir = Path(str(task.get("root_dir") or "")).expanduser()
        filename_index: dict[str, Path] | None = None
        for item in items:
            prompt = ""
            prompt_path = ""
            for candidate in generation_prompt_candidates(prompt_dir, root_dir, item.get("dst_image")):
                if candidate.exists() and candidate.is_file():
                    prompt = read_generation_prompt_json(candidate)
                    prompt_path = str(candidate) if prompt else ""
                    break
            if not prompt:
                if filename_index is None:
                    filename_index = build_generation_prompt_filename_index(prompt_dir)
                prompt, prompt_path = self._generation_prompt_for_item(
                    prompt_dir,
                    root_dir,
                    item,
                    filename_index,
                )
            item["generation_prompt"] = prompt
            item["generation_prompt_json_path"] = prompt_path
        return items

    def _refresh_generation_prompts(self, task: dict[str, Any]) -> None:
        items = self._read_items(task)
        self._apply_generation_prompts(task, items)
        write_json_file(self._items_path(task), items)

    def _read_records(self, task: dict[str, Any]) -> dict[str, Any]:
        records = read_json_file(self._records_path(task), {})
        return records if isinstance(records, dict) else {}

    def _write_records(self, task: dict[str, Any], records: dict[str, Any]) -> None:
        write_json_file(self._records_path(task), records)

    def _stage_target(self, task: dict[str, Any], stage: str) -> int:
        return clean_positive_int(task.get(stage, {}).get("annotator_count"), 1)

    def _screen_annotations(self, record: dict[str, Any], stage: str) -> list[dict[str, Any]]:
        annotations = record.get(f"{stage}_annotations")
        if isinstance(annotations, list):
            return [deepcopy(annotation) for annotation in annotations if isinstance(annotation, dict)]
        legacy = record.get(stage)
        return [deepcopy(legacy)] if isinstance(legacy, dict) else []

    def _annotation_for_user(self, record: dict[str, Any], stage: str, username: str) -> dict[str, Any] | None:
        for annotation in self._screen_annotations(record, stage):
            if annotation.get("username") == username:
                return annotation
        return None

    def _annotation_count(self, record: dict[str, Any], stage: str) -> int:
        usernames = {
            str(annotation.get("username") or "")
            for annotation in self._screen_annotations(record, stage)
            if annotation.get("username")
        }
        return len(usernames)

    def _stage_complete(self, task: dict[str, Any], record: dict[str, Any], stage: str) -> bool:
        return self._annotation_count(record, stage) >= self._stage_target(task, stage)

    def _aggregate_screen_annotations(
        self,
        task: dict[str, Any],
        stage: str,
        annotations: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not annotations:
            return None
        issues = []
        for annotation in annotations:
            for issue in annotation.get("issues") or []:
                if issue not in issues:
                    issues.append(issue)
        primary_issue = next((annotation.get("primary_issue") for annotation in annotations if annotation.get("primary_issue")), "")
        other_issue = "；".join(
            str(annotation.get("other_issue") or "").strip()
            for annotation in annotations
            if str(annotation.get("other_issue") or "").strip()
        )
        note = "；".join(
            str(annotation.get("note") or "").strip()
            for annotation in annotations
            if str(annotation.get("note") or "").strip()
        )
        return {
            "username": ",".join(str(annotation.get("username") or "") for annotation in annotations if annotation.get("username")),
            "mos": min(int(annotation.get("mos") or 0) for annotation in annotations),
            "has_defect": any(bool(annotation.get("has_defect")) for annotation in annotations),
            "primary_issue": primary_issue,
            "issues": issues,
            "other_issue": other_issue,
            "note": note,
            "updated_at": max(float(annotation.get("updated_at") or 0) for annotation in annotations),
            "annotator_count": len(annotations),
            "required_annotator_count": self._stage_target(task, stage),
        }

    def _upsert_screen_annotation(
        self,
        task: dict[str, Any],
        item_record: dict[str, Any],
        stage: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        target = self._stage_target(task, stage)
        annotation = self._screen_record(payload, allow_empty_issue=True)
        annotations = self._screen_annotations(item_record, stage)
        existing_index = next(
            (index for index, entry in enumerate(annotations) if entry.get("username") == annotation["username"]),
            None,
        )
        if existing_index is None:
            if len(annotations) >= target:
                stage_name = "粗筛" if stage == "rough" else "精筛"
                raise ValueError(f"该图片的{stage_name}标注人数已达到上限")
            annotations.append(annotation)
        else:
            annotations[existing_index] = annotation
        item_record[f"{stage}_annotations"] = annotations
        aggregate = self._aggregate_screen_annotations(task, stage, annotations)
        if aggregate is None:
            item_record.pop(stage, None)
        else:
            item_record[stage] = aggregate
        return deepcopy(annotation)

    def _item_import_keys(self, task: dict[str, Any], item: dict[str, Any]) -> set[tuple[str, str]]:
        return {
            (str(item.get("src_image") or ""), str(item.get("dst_image") or "")),
            (
                image_relative_path(task.get("root_dir"), item.get("src_image")),
                image_relative_path(task.get("root_dir"), item.get("dst_image")),
            ),
        }

    def _match_import_item(
        self,
        task: dict[str, Any],
        items: list[dict[str, Any]],
        row: dict[str, Any],
        path_index: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any] | None:
        if row.get("item_index") is not None:
            try:
                item_index = int(row.get("item_index"))
            except (TypeError, ValueError):
                return None
            return next((item for item in items if int(item.get("item_index", -1)) == item_index), None)
        if row.get("src_image") is None or row.get("dst_image") is None:
            return None
        raw_key = (str(row.get("src_image") or ""), str(row.get("dst_image") or ""))
        relative_key = (
            image_relative_path(task.get("root_dir"), row.get("src_image")),
            image_relative_path(task.get("root_dir"), row.get("dst_image")),
        )
        return path_index.get(raw_key) or path_index.get(relative_key)

    def _import_screen_annotations(
        self,
        task: dict[str, Any],
        item_record: dict[str, Any],
        row: dict[str, Any],
        stage: str,
    ) -> bool:
        raw_annotations = row.get(f"{stage}_annotations")
        if isinstance(raw_annotations, list):
            annotation_payloads = [entry for entry in raw_annotations if isinstance(entry, dict)]
        else:
            stage_payload = row.get(stage)
            annotation_payloads = [stage_payload] if isinstance(stage_payload, dict) else []
        if not annotation_payloads:
            return False
        annotations = [self._screen_record(payload, allow_empty_issue=True) for payload in annotation_payloads]
        item_record[f"{stage}_annotations"] = annotations
        aggregate = self._aggregate_screen_annotations(task, stage, annotations)
        if aggregate is None:
            item_record.pop(stage, None)
        else:
            item_record[stage] = aggregate
        return True

    def _import_label_record(self, item_record: dict[str, Any], row: dict[str, Any]) -> bool:
        label_payload = row.get("label") if isinstance(row.get("label"), dict) else {}
        labels = None
        username = normalized_import_username(label_payload, row.get("label_username") or row.get("username") or "imported")
        if isinstance(label_payload.get("labels"), dict):
            labels = label_payload.get("labels")
        elif isinstance(row.get("corrected_labels"), dict):
            labels = row.get("corrected_labels")
        if not isinstance(labels, dict):
            return False
        item_record["label"] = {
            "username": username or "imported",
            "labels": sanitize_labels(labels),
            "updated_at": float(label_payload.get("updated_at") or row.get("label_updated_at") or utc_now()),
        }
        return True

    def import_annotations_jsonl(self, task_id: str, jsonl_path: str | os.PathLike[str]) -> dict[str, Any]:
        task = self._require_task(task_id)
        rows = load_import_jsonl(jsonl_path)
        items = self._read_items(task)
        records = self._read_records(task)
        path_index: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            for key in self._item_import_keys(task, item):
                path_index[key] = item

        imported_count = 0
        updated_items = 0
        updated_records = 0
        skipped_count = 0
        unmatched_rows = []
        item_changed_indexes: set[int] = set()
        record_changed_indexes: set[int] = set()

        for line_number, row in rows:
            item = self._match_import_item(task, items, row, path_index)
            if item is None:
                unmatched_rows.append({"line": line_number, "reason": "未匹配到任务图片"})
                continue

            item_index = int(item["item_index"])
            item_changed = False
            record_changed = False
            imported_labels = row.get("labels") if isinstance(row.get("labels"), dict) else row.get("object_labels")
            if not isinstance(imported_labels, dict):
                imported_labels = row.get("original_labels")
            if isinstance(imported_labels, dict):
                cleaned_labels = sanitize_labels(imported_labels)
                if cleaned_labels:
                    item["labels"] = cleaned_labels
                    item_changed = True

            item_record = records.setdefault(str(item_index), {})
            if self._import_screen_annotations(task, item_record, row, "rough"):
                record_changed = True
            if self._import_screen_annotations(task, item_record, row, "fine"):
                record_changed = True
            if row.get("sampled") is not None:
                item_record["sampled"] = clean_bool(row.get("sampled"))
                if row.get("sample_bucket") is not None:
                    item_record["sample_bucket"] = str(row.get("sample_bucket") or "")
                record_changed = True
            elif row.get("sample_bucket") is not None:
                item_record["sample_bucket"] = str(row.get("sample_bucket") or "")
                record_changed = True
            if self._import_label_record(item_record, row):
                item_record["sampled"] = True
                record_changed = True

            if item_changed or record_changed:
                imported_count += 1
            else:
                skipped_count += 1
            if item_changed:
                item_changed_indexes.add(item_index)
            if record_changed:
                record_changed_indexes.add(item_index)

        updated_items = len(item_changed_indexes)
        updated_records = len(record_changed_indexes)
        if updated_items:
            write_json_file(self._items_path(task), items)
        if updated_records:
            self._write_records(task, records)
        return {
            "total_rows": len(rows),
            "imported_count": imported_count,
            "unmatched_count": len(unmatched_rows),
            "skipped_count": skipped_count,
            "updated_items": updated_items,
            "updated_records": updated_records,
            "unmatched_rows": unmatched_rows[:20],
            "summary": self.summary(task_id),
        }

    def _screening_rounds(self, items: list[dict[str, Any]], records: dict[str, Any], stage: str, target: int) -> list[dict[str, int]]:
        rounds = []
        total = len(items)
        for round_index in range(1, target + 1):
            completed = sum(
                1
                for item in items
                if self._annotation_count(records.get(str(item["item_index"]), {}), stage) >= round_index
            )
            rounds.append({"round": round_index, "completed": completed, "total": total})
        return rounds

    def _allocation_offset(self, username: str, item_count: int) -> int:
        if item_count <= 0:
            return 0
        return sum(ord(char) for char in username) % item_count

    def _sort_allocated_items(
        self,
        items: list[dict[str, Any]],
        records: dict[str, Any],
        stage: str,
        username: str,
    ) -> list[dict[str, Any]]:
        if not username or stage not in {"rough", "fine"}:
            return items
        offset = self._allocation_offset(username, len(items))
        return sorted(
            items,
            key=lambda item: (
                self._annotation_count(records.get(str(item["item_index"]), {}), stage),
                (int(item["item_index"]) - offset) % max(1, len(items)),
            ),
        )

    def _active_label_claim(
        self,
        record: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any] | None:
        claim = record.get("label_claim")
        if not isinstance(claim, dict):
            return None
        username = str(claim.get("username") or "").strip()
        if not username:
            return None
        try:
            claimed_at = float(claim.get("claimed_at") or 0)
        except (TypeError, ValueError):
            return None
        now = utc_now() if now is None else now
        if claimed_at <= 0 or now - claimed_at > LABEL_CLAIM_TTL_SECONDS:
            return None
        return claim

    def _clear_expired_label_claims(self, records: dict[str, Any], now: float | None = None) -> bool:
        now = utc_now() if now is None else now
        changed = False
        for record in records.values():
            if not isinstance(record, dict) or "label_claim" not in record:
                continue
            if self._active_label_claim(record, now) is None:
                record.pop("label_claim", None)
                changed = True
        return changed

    def _label_claimed_by_other(self, record: dict[str, Any], username: str, now: float | None = None) -> bool:
        claim = self._active_label_claim(record, now)
        return bool(claim and claim.get("username") != username)

    def _has_active_label_claim_for_user(
        self,
        record: dict[str, Any],
        username: str,
        now: float | None = None,
    ) -> bool:
        claim = self._active_label_claim(record, now)
        return bool(claim and claim.get("username") == username)

    def _read_item(self, task: dict[str, Any], item_index: int) -> dict[str, Any]:
        items = self._read_items(task)
        try:
            item = items[int(item_index)]
        except (IndexError, ValueError) as exc:
            raise KeyError("item not found") from exc
        if int(item.get("item_index")) != int(item_index):
            for candidate in items:
                if int(candidate.get("item_index", -1)) == int(item_index):
                    return candidate
            raise KeyError("item not found")
        return item

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        jsonl_path = Path(str(payload.get("jsonl_path") or "")).expanduser()
        root_dir = Path(str(payload.get("root_dir") or jsonl_path.parent)).expanduser()
        label_dir_value = str(payload.get("label_dir") or "").strip()
        label_dir = Path(label_dir_value).expanduser() if label_dir_value else None
        rows = load_jsonl(jsonl_path)

        items = []
        for item_index, row in enumerate(rows):
            labels = sanitize_labels(deepcopy(row.get("labels") if isinstance(row.get("labels"), dict) else {}))
            if label_dir is not None:
                merge_labels(labels, INPUT_GROUP_NAME, read_image_labels(root_dir, label_dir, str(row["src_image"])))
                merge_labels(labels, OUTPUT_GROUP_NAME, read_image_labels(root_dir, label_dir, str(row["dst_image"])))
            items.append(
                {
                    "item_index": item_index,
                    "src_image": str(row["src_image"]),
                    "dst_image": str(row["dst_image"]),
                    "labels": labels,
                }
            )

        rough_payload = payload.get("rough") if isinstance(payload.get("rough"), dict) else {}
        fine_payload = payload.get("fine") if isinstance(payload.get("fine"), dict) else {}
        issue_options = normalize_issue_options(
            rough_payload.get("issue_options", payload.get("issue_options", []))
        )
        primary_issue = str(rough_payload.get("primary_issue") or payload.get("primary_issue") or "").strip()
        if primary_issue and primary_issue not in issue_options:
            issue_options.insert(0, primary_issue)

        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "name": str(payload.get("name") or "").strip() or jsonl_path.stem,
            "root_dir": str(root_dir),
            "jsonl_path": str(jsonl_path),
            "label_dir": str(label_dir) if label_dir is not None else "",
            "generation_prompt_dir": clean_optional_path(payload.get("generation_prompt_dir")),
            "data_dir": str(self._task_data_dir(task_id)),
            "created_at": utc_now(),
            "item_count": len(items),
            "rough": {
                "min_mos": int(rough_payload.get("min_mos") or payload.get("rough_min_mos") or 4),
                "annotator_count": clean_positive_int(rough_payload.get("annotator_count"), 1),
                "require_no_defect": clean_bool(rough_payload.get("require_no_defect", True)),
                "primary_issue": primary_issue,
                "issue_options": issue_options,
            },
            "fine": {
                "min_mos": int(fine_payload.get("min_mos") or payload.get("fine_min_mos") or 4),
                "annotator_count": clean_positive_int(fine_payload.get("annotator_count"), 1),
                "enable_defect": clean_bool(fine_payload.get("enable_defect", False)),
            },
            "selected_label_paths": normalize_label_paths(payload.get("selected_label_paths")),
        }
        self._apply_generation_prompts(task, items)
        write_json_file(self._items_path(task), items)
        write_json_file(self._records_path(task), {})
        state = self._read_state()
        state.setdefault("tasks", []).append(task)
        self._write_state(state)
        return self._task_payload(task)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [self._task_payload(task) for task in self._read_state().get("tasks", [])]

    def warm_preview_cache(self, task_id: str, progress_callback=None) -> dict[str, Any]:
        task = deepcopy(self._require_task(task_id))
        items = self._read_items(task)
        image_jobs = [(item, "src", "src_image") for item in items] + [
            (item, "dst", "dst_image") for item in items
        ]
        total = len(image_jobs)
        cache_dir = self.preview_cache_dir(task_id)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_index = preview_cache_index(cache_dir)
        result = {
            "total": total,
            "processed_count": 0,
            "generated_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "failures": [],
            "unique_image_count": 0,
            "duplicate_ref_count": 0,
        }
        if total == 0:
            if progress_callback:
                progress_callback(100, "没有图片需要缓存")
            return result

        last_status_write = 0.0

        def update_progress(message: str, force_status_write: bool = False) -> None:
            nonlocal last_status_write
            if progress_callback:
                percent = round((result["processed_count"] / total) * 100) if total else 100
                progress_callback(percent, message)
            now = utc_now()
            if not force_status_write and result["processed_count"] < total and now - last_status_write < 1:
                return
            last_status_write = now
            write_json_file(
                preview_cache_status_path(cache_dir),
                {
                    "task_id": task_id,
                    "status": "running",
                    "updated_at": utc_now(),
                    "progress": round((result["processed_count"] / total) * 100) if total else 100,
                    "result": result,
                },
            )

        pending: dict[str, dict[str, Any]] = {}
        key_by_path: dict[str, str] = {}
        referenced_cache_keys: set[str] = set()
        for item, kind, field in image_jobs:
            item_index = item.get("item_index")
            raw_path = Path(str(item[field]))
            image_path = raw_path if raw_path.is_absolute() else Path(task["root_dir"]) / raw_path
            try:
                if not image_path.exists() or not image_path.is_file():
                    raise FileNotFoundError(str(image_path))
                resolved_key = str(image_path.resolve())
                cache_key = key_by_path.get(resolved_key)
                if cache_key is None:
                    cache_key = preview_cache_key(image_path)
                    key_by_path[resolved_key] = cache_key
                referenced_cache_keys.add(cache_key)
                if cache_key in cache_index:
                    result["skipped_count"] += 1
                    result["processed_count"] += 1
                    continue
                group = pending.setdefault(
                    cache_key,
                    {
                        "image_path": image_path,
                        "refs": [],
                    },
                )
                group["refs"].append({"item_index": item_index, "kind": kind})
            except Exception as exc:  # noqa: BLE001 - keep warming the rest of the task
                result["failed_count"] += 1
                result["processed_count"] += 1
                result["failures"].append(
                    {
                        "item_index": item_index,
                        "kind": kind,
                        "error": str(exc),
                    }
                )

        result["unique_image_count"] = len(referenced_cache_keys)
        result["duplicate_ref_count"] = total - len(referenced_cache_keys)
        if result["processed_count"]:
            update_progress(f"正在缓存图片 {result['processed_count']} / {total}", force_status_write=True)

        def warm_one(image_path: Path) -> str:
            preview_path, _ = resized_image_file(image_path, cache_dir)
            return "skipped" if preview_path.resolve() == image_path.resolve() else "generated"

        with ThreadPoolExecutor(max_workers=PREVIEW_CACHE_WORKERS) as executor:
            futures = {
                executor.submit(warm_one, group["image_path"]): group
                for group in pending.values()
            }
            for future in as_completed(futures):
                group = futures[future]
                refs = group["refs"]
                try:
                    status = future.result()
                    if status == "skipped":
                        result["skipped_count"] += len(refs)
                    else:
                        result["generated_count"] += len(refs)
                except Exception as exc:  # noqa: BLE001 - keep warming the rest of the task
                    result["failed_count"] += len(refs)
                    for ref in refs:
                        result["failures"].append(
                            {
                                "item_index": ref["item_index"],
                                "kind": ref["kind"],
                                "error": str(exc),
                            }
                        )
                finally:
                    result["processed_count"] += len(refs)
                    update_progress(f"正在缓存图片 {result['processed_count']} / {total}")

        if progress_callback:
            progress_callback(100, "图片缓存完成")
        write_json_file(
            preview_cache_status_path(cache_dir),
            {
                "task_id": task_id,
                "status": "completed",
                "updated_at": utc_now(),
                "progress": 100,
                "result": result,
            },
        )
        return result

    def delete_task(self, task_id: str) -> dict[str, Any]:
        state = self._read_state()
        tasks = state.get("tasks", [])
        task = self._find_task(state, task_id)
        if not task:
            raise KeyError("task not found")
        payload = self._task_payload(task)
        state["tasks"] = [entry for entry in tasks if entry.get("id") != task_id]
        self._write_state(state)
        return payload

    def update_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._read_state()
        task = self._find_task(state, task_id)
        if not task:
            raise KeyError("task not found")

        rough_payload = payload.get("rough") if isinstance(payload.get("rough"), dict) else {}
        if "issue_options" in rough_payload or "issue_options" in payload:
            task.setdefault("rough", {})["issue_options"] = normalize_issue_options(
                rough_payload.get("issue_options", payload.get("issue_options", []))
            )
        if "selected_label_paths" in payload:
            task["selected_label_paths"] = normalize_label_paths(payload.get("selected_label_paths"))
        if "generation_prompt_dir" in payload:
            task["generation_prompt_dir"] = clean_optional_path(payload.get("generation_prompt_dir"))
            self._refresh_generation_prompts(task)

        self._write_state(state)
        return self._task_payload(task)

    def _task_payload(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(task)
        payload["label_option_groups"] = deepcopy(LABEL_OPTION_GROUPS)
        payload["summary"] = self.summary(task["id"])
        return payload

    def summary(self, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        items = self._read_items(task)
        records = self._read_records(task)
        rough_target = self._stage_target(task, "rough")
        fine_target = self._stage_target(task, "fine")
        rough_completed = 0
        rough_passed = 0
        fine_completed = 0
        fine_passed = 0
        rough_annotation_completed = 0
        fine_annotation_completed = 0
        sampled = 0
        label_completed = 0
        for item in items:
            record = records.get(str(item["item_index"]), {})
            rough_count = min(self._annotation_count(record, "rough"), rough_target)
            fine_count = min(self._annotation_count(record, "fine"), fine_target)
            rough_annotation_completed += rough_count
            fine_annotation_completed += fine_count
            rough = record.get("rough")
            fine = record.get("fine")
            if rough_count >= rough_target:
                rough_completed += 1
            if rough_count >= rough_target and self._rough_passes(task, rough):
                rough_passed += 1
            if fine_count >= fine_target:
                fine_completed += 1
            if fine_count >= fine_target and self._fine_passes(task, fine):
                fine_passed += 1
            if record.get("sampled"):
                sampled += 1
                if record.get("label"):
                    label_completed += 1
        return {
            "total": len(items),
            "rough_annotator_count": rough_target,
            "fine_annotator_count": fine_target,
            "rough_completed": rough_completed,
            "rough_passed": rough_passed,
            "fine_candidates": rough_passed,
            "fine_completed": fine_completed,
            "fine_passed": fine_passed,
            "rough_annotation_completed": rough_annotation_completed,
            "rough_annotation_target": len(items) * rough_target,
            "fine_annotation_completed": fine_annotation_completed,
            "fine_annotation_target": rough_passed * fine_target,
            "rough_rounds": self._screening_rounds(items, records, "rough", rough_target),
            "fine_rounds": self._screening_rounds(
                [
                    item
                    for item in items
                    if self._stage_complete(task, records.get(str(item["item_index"]), {}), "rough")
                    and self._rough_passes(task, records.get(str(item["item_index"]), {}).get("rough"))
                ],
                records,
                "fine",
                fine_target,
            ),
            "sampled": sampled,
            "label_completed": label_completed,
        }

    def list_stage_items(
        self,
        task_id: str,
        stage: str,
        username: str = "",
        include_history: bool = False,
        reserve_open_label_item: bool = False,
    ) -> list[dict[str, Any]]:
        task = self._require_task(task_id)
        stage = str(stage or "rough")
        username = str(username or "").strip()
        include_history = bool(include_history and username)
        if stage == "label" and reserve_open_label_item and username:
            records_path = self._records_path(task)
            with json_write_lock(records_path):
                records = self._read_records(task)
                items = self._read_items(task)
                now = utc_now()
                changed = self._clear_expired_label_claims(records, now)
                if not any(
                    self._has_active_label_claim_for_user(record, username, now)
                    for record in records.values()
                    if isinstance(record, dict) and not record.get("label")
                ):
                    candidates = self._list_stage_items_from_loaded(
                        task,
                        items,
                        records,
                        stage,
                        username,
                        include_history,
                        now,
                    )
                    claim_candidate = next(
                        (
                            item
                            for item in candidates
                            if not (item.get("record") or {}).get("label")
                            and not self._label_claimed_by_other(item.get("record") or {}, username, now)
                        ),
                        None,
                    )
                    if claim_candidate:
                        item_record = records.setdefault(str(int(claim_candidate["item_index"])), {})
                        item_record["label_claim"] = {"username": username, "claimed_at": now}
                        changed = True
                if changed:
                    self._write_records(task, records)
                return self._list_stage_items_from_loaded(
                    task,
                    items,
                    records,
                    stage,
                    username,
                    include_history,
                    now,
                )

        records = self._read_records(task)
        items = self._read_items(task)
        now = utc_now()
        return self._list_stage_items_from_loaded(task, items, records, stage, username, include_history, now)

    def _list_stage_items_from_loaded(
        self,
        task: dict[str, Any],
        items: list[dict[str, Any]],
        records: dict[str, Any],
        stage: str,
        username: str,
        include_history: bool,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        result = []
        for item in items:
            record = records.get(str(item["item_index"]), {})
            user_screen_annotation = (
                self._annotation_for_user(record, stage, username) if username and stage in {"rough", "fine"} else None
            )
            label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
            has_user_label = bool(username and stage == "label" and label_record.get("username") == username)
            if stage == "label" and username and not has_user_label and self._label_claimed_by_other(record, username, now):
                continue
            if stage == "fine" and (
                not self._stage_complete(task, record, "rough") or not self._rough_passes(task, record.get("rough"))
            ):
                continue
            if stage == "label" and (
                not record.get("sampled") or (record.get("label") and not (include_history and has_user_label))
            ):
                continue
            if stage not in {"rough", "fine", "label"}:
                raise ValueError("未知阶段")
            if username and stage in {"rough", "fine"}:
                target = self._stage_target(task, stage)
                if user_screen_annotation is not None and not include_history:
                    continue
                if user_screen_annotation is None and self._annotation_count(record, stage) >= target:
                    continue
            result.append(self._item_payload(task, item, record, stage=stage, username=username))
        sorted_result = self._sort_allocated_items(result, records, stage, username)
        if include_history:
            if stage == "label":
                sorted_result = sorted(
                    sorted_result,
                    key=lambda item: (
                        0
                        if self._payload_has_user_label_claim(item, username, now)
                        else 1
                        if self._payload_has_user_annotation(item, stage, username)
                        else 2
                    ),
                )
            else:
                sorted_result = sorted(
                    sorted_result,
                    key=lambda item: 0 if self._payload_has_user_annotation(item, stage, username) else 1,
                )
        return sorted_result

    def _payload_has_user_label_claim(self, item: dict[str, Any], username: str, now: float | None = None) -> bool:
        if not username:
            return False
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        return self._has_active_label_claim_for_user(record, username, now)

    def _payload_has_user_annotation(self, item: dict[str, Any], stage: str, username: str) -> bool:
        if not username:
            return False
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        if stage in {"rough", "fine"}:
            annotation = record.get(stage) if isinstance(record.get(stage), dict) else {}
            return annotation.get("username") == username
        if stage == "label":
            label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
            return label_record.get("username") == username
        return False

    def _item_payload(
        self,
        task: dict[str, Any],
        item: dict[str, Any],
        record: dict[str, Any],
        stage: str = "",
        username: str = "",
    ) -> dict[str, Any]:
        payload = deepcopy(item)
        payload_record = deepcopy(record)
        if username and stage in {"rough", "fine"}:
            user_annotation = self._annotation_for_user(record, stage, username)
            if user_annotation is not None:
                payload_record[stage] = user_annotation
            elif f"{stage}_annotations" in payload_record:
                payload_record.pop(stage, None)
        if stage == "label":
            payload_record["label_draft"] = {"labels": self._label_draft_labels(task, item, payload_record)}
        payload["record"] = payload_record
        payload["image_urls"] = {
            "src": f"/api/tasks/{task['id']}/images/{item['item_index']}/src",
            "dst": f"/api/tasks/{task['id']}/images/{item['item_index']}/dst",
        }
        return payload

    def _label_draft_labels(
        self,
        task: dict[str, Any],
        item: dict[str, Any],
        record: dict[str, Any],
    ) -> dict[str, Any]:
        draft = {}
        paths = task.get("selected_label_paths") or flatten_label_paths(item.get("labels", {}))
        for path in paths:
            normalized_path = [str(part) for part in path]
            value = nested_get(item.get("labels", {}), normalized_path)
            if value not in (None, ""):
                nested_set(draft, normalized_path, deepcopy(value))
        label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
        saved_labels = label_record.get("labels") if isinstance(label_record.get("labels"), dict) else {}
        nested_overlay(draft, saved_labels)
        return draft

    def save_rough(self, task_id: str, item_index: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = self._require_task(task_id)
        self._read_item(task, int(item_index))
        records = self._read_records(task)
        item_record = records.setdefault(str(int(item_index)), {})
        record = self._upsert_screen_annotation(task, item_record, "rough", payload)
        self._write_records(task, records)
        return record

    def save_fine(self, task_id: str, item_index: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = self._require_task(task_id)
        self._read_item(task, int(item_index))
        records = self._read_records(task)
        item_record = records.setdefault(str(int(item_index)), {})
        if not self._stage_complete(task, item_record, "rough") or not self._rough_passes(task, item_record.get("rough")):
            raise ValueError("精筛前必须先通过粗筛")
        record = self._upsert_screen_annotation(task, item_record, "fine", payload)
        self._write_records(task, records)
        return record

    def save_label(self, task_id: str, item_index: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = self._require_task(task_id)
        self._read_item(task, int(item_index))
        records_path = self._records_path(task)
        with json_write_lock(records_path):
            records = self._read_records(task)
            item_record = records.setdefault(str(int(item_index)), {})
            if not item_record.get("sampled"):
                raise ValueError("标签纠错前必须先采样")
            username = str(payload.get("username") or "").strip()
            if not username:
                raise ValueError("username is required")
            label_claim = self._active_label_claim(item_record)
            if label_claim and label_claim.get("username") != username:
                raise ValueError("该图片已分配给其他用户进行标签纠错")
            labels = payload.get("labels")
            if not isinstance(labels, dict):
                raise ValueError("labels must be an object")
            item_record["label"] = {
                "username": username,
                "labels": deepcopy(labels),
                "updated_at": utc_now(),
            }
            item_record.pop("label_claim", None)
            self._write_records(task, records)
            return deepcopy(item_record["label"])

    def _screen_record(self, payload: dict[str, Any], allow_empty_issue: bool) -> dict[str, Any]:
        username = normalized_import_username(payload)
        if not username:
            raise ValueError("username is required")
        issues = normalize_issue_options(payload.get("issues", []))
        primary_issue = str(payload.get("primary_issue") or "").strip()
        if primary_issue and primary_issue not in issues:
            issues.insert(0, primary_issue)
        if primary_issue == "" and issues and not allow_empty_issue:
            primary_issue = issues[0]
        return {
            "username": username,
            "mos": clean_mos(payload.get("mos")),
            "has_defect": clean_bool(payload.get("has_defect", False)),
            "primary_issue": primary_issue,
            "issues": issues,
            "other_issue": str(payload.get("other_issue") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
            "updated_at": utc_now(),
        }

    def _rough_passes(self, task: dict[str, Any], record: dict[str, Any] | None) -> bool:
        if not isinstance(record, dict):
            return False
        rough = task.get("rough", {})
        if int(record.get("mos") or 0) < int(rough.get("min_mos") or 4):
            return False
        if rough.get("require_no_defect", True) and record.get("has_defect"):
            return False
        return True

    def _fine_passes(self, task: dict[str, Any], record: dict[str, Any] | None) -> bool:
        if not isinstance(record, dict):
            return False
        fine = task.get("fine", {})
        if int(record.get("mos") or 0) < int(fine.get("min_mos") or 4):
            return False
        if fine.get("enable_defect") and record.get("has_defect"):
            return False
        return True

    def _sample_candidates(
        self,
        task: dict[str, Any],
        items: list[dict[str, Any]],
        records: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in items
            if self._stage_complete(task, records.get(str(item["item_index"]), {}), "rough")
            and self._rough_passes(task, records.get(str(item["item_index"]), {}).get("rough"))
            and self._stage_complete(task, records.get(str(item["item_index"]), {}), "fine")
            and self._fine_passes(task, records.get(str(item["item_index"]), {}).get("fine"))
        ]

    def _sample_bucket_map(self, task: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in candidates:
            for bucket_name in self._sample_buckets_for_item(item):
                buckets[bucket_name].append(item)
        return dict(buckets)

    def sample_buckets(self, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        items = self._read_items(task)
        records = self._read_records(task)
        candidates = self._sample_candidates(task, items, records)
        buckets = self._sample_bucket_map(task, candidates)
        bucket_summary = []
        for bucket_name in sorted(buckets):
            sampled_count = sum(
                1
                for item in buckets[bucket_name]
                if records.get(str(item["item_index"]), {}).get("sampled")
            )
            bucket_summary.append(
                {
                    "bucket": bucket_name,
                    "candidate_count": len(buckets[bucket_name]),
                    "sampled_count": sampled_count,
                }
            )
        return {
            "candidate_count": len(candidates),
            "sampled_count": sum(1 for item in candidates if records.get(str(item["item_index"]), {}).get("sampled")),
            "buckets": bucket_summary,
        }

    def _selected_sample_items(
        self,
        payload: dict[str, Any],
        buckets: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        if clean_bool(payload.get("select_all", False)):
            selected = []
            selected_indexes = set()
            for bucket_name in sorted(buckets):
                for item in buckets[bucket_name]:
                    if item["item_index"] in selected_indexes:
                        continue
                    selected.append(item)
                    selected_indexes.add(item["item_index"])
            return selected

        selections = payload.get("selections")
        if isinstance(selections, list):
            selected: list[dict[str, Any]] = []
            selected_indexes: set[int] = set()
            for selection in selections:
                if not isinstance(selection, dict):
                    continue
                bucket_name = str(selection.get("bucket") or "")
                bucket_items = buckets.get(bucket_name, [])
                if not bucket_items:
                    continue
                count_value = selection.get("count")
                count = len(bucket_items) if str(count_value).strip().lower() == "all" else max(0, int(count_value or 0))
                for item in random.sample(bucket_items, min(count, len(bucket_items))):
                    if item["item_index"] in selected_indexes:
                        continue
                    selected.append(item)
                    selected_indexes.add(item["item_index"])
            return selected

        target_count = max(0, int(payload.get("target_count") or 0))
        min_per_bucket = max(1, int(payload.get("min_per_bucket") or 1))
        selected = []
        bucket_names = sorted(buckets)
        for bucket_name in bucket_names:
            if len(selected) >= target_count:
                break
            for item in buckets[bucket_name][:min_per_bucket]:
                if len(selected) >= target_count:
                    break
                selected.append(item)

        if len(selected) < target_count:
            selected_indexes = {item["item_index"] for item in selected}
            for bucket_name in bucket_names:
                remaining = [item for item in buckets[bucket_name] if item["item_index"] not in selected_indexes]
                random.shuffle(remaining)
                for item in remaining:
                    if len(selected) >= target_count:
                        break
                    selected.append(item)
                    selected_indexes.add(item["item_index"])
        return selected

    def sample(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = self._require_task(task_id)
        items = self._read_items(task)
        records = self._read_records(task)
        candidates = self._sample_candidates(task, items, records)
        buckets = self._sample_bucket_map(task, candidates)

        for record in records.values():
            if isinstance(record, dict):
                record.pop("sampled", None)
                record.pop("sample_bucket", None)

        selected = self._selected_sample_items(payload, buckets)
        for item in selected:
            key = str(item["item_index"])
            bucket_name = self._sample_bucket(task, item)
            records.setdefault(key, {})["sampled"] = True
            records[key]["sample_bucket"] = bucket_name

        self._write_records(task, records)
        selected_indexes = {item["item_index"] for item in selected}
        bucket_summary = [
            {
                "bucket": bucket_name,
                "candidate_count": len(buckets[bucket_name]),
                "sampled_count": sum(1 for item in buckets[bucket_name] if item["item_index"] in selected_indexes),
            }
            for bucket_name in sorted(buckets)
            if any(item["item_index"] in selected_indexes for item in buckets[bucket_name])
        ]
        return {
            "candidate_count": len(candidates),
            "sampled_count": len(selected),
            "buckets": bucket_summary,
            "summary": self.summary(task_id),
        }

    def _sample_bucket(self, task: dict[str, Any], item: dict[str, Any]) -> str:
        paths = task.get("selected_label_paths") or []
        if not paths:
            return "未分组"
        parts = []
        for path in paths:
            value = nested_get(item.get("labels", {}), [str(part) for part in path])
            label = "/".join(str(part) for part in path)
            if value in (None, ""):
                parts.append(f"{label}=未标注")
            else:
                parts.append(f"{label}={value}")
        return " | ".join(parts)

    def _sample_buckets_for_item(self, item: dict[str, Any]) -> list[str]:
        labels = item.get("labels", {})
        buckets = []
        for label_path in flatten_label_paths(labels):
            value = nested_get(labels, label_path)
            label = "/".join(str(part) for part in label_path)
            for item_value in stat_values(value):
                buckets.append(f"{label}={item_value}")
        return sorted(set(buckets)) or ["未分组"]

    def _visualization_candidates(
        self,
        task: dict[str, Any],
        items: list[dict[str, Any]],
        records: dict[str, Any],
        stage: str,
    ) -> list[dict[str, Any]]:
        if stage == "rough":
            return items
        if stage == "fine":
            return [
                item
                for item in items
                if self._stage_complete(task, records.get(str(item["item_index"]), {}), "rough")
                and self._rough_passes(task, records.get(str(item["item_index"]), {}).get("rough"))
            ]
        if stage == "sample":
            return [
                item
                for item in items
                if self._stage_complete(task, records.get(str(item["item_index"]), {}), "rough")
                and self._rough_passes(task, records.get(str(item["item_index"]), {}).get("rough"))
                and self._stage_complete(task, records.get(str(item["item_index"]), {}), "fine")
                and self._fine_passes(task, records.get(str(item["item_index"]), {}).get("fine"))
            ]
        return [
            item
            for item in items
            if records.get(str(item["item_index"]), {}).get("sampled")
        ]

    def _visualization_row(
        self,
        task: dict[str, Any],
        item: dict[str, Any],
        record: dict[str, Any],
        stage: str,
    ) -> dict[str, Any]:
        label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
        if stage in {"rough", "fine"}:
            stage_result = deepcopy(record.get(stage)) if isinstance(record.get(stage), dict) else None
            stage_annotations = self._screen_annotations(record, stage)
            if stage == "rough":
                stage_passed = self._stage_complete(task, record, "rough") and self._rough_passes(task, record.get("rough"))
            else:
                stage_passed = self._stage_complete(task, record, "fine") and self._fine_passes(task, record.get("fine"))
        elif stage == "sample":
            stage_result = {
                "sampled": bool(record.get("sampled")),
                "sample_bucket": record.get("sample_bucket"),
            }
            stage_annotations = []
            stage_passed = bool(record.get("sampled"))
        else:
            stage_result = deepcopy(label_record) if label_record else None
            stage_annotations = []
            stage_passed = bool(label_record)

        return {
            "item_index": item["item_index"],
            "src_image": item["src_image"],
            "dst_image": item["dst_image"],
            "src_relative_path": image_relative_path(task.get("root_dir"), item["src_image"]),
            "dst_relative_path": image_relative_path(task.get("root_dir"), item["dst_image"]),
            "generation_prompt": item.get("generation_prompt", ""),
            "generation_prompt_json_path": item.get("generation_prompt_json_path", ""),
            "image_urls": {
                "src": f"/api/tasks/{task['id']}/images/{item['item_index']}/src",
                "dst": f"/api/tasks/{task['id']}/images/{item['item_index']}/dst",
            },
            "original_labels": deepcopy(item.get("labels", {})),
            "record": deepcopy(record),
            "stage": stage,
            "stage_result": stage_result,
            "stage_annotations": stage_annotations,
            "stage_passed": stage_passed,
            "rough": deepcopy(record.get("rough")) if isinstance(record.get("rough"), dict) else None,
            "fine": deepcopy(record.get("fine")) if isinstance(record.get("fine"), dict) else None,
            "sampled": bool(record.get("sampled")),
            "sample_bucket": record.get("sample_bucket"),
            "corrected_labels": deepcopy(label_record.get("labels")) if isinstance(label_record.get("labels"), dict) else None,
            "label_username": label_record.get("username"),
            "label_updated_at": label_record.get("updated_at"),
        }

    def _filter_record_for_stage(
        self,
        item: dict[str, Any],
        record: dict[str, Any],
        stage: str,
    ) -> dict[str, Any]:
        if stage == "label":
            label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
            labels = label_record.get("labels") if isinstance(label_record.get("labels"), dict) else item.get("labels", {})
            return {
                "mos": None,
                "has_defect": None,
                "username": label_record.get("username"),
                "annotations": [label_record] if label_record else [],
                "labels": labels,
            }
        source_stage = "fine" if stage == "sample" else stage
        return {
            "mos": record.get(source_stage, {}).get("mos") if isinstance(record.get(source_stage), dict) else None,
            "has_defect": record.get(source_stage, {}).get("has_defect") if isinstance(record.get(source_stage), dict) else None,
            "username": record.get(source_stage, {}).get("username") if isinstance(record.get(source_stage), dict) else None,
            "annotations": self._screen_annotations(record, source_stage),
            "labels": item.get("labels", {}),
        }

    def _visualization_matches_filters(
        self,
        item: dict[str, Any],
        record: dict[str, Any],
        stage: str,
        filters: dict[str, Any] | None,
    ) -> bool:
        if not filters:
            return True
        filter_record = self._filter_record_for_stage(item, record, stage)

        mos_values = normalize_filter_values(filters.get("mos"))
        if mos_values and str(filter_record.get("mos")) not in mos_values:
            return False

        defect_values = normalize_filter_values(filters.get("has_defect"))
        if defect_values and str(bool(filter_record.get("has_defect"))) not in defect_values:
            return False

        annotators = normalize_filter_values(filters.get("annotators"))
        if annotators:
            stage_annotators = {
                str(annotation.get("username") or "")
                for annotation in filter_record.get("annotations", [])
                if isinstance(annotation, dict) and annotation.get("username")
            }
            username = str(filter_record.get("username") or "")
            if username:
                stage_annotators.add(username)
            if not stage_annotators.intersection(annotators):
                return False

        for label_filter in filters.get("labels") or []:
            if not isinstance(label_filter, dict):
                continue
            path = label_filter.get("path")
            if not isinstance(path, list) or not path:
                continue
            selected_values = normalize_filter_values(label_filter.get("values"))
            if not selected_values:
                continue
            current_value = nested_get(filter_record.get("labels", {}), [str(part) for part in path])
            current_values = normalize_filter_values(current_value if isinstance(current_value, list) else [current_value])
            if not current_values.intersection(selected_values):
                return False
        return True

    def _visualization_filter_options(
        self,
        items: list[dict[str, Any]],
        records: dict[str, Any],
        stage: str,
    ) -> dict[str, Any]:
        mos_values = set()
        defect_values = set()
        annotators = set()
        label_values: dict[str, dict[str, Any]] = {}

        for item in items:
            record = records.get(str(item["item_index"]), {})
            filter_record = self._filter_record_for_stage(item, record, stage)
            if filter_record.get("mos") is not None:
                mos_values.add(int(filter_record["mos"]))
            if filter_record.get("has_defect") is not None:
                defect_values.add(bool(filter_record["has_defect"]))
            for annotation in filter_record.get("annotations", []):
                if isinstance(annotation, dict) and annotation.get("username"):
                    annotators.add(str(annotation["username"]))
            if filter_record.get("username"):
                annotators.add(str(filter_record["username"]))
            for label_path in flatten_label_paths(filter_record.get("labels", {})):
                key = json.dumps(label_path, ensure_ascii=False)
                entry = label_values.setdefault(key, {"path": label_path, "values": set()})
                for value in stat_values(nested_get(filter_record.get("labels", {}), label_path)):
                    entry["values"].add(value)

        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in label_values.values():
            path = entry["path"]
            if len(path) < 2:
                continue
            group_name = str(path[0])
            dimension_name = "/".join(str(part) for part in path[1:])
            groups.setdefault(group_name, []).append(
                {"name": dimension_name, "options": sorted(entry["values"])}
            )
        return {
            "mos": sorted(mos_values),
            "has_defect": sorted(defect_values),
            "annotators": sorted(annotators),
            "label_options": [
                {"name": group_name, "dimensions": sorted(dimensions, key=lambda item: item["name"])}
                for group_name, dimensions in sorted(groups.items())
            ],
        }

    def get_visualization_results(
        self,
        task_id: str,
        stage: str,
        offset: int = 0,
        limit: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        stage = str(stage or "rough")
        if stage not in VALID_VISUALIZATION_STAGES:
            raise ValueError("未知可视化阶段")
        task = self._require_task(task_id)
        items = self._read_items(task)
        records = self._read_records(task)
        candidates = self._visualization_candidates(task, items, records, stage)
        candidates = [
            item
            for item in candidates
            if self._visualization_matches_filters(item, records.get(str(item["item_index"]), {}), stage, filters)
        ]
        total = len(candidates)
        start = max(0, int(offset or 0))
        stop = total if limit is None else min(total, start + max(0, int(limit)))
        rows = [
            self._visualization_row(task, item, records.get(str(item["item_index"]), {}), stage)
            for item in candidates[start:stop]
        ]
        return total, rows

    def get_visualization_filter_options(self, task_id: str, stage: str) -> dict[str, Any]:
        stage = str(stage or "rough")
        if stage not in VALID_VISUALIZATION_STAGES:
            raise ValueError("未知可视化阶段")
        task = self._require_task(task_id)
        items = self._read_items(task)
        records = self._read_records(task)
        candidates = self._visualization_candidates(task, items, records, stage)
        return self._visualization_filter_options(candidates, records, stage)

    def export_jsonl(self, task_id: str) -> str:
        task = self._require_task(task_id)
        records = self._read_records(task)
        lines = []
        for item in self._read_items(task):
            record = records.get(str(item["item_index"]), {})
            label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
            row = {
                "item_index": item["item_index"],
                "src_image": item["src_image"],
                "dst_image": item["dst_image"],
                "generation_prompt": item.get("generation_prompt", ""),
                "generation_prompt_json_path": item.get("generation_prompt_json_path", ""),
                "original_labels": item.get("labels", {}),
                "rough": record.get("rough"),
                "rough_annotations": record.get("rough_annotations", []),
                "fine": record.get("fine"),
                "fine_annotations": record.get("fine_annotations", []),
                "sampled": bool(record.get("sampled")),
                "sample_bucket": record.get("sample_bucket"),
                "corrected_labels": label_record.get("labels"),
                "label_username": label_record.get("username"),
                "label_updated_at": label_record.get("updated_at"),
            }
            lines.append(json.dumps(row, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

    def image_path(self, task_id: str, item_index: int, kind: str) -> Path:
        task = self._require_task(task_id)
        item = self._read_item(task, int(item_index))
        field = "src_image" if kind == "src" else "dst_image"
        raw_path = Path(str(item[field]))
        return raw_path if raw_path.is_absolute() else Path(task["root_dir"]) / raw_path


class PreviewCacheJobs:
    def __init__(self, store: AnnotationV2Store):
        self.store = store
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            running_job = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.get("task_id") == task_id and job.get("status") == "running"
                ),
                None,
            )
            if running_job:
                return deepcopy(running_job)

        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "task_id": task_id,
            "status": "running",
            "progress": 0,
            "message": "waiting to start",
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(target=self._run, args=(job_id, task_id), daemon=True)
        thread.start()
        return deepcopy(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def _update(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(updates)

    def _run(self, job_id: str, task_id: str) -> None:
        def progress(percent: int, message: str) -> None:
            self._update(job_id, progress=max(0, min(100, int(percent))), message=message)

        try:
            result = self.store.warm_preview_cache(task_id, progress_callback=progress)
            self._update(
                job_id,
                status="completed",
                progress=100,
                message="图片缓存完成",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced through job status for the UI
            self._update(job_id, status="failed", error=str(exc), message=str(exc))


store = AnnotationV2Store()
preview_cache_jobs = PreviewCacheJobs(store)
app = Flask(__name__, template_folder="templates", static_folder="static")
app.json.ensure_ascii = False


@app.errorhandler(ValueError)
def handle_value_error(exc: ValueError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(KeyError)
def handle_key_error(exc: KeyError):
    return jsonify({"error": str(exc).strip("'")}), 404


@app.errorhandler(PermissionError)
def handle_permission_error(exc: PermissionError):
    return jsonify({"error": str(exc)}), 403


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/dataset/rate/<task_id>")
def rate_page(task_id: str):
    return render_template("rate.html", task_id=task_id)


@app.get("/dataset/visualize/<task_id>")
def visualize_page(task_id: str):
    return render_template("visualize.html", task_id=task_id)


@app.get("/dataset/sample/<task_id>")
def sample_page(task_id: str):
    return render_template("sample.html", task_id=task_id)


@app.get("/api/tasks")
def api_tasks():
    return jsonify({"tasks": store.list_tasks()})


@app.post("/api/tasks")
def api_create_task():
    task = store.create_task(request.get_json(force=True) or {})
    return jsonify({"task": task}), 201


@app.patch("/api/tasks/<task_id>")
def api_update_task(task_id: str):
    return jsonify({"task": store.update_task(task_id, request.get_json(force=True) or {})})


@app.post("/api/tasks/<task_id>/preview-cache/jobs")
def api_start_preview_cache_job(task_id: str):
    store._require_task(task_id)
    preview_cache_jobs.store = store
    job = preview_cache_jobs.start(task_id)
    return jsonify({"job": job}), 202


@app.get("/api/tasks/<task_id>/preview-cache/jobs/<job_id>")
def api_get_preview_cache_job(task_id: str, job_id: str):
    job = preview_cache_jobs.get(job_id)
    if job is None or job.get("task_id") != task_id:
        return jsonify({"error": "preview cache job not found"}), 404
    return jsonify({"job": job})


@app.delete("/api/tasks/<task_id>")
def api_delete_task(task_id: str):
    payload = request.get_json(silent=True) or {}
    require_task_delete_admin(payload)
    return jsonify({"task": store.delete_task(task_id)})


@app.get("/api/tasks/<task_id>/summary")
def api_summary(task_id: str):
    return jsonify({"summary": store.summary(task_id)})


@app.get("/api/tasks/<task_id>/items")
def api_stage_items(task_id: str):
    return jsonify(
        {
            "items": store.list_stage_items(
                task_id,
                request.args.get("stage", "rough"),
                username=request.args.get("username", ""),
                include_history=clean_bool(request.args.get("include_history", False)),
                reserve_open_label_item=True,
            )
        }
    )


@app.get("/api/tasks/<task_id>/visualization-results")
def api_visualization_results(task_id: str):
    page = max(0, int(request.args.get("page", 0) or 0))
    limit = max(1, int(request.args.get("limit", 1) or 1))
    stage = request.args.get("stage", "rough")
    filters = json.loads(request.args.get("filters", "{}") or "{}")
    total, results = store.get_visualization_results(task_id, stage, offset=page * limit, limit=limit, filters=filters)
    return jsonify(
        {
            "stage": stage,
            "results": results,
            "total": total,
            "page": page,
            "limit": limit,
            "filter_options": store.get_visualization_filter_options(task_id, stage),
        }
    )


@app.post("/api/tasks/<task_id>/items/<int:item_index>/rough")
def api_save_rough(task_id: str, item_index: int):
    return jsonify({"record": store.save_rough(task_id, item_index, request.get_json(force=True) or {})})


@app.post("/api/tasks/<task_id>/items/<int:item_index>/fine")
def api_save_fine(task_id: str, item_index: int):
    return jsonify({"record": store.save_fine(task_id, item_index, request.get_json(force=True) or {})})


@app.post("/api/tasks/<task_id>/items/<int:item_index>/label")
def api_save_label(task_id: str, item_index: int):
    return jsonify({"record": store.save_label(task_id, item_index, request.get_json(force=True) or {})})


@app.post("/api/tasks/<task_id>/sample")
def api_sample(task_id: str):
    return jsonify({"result": store.sample(task_id, request.get_json(force=True) or {})})


@app.get("/api/tasks/<task_id>/sample-buckets")
def api_sample_buckets(task_id: str):
    return jsonify({"result": store.sample_buckets(task_id)})


@app.post("/api/tasks/<task_id>/import")
def api_import_annotations(task_id: str):
    payload = request.get_json(force=True) or {}
    jsonl_path = str(payload.get("jsonl_path") or "").strip()
    if not jsonl_path:
        raise ValueError("jsonl_path is required")
    return jsonify({"result": store.import_annotations_jsonl(task_id, jsonl_path)})


@app.get("/api/tasks/<task_id>/download")
def api_download(task_id: str):
    task = store._require_task(task_id)
    content = store.export_jsonl(task_id)
    output = BytesIO(content.encode("utf-8"))
    output.seek(0)
    return send_file(
        output,
        mimetype="application/x-ndjson; charset=utf-8",
        as_attachment=True,
        download_name=safe_download_name(task["name"], "annotations_v2.jsonl"),
    )


@app.get("/api/tasks/<task_id>/images/<int:item_index>/<kind>")
def api_image(task_id: str, item_index: int, kind: str):
    if kind not in {"src", "dst"}:
        abort(404)
    image_path = store.image_path(task_id, item_index, kind)
    if not image_path.exists() or not image_path.is_file():
        abort(404)
    mimetype = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    if truthy(request.args.get("original")):
        return send_file(image_path.resolve(), mimetype=mimetype)
    preview_path, preview_mimetype = resized_image_file(image_path, store.preview_cache_dir(task_id))
    return send_file(preview_path, mimetype=preview_mimetype, conditional=True)


if __name__ == "__main__":
    app.run(
        host=default_server_host(),
        port=int(os.environ.get("ANNOTATIONS_V2_PORT", "5065")),
        debug=False,
        threaded=True,
    )
