from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import random
import threading
import time
import uuid
from collections import Counter
from io import BytesIO
from copy import deepcopy
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_file
from openpyxl import Workbook
from PIL import Image

from web.annotations.label_options import LABEL_OPTION_GROUPS


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = APP_DIR / "data" / "state.json"
PREVIEW_CACHE_DIR_ENV = "ANNOTATIONS_PREVIEW_CACHE_DIR"
IMAGE_PREVIEW_MAX_EDGE = 1024


def utc_now() -> float:
    return time.time()


def load_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"绗?{line_number} 琛屼笉鏄悎娉?JSON: {exc}") from exc
            if "src_image" not in row or "dst_image" not in row:
                raise ValueError(f"绗?{line_number} 琛岀己灏?src_image 鎴?dst_image")
            row["labels"] = {}
            row.pop("tags", None)
            rows.append(row)
    if not rows:
        raise ValueError("jsonl 鏂囦欢涓虹┖")
    return rows


def nested_has_value(value: Any, parts: list[str]) -> bool:
    cursor = value
    for part in parts:
        if not isinstance(cursor, dict) or part not in cursor:
            return False
        cursor = cursor[part]
    return cursor not in (None, "")


def nested_get(value: Any, parts: list[str]) -> Any:
    cursor = value
    for part in parts:
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def normalize_annotation_tags(tags: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(tags or {})
    for group in LABEL_OPTION_GROUPS:
        group_name = str(group["name"])
        group_tags = normalized.get(group_name)
        if not isinstance(group_tags, dict):
            continue
        for dimension in group.get("dimensions", []):
            dimension_name = str(dimension["name"])
            value = group_tags.get(dimension_name)
            if isinstance(value, list):
                selected = next((item for item in value if item not in (None, "")), None)
                if selected is None:
                    group_tags.pop(dimension_name, None)
                else:
                    group_tags[dimension_name] = selected
    return normalized


def active_qc_history(annotation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        record
        for record in annotation.get("qc_history", [])
        if isinstance(record, dict) and not record.get("undone_at")
    ]


def qc_base_result(annotation: dict[str, Any]) -> dict[str, Any]:
    history = annotation.get("qc_history", [])
    if history and isinstance(history[0], dict) and isinstance(history[0].get("before"), dict):
        before = history[0]["before"]
        return {"mos": before.get("mos"), "tags": deepcopy(before.get("tags", {}))}
    return {"mos": annotation.get("mos"), "tags": deepcopy(annotation.get("tags", {}))}


def qc_reviewers(annotation: dict[str, Any]) -> list[str]:
    annotator = str(annotation.get("username") or "")
    reviewers = []
    seen = set()
    for record in active_qc_history(annotation):
        username = str(record.get("username") or "").strip()
        if not username or username == annotator or username in seen:
            continue
        seen.add(username)
        reviewers.append(username)
    return reviewers


def annotation_with_qc_summary(annotation: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(annotation)
    result["qc_reviewers"] = qc_reviewers(result)
    return result


def normalize_filter_values(values: Any) -> set[str]:
    if values is None:
        return set()
    if not isinstance(values, list):
        values = [values]
    return {str(value) for value in values if value not in (None, "")}


def normalize_filter_ranges(ranges: Any) -> list[tuple[float | None, float | None]]:
    if not isinstance(ranges, list):
        return []
    normalized = []
    for value_range in ranges:
        if not isinstance(value_range, dict):
            continue
        min_value = coerce_float(value_range.get("min"))
        max_value = coerce_float(value_range.get("max"))
        if min_value is None and max_value is None:
            continue
        normalized.append((min_value, max_value))
    return normalized


def coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def values_match_ranges(values: list[Any], ranges: list[tuple[float | None, float | None]]) -> bool:
    if not ranges:
        return True
    for value in values:
        numeric_value = coerce_float(value)
        if numeric_value is None:
            continue
        for min_value, max_value in ranges:
            if min_value is not None and numeric_value < min_value:
                continue
            if max_value is not None and numeric_value > max_value:
                continue
            return True
    return False


def result_matches_filters(annotation: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True

    mos_values = normalize_filter_values(filters.get("mos"))
    if mos_values and str(annotation.get("mos")) not in mos_values:
        return False

    annotators = normalize_filter_values(filters.get("annotators"))
    if annotators and str(annotation.get("username") or "") not in annotators:
        return False

    for label_filter in filters.get("labels") or []:
        if not isinstance(label_filter, dict):
            continue
        path = label_filter.get("path")
        if not isinstance(path, list) or not path:
            continue
        selected_values = normalize_filter_values(label_filter.get("values"))
        selected_ranges = normalize_filter_ranges(label_filter.get("ranges"))
        if not selected_values and not selected_ranges:
            continue
        current_value = nested_get(annotation.get("tags", {}), [str(part) for part in path])
        raw_values = current_value if isinstance(current_value, list) else [current_value]
        current_values = normalize_filter_values(raw_values)
        if selected_values and not current_values.intersection(selected_values):
            return False
        if selected_ranges and not values_match_ranges(raw_values, selected_ranges):
            return False
    return True


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


def stringify_stat_value(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def stat_values(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    result = []
    for raw_value in raw_values:
        label = stringify_stat_value(raw_value)
        if label is not None:
            result.append(label)
    return result


def stat_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"label": label, "count": count}
        for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def label_dimension_title(path: list[str]) -> str:
    return " / ".join(str(part) for part in path)


def dimension_key(dimension: dict[str, Any]) -> str | None:
    kind = dimension.get("type")
    if kind in {"mos", "annotator"}:
        return str(kind)
    if kind == "label" and isinstance(dimension.get("path"), list):
        return json.dumps([str(part) for part in dimension["path"]], ensure_ascii=False)
    return None


def combo_dimension_value(annotation: dict[str, Any], dimension: dict[str, Any]) -> str | None:
    kind = dimension.get("type")
    if kind == "mos":
        value = stringify_stat_value(annotation.get("mos"))
        return f"MOS {value}" if value is not None else None
    if kind == "annotator":
        value = stringify_stat_value(annotation.get("username"))
        return f"鏍囨敞鑰?{value}" if value is not None else None
    if kind == "label":
        path = dimension.get("path")
        if not isinstance(path, list) or not path:
            return None
        values = stat_values(nested_get(annotation.get("tags", {}), [str(part) for part in path]))
        return values[0] if values else None
    return None


def visible_label_options(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible_groups: list[dict[str, Any]] = []
    for group in LABEL_OPTION_GROUPS:
        visible_dimensions = []
        for dimension in group["dimensions"]:
            path = [group["name"], dimension["name"]]
            if any(nested_has_value(item.get("labels", {}), path) for item in items):
                visible_dimensions.append(deepcopy(dimension))
        if visible_dimensions:
            visible_groups.append({"name": group["name"], "dimensions": visible_dimensions})
    return visible_groups


def label_json_path(root_dir: Path, annotation_dir: Path, image_path: str) -> Path:
    raw_path = Path(str(image_path))
    image_full_path = raw_path if raw_path.is_absolute() else root_dir / raw_path
    try:
        relative_path = image_full_path.relative_to(root_dir)
    except ValueError:
        relative_path = Path(raw_path.name)
    return annotation_dir / relative_path.with_suffix(".json")


def read_image_labels(root_dir: Path, annotation_dir: Path, image_path: str) -> dict[str, Any]:
    path = label_json_path(root_dir, annotation_dir, image_path)
    if not path.exists() or not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"鏍囩鏂囦欢涓嶆槸鍚堟硶 JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        return {}
    labels = data.get("labels")
    return labels if isinstance(labels, dict) else data


def merge_labels(target: dict[str, Any], default_group: str, labels: dict[str, Any]) -> None:
    if not labels:
        return
    known_groups = {group["name"] for group in LABEL_OPTION_GROUPS}
    if any(group in labels for group in known_groups):
        for group, group_labels in labels.items():
            if isinstance(group_labels, dict):
                target.setdefault(group, {}).update(group_labels)
        return
    target.setdefault(default_group, {}).update(labels)


def load_item_labels(
    items: list[dict[str, Any]],
    root_dir: str,
    annotation_dir: str,
    progress_callback: Any | None = None,
) -> None:
    if not annotation_dir:
        return
    root_path = Path(root_dir)
    annotation_path = Path(annotation_dir)
    total = max(1, len(items))
    for index, item in enumerate(items, start=1):
        labels: dict[str, Any] = {}
        merge_labels(labels, str(LABEL_OPTION_GROUPS[0]["name"]), read_image_labels(root_path, annotation_path, item["src_image"]))
        merge_labels(labels, str(LABEL_OPTION_GROUPS[1]["name"]), read_image_labels(root_path, annotation_path, item["dst_image"]))
        item["labels"] = labels
        if progress_callback and (index == total or index % 50 == 0):
            progress_callback(20 + int(index / total * 60), f"姝ｅ湪鍔犺浇鏍囩 {index}/{total}")


def build_subtasks(total_items: int, chunk_size: int) -> list[dict[str, Any]]:
    if chunk_size <= 0:
        raise ValueError("瀛愪换鍔℃暟閲忓ぇ灏忓繀椤诲ぇ浜?0")
    subtasks = []
    for start in range(0, total_items, chunk_size):
        indexes = list(range(start, min(start + chunk_size, total_items)))
        subtasks.append(
            {
                "id": str(uuid.uuid4()),
                "index": len(subtasks) + 1,
                "item_indexes": indexes,
                "assigned_to": None,
                "assigned_at": None,
                "completed_at": None,
                "completed_count": 0,
            }
        )
    return subtasks


def safe_download_name(name: str, suffix: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in name.strip())
    return f"{safe or 'annotations'}_{suffix}"


def resized_image_bytes(path: Path, max_edge: int = IMAGE_PREVIEW_MAX_EDGE) -> tuple[BytesIO, str]:
    with Image.open(path) as image:
        image.load()
        if max(image.size) <= max_edge:
            output = BytesIO(path.read_bytes())
            output.seek(0)
            mimetype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            return output, mimetype

        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        output = BytesIO()
        image_format = (image.format or path.suffix.lstrip(".") or "JPEG").upper()
        if image_format == "JPG":
            image_format = "JPEG"
        if image_format == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(output, format=image_format, quality=88, optimize=True)
        output.seek(0)
        mimetype = Image.MIME.get(image_format, mimetypes.guess_type(str(path))[0] or "image/jpeg")
        return output, mimetype


def preview_cache_key(path: Path, max_edge: int = IMAGE_PREVIEW_MAX_EDGE) -> str:
    resolved = path.resolve()
    stat = resolved.stat()
    raw_key = f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}|{max_edge}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def resized_image_file(path: Path, cache_dir: Path, max_edge: int = IMAGE_PREVIEW_MAX_EDGE) -> tuple[Path, str]:
    mimetype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    cache_key = preview_cache_key(path, max_edge)
    if cache_dir.exists():
        cached_path = next(cache_dir.glob(f"{cache_key}.*"), None)
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
            return cache_path, Image.MIME.get(image_format, mimetype)

        tmp_path = cache_path.with_name(f"{cache_path.name}.{threading.get_ident()}.tmp")
        image.save(tmp_path, format=image_format, quality=88, optimize=True)
        os.replace(tmp_path, cache_path)
        return cache_path.resolve(), Image.MIME.get(image_format, mimetype)


def export_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_index": result["item_index"],
        "src_image": result["src_image"],
        "dst_image": result["dst_image"],
        "original_tags": result.get("original_tags", {}),
        "tags": result.get("tags", {}),
        "mos": result.get("mos"),
        "username": result.get("username"),
        "updated_at": result.get("updated_at"),
    }


def json_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


class AnnotationStore:
    def __init__(
        self,
        state_path: str | os.PathLike[str] = DEFAULT_STATE_PATH,
        preview_cache_dir: str | os.PathLike[str] | None = None,
    ):
        self.state_path = Path(state_path)
        configured_cache_dir = preview_cache_dir or os.environ.get(PREVIEW_CACHE_DIR_ENV)
        self.preview_cache_root = Path(configured_cache_dir).expanduser() if configured_cache_dir else None
        self._lock = threading.RLock()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write_state({"tasks": []})
        else:
            self._migrate_inline_tasks()
            self._migrate_task_files()

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"tasks": []}
        with self.state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.state_path)

    def _migrate_inline_tasks(self) -> None:
        with self._lock:
            state = self._read_state()
            changed = False
            for task in state.get("tasks", []):
                if "items" not in task and "annotations" not in task:
                    continue
                items = task.pop("items", [])
                annotations = task.pop("annotations", {})
                task.setdefault("item_count", len(items))
                task.setdefault("annotation_count", len(annotations))
                task.setdefault("data_dir", str(self._task_data_dir(task["id"])))
                task.setdefault("label_options", visible_label_options(items))
                self._write_item_chunks(task, items)
                self._write_annotation_files(task, annotations)
                task["items_storage"] = "chunks"
                task["annotations_storage"] = "items"
                changed = True
            if changed:
                self._write_state(state)

    def _migrate_task_files(self) -> None:
        with self._lock:
            state = self._read_state()
            changed = False
            for task in state.get("tasks", []):
                task.setdefault("data_dir", str(self._task_data_dir(task["id"])))
                if task.get("items_storage") != "chunks" and self._items_path(task).exists():
                    items = self._read_json_file(self._items_path(task), [])
                    self._write_item_chunks(task, items)
                    task["items_storage"] = "chunks"
                    task.setdefault("item_count", len(items))
                    changed = True
                if task.get("annotations_storage") != "items" and self._annotations_path(task).exists():
                    annotations = self._read_json_file(self._annotations_path(task), {})
                    self._write_annotation_files(task, annotations)
                    task["annotations_storage"] = "items"
                    task["annotation_count"] = len(annotations)
                    changed = True
            if changed:
                self._write_state(state)

    def _task_data_dir(self, task_id: str) -> Path:
        return self.state_path.parent / "tasks" / task_id

    def preview_cache_dir(self, task_id: str) -> Path:
        if self.preview_cache_root:
            return self.preview_cache_root / task_id
        return self._task_data_dir(task_id) / "preview_cache"

    def _read_json_file(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return deepcopy(default)
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json_file(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def _items_path(self, task: dict[str, Any]) -> Path:
        return Path(task["data_dir"]) / "items.json"

    def _item_chunks_dir(self, task: dict[str, Any]) -> Path:
        return Path(task["data_dir"]) / "items"

    def _item_chunk_path(self, task: dict[str, Any], subtask: dict[str, Any]) -> Path:
        return self._item_chunks_dir(task) / f"{subtask['id']}.json"

    def _annotations_path(self, task: dict[str, Any]) -> Path:
        return Path(task["data_dir"]) / "annotations.json"

    def _annotation_items_dir(self, task: dict[str, Any]) -> Path:
        return Path(task["data_dir"]) / "annotations"

    def _annotation_item_path(self, task: dict[str, Any], item_index: int) -> Path:
        return self._annotation_items_dir(task) / f"{int(item_index)}.json"

    def _write_item_chunks(self, task: dict[str, Any], items: list[dict[str, Any]]) -> None:
        for subtask in task.get("subtasks", []):
            chunk = [items[index] for index in subtask["item_indexes"]]
            self._write_json_file(self._item_chunk_path(task, subtask), chunk)

    def _write_annotation_files(self, task: dict[str, Any], annotations: dict[str, Any]) -> None:
        for key, annotation in annotations.items():
            self._write_json_file(self._annotation_item_path(task, int(key)), annotation)

    def _subtask_for_item_index(self, task: dict[str, Any], item_index: int) -> dict[str, Any] | None:
        item_index = int(item_index)
        for subtask in task.get("subtasks", []):
            indexes = subtask.get("item_indexes", [])
            if indexes and indexes[0] <= item_index <= indexes[-1] and item_index in indexes:
                return subtask
        return None

    def _read_subtask_items(self, task: dict[str, Any], subtask: dict[str, Any]) -> list[dict[str, Any]]:
        chunk_path = self._item_chunk_path(task, subtask)
        if chunk_path.exists():
            return self._read_json_file(chunk_path, [])
        all_items = self._read_items(task)
        return [all_items[index] for index in subtask["item_indexes"]]

    def _read_item(self, task: dict[str, Any], item_index: int) -> dict[str, Any]:
        subtask = self._subtask_for_item_index(task, int(item_index))
        if subtask:
            items = self._read_subtask_items(task, subtask)
            try:
                offset = subtask["item_indexes"].index(int(item_index))
            except ValueError as exc:
                raise IndexError(item_index) from exc
            return items[offset]
        return self._read_items(task)[int(item_index)]

    def _read_items(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        if "items" in task:
            return task["items"]
        chunks_dir = self._item_chunks_dir(task)
        if chunks_dir.exists():
            items: list[dict[str, Any]] = []
            for subtask in task.get("subtasks", []):
                items.extend(self._read_subtask_items(task, subtask))
            return items
        return self._read_json_file(self._items_path(task), [])

    def _read_annotations(self, task: dict[str, Any]) -> dict[str, Any]:
        annotations = deepcopy(task.get("annotations", {}))
        if task.get("annotations_storage") != "items" and self._annotations_path(task).exists():
            annotations.update(self._read_json_file(self._annotations_path(task), {}))
        annotations_dir = self._annotation_items_dir(task)
        if annotations_dir.exists():
            for path in annotations_dir.glob("*.json"):
                annotations[path.stem] = self._read_json_file(path, {})
        return annotations

    def _read_annotation(self, task: dict[str, Any], item_index: int) -> dict[str, Any] | None:
        path = self._annotation_item_path(task, int(item_index))
        if path.exists():
            return self._read_json_file(path, {})
        if "annotations" in task:
            return task["annotations"].get(str(item_index))
        if task.get("annotations_storage") != "items" and self._annotations_path(task).exists():
            return self._read_json_file(self._annotations_path(task), {}).get(str(item_index))
        return None

    def _write_annotations(self, task: dict[str, Any], annotations: dict[str, Any]) -> None:
        if "annotations" in task:
            task["annotations"] = annotations
        self._write_annotation_files(task, annotations)

    def _hydrate_task(self, task: dict[str, Any]) -> dict[str, Any]:
        hydrated = deepcopy(task)
        hydrated["items"] = self._read_items(task)
        hydrated["annotations"] = self._read_annotations(task)
        return hydrated

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            return [self._task_summary(task) for task in state["tasks"]]

    def create_task(
        self,
        name: str,
        root_dir: str,
        jsonl_path: str,
        chunk_size: int = 100,
        annotation_dir: str = "",
        progress_callback: Any | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip() or Path(jsonl_path).stem
        root_dir = str(Path(root_dir).expanduser())
        jsonl_path = str(Path(jsonl_path).expanduser())
        annotation_dir = str(Path(annotation_dir).expanduser()) if annotation_dir else ""
        if progress_callback:
            progress_callback(5, "姝ｅ湪璇诲彇 jsonl")
        items = load_jsonl(jsonl_path)
        if progress_callback:
            progress_callback(15, f"loaded {len(items)} rows")
        load_item_labels(items, root_dir, annotation_dir, progress_callback=progress_callback)
        if progress_callback:
            progress_callback(82, "building subtasks")
        subtasks = build_subtasks(len(items), int(chunk_size or 100))
        task_id = str(uuid.uuid4())
        data_dir = self._task_data_dir(task_id)
        label_options = visible_label_options(items)
        task = {
            "id": task_id,
            "name": name,
            "root_dir": root_dir,
            "jsonl_path": jsonl_path,
            "annotation_dir": annotation_dir,
            "chunk_size": int(chunk_size or 100),
            "created_at": utc_now(),
            "item_count": len(items),
            "annotation_count": 0,
            "data_dir": str(data_dir),
            "label_options": label_options,
            "subtasks": subtasks,
        }
        with self._lock:
            if progress_callback:
                progress_callback(90, "姝ｅ湪鍐欏叆浠诲姟鏁版嵁")
            task["items_storage"] = "chunks"
            task["annotations_storage"] = "items"
            self._write_item_chunks(task, items)
            state = self._read_state()
            state["tasks"].append(task)
            self._write_state(state)
        if progress_callback:
            progress_callback(100, "浠诲姟鍒涘缓瀹屾垚")
        return self._hydrate_task(task)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._find_task(self._read_state(), task_id)
            return self._hydrate_task(task) if task else None

    def refresh_task_labels(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            task = self._require_task(state, task_id)
            annotation_dir = task.get("annotation_dir", "")
            if not annotation_dir:
                raise ValueError("annotation_dir is required")

            root_dir = task["root_dir"]
            total_count = 0
            updated_count = 0
            labeled_count = 0
            cleared_count = 0
            all_items: list[dict[str, Any]] = []

            for subtask in task.get("subtasks", []):
                items = self._read_subtask_items(task, subtask)
                for item in items:
                    total_count += 1
                    previous_labels = deepcopy(item.get("labels", {}))
                    load_item_labels([item], root_dir, annotation_dir)
                    current_labels = item.get("labels", {})
                    if current_labels:
                        labeled_count += 1
                    if previous_labels and not current_labels:
                        cleared_count += 1
                    if previous_labels != current_labels:
                        updated_count += 1
                all_items.extend(items)
                self._write_json_file(self._item_chunk_path(task, subtask), items)

            task["label_options"] = visible_label_options(all_items)
            task["labels_refreshed_at"] = utc_now()
            task["items_storage"] = "chunks"
            self._write_state(state)
            return {
                "task": self._task_summary(task),
                "item_count": total_count,
                "updated_count": updated_count,
                "labeled_count": labeled_count,
                "cleared_count": cleared_count,
            }

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            state = self._read_state()
            before_count = len(state["tasks"])
            state["tasks"] = [task for task in state["tasks"] if task["id"] != task_id]
            deleted = len(state["tasks"]) != before_count
            if deleted:
                self._write_state(state)
            return deleted

    def assign_subtask(self, task_id: str, username: str) -> dict[str, Any] | None:
        username = (username or "").strip()
        if not username:
            raise ValueError("username is required")
        with self._lock:
            state = self._read_state()
            task = self._require_task(state, task_id)
            active = self._active_subtask_for_user(task, username)
            if active:
                self._write_state(state)
                return self._subtask_payload(task, active)

            available = [
                subtask
                for subtask in task["subtasks"]
                if subtask["assigned_to"] is None and subtask["completed_at"] is None
            ]
            if not available:
                return None
            subtask = random.choice(available)
            subtask["assigned_to"] = username
            subtask["assigned_at"] = utc_now()
            self._write_state(state)
            return self._subtask_payload(task, subtask)

    def get_subtask(self, task_id: str, subtask_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._find_task(self._read_state(), task_id)
            if not task:
                return None
            subtask = self._find_subtask(task, subtask_id)
            return self._subtask_payload(task, subtask) if subtask else None

    def save_annotation(
        self,
        task_id: str,
        subtask_id: str,
        item_index: int,
        username: str,
        mos: int,
        tags: dict[str, Any],
    ) -> dict[str, Any]:
        username = (username or "").strip()
        if not username:
            raise ValueError("username is required")
        if int(mos) < 1 or int(mos) > 5:
            raise ValueError("MOS 鍒嗗繀椤诲湪 1-5 涔嬮棿")
        with self._lock:
            state = self._read_state()
            task = self._require_task(state, task_id)
            subtask = self._require_subtask(task, subtask_id)
            if subtask["assigned_to"] != username:
                raise PermissionError("璇ュ瓙浠诲姟宸插垎閰嶇粰鍏朵粬鐢ㄦ埛")
            if int(item_index) not in subtask["item_indexes"]:
                raise ValueError("鏁版嵁涓嶅睘浜庡綋鍓嶅瓙浠诲姟")

            existing_annotation = self._read_annotation(task, int(item_index))
            annotation = {
                "item_index": int(item_index),
                "subtask_id": subtask_id,
                "username": username,
                "mos": int(mos),
                "tags": normalize_annotation_tags(tags or {}),
                "updated_at": utc_now(),
            }
            self._write_json_file(self._annotation_item_path(task, int(item_index)), annotation)
            subtask["completed_count"] = sum(
                1 for index in subtask["item_indexes"] if self._read_annotation(task, index) is not None
            )
            if subtask["completed_count"] >= len(subtask["item_indexes"]):
                subtask["completed_at"] = utc_now()
            if existing_annotation is None:
                task["annotation_count"] = int(task.get("annotation_count", 0)) + 1
            task["annotations_storage"] = "items"
            self._write_state(state)
            return deepcopy(annotation)

    def save_quality_check(
        self,
        task_id: str,
        item_index: int,
        username: str,
        mos: int,
        tags: dict[str, Any],
    ) -> dict[str, Any]:
        username = (username or "").strip()
        if not username:
            raise ValueError("username is required")
        if int(mos) < 1 or int(mos) > 5:
            raise ValueError("MOS must be between 1 and 5")
        with self._lock:
            state = self._read_state()
            task = self._require_task(state, task_id)
            annotation = self._read_annotation(task, int(item_index))
            if annotation is None:
                raise ValueError("annotation is required before quality check")

            now = utc_now()
            history = annotation.setdefault("qc_history", [])
            record = {
                "id": str(uuid.uuid4()),
                "username": username,
                "updated_at": now,
                "before": {
                    "mos": annotation.get("mos"),
                    "tags": deepcopy(annotation.get("tags", {})),
                },
                "after": {
                    "mos": int(mos),
                    "tags": normalize_annotation_tags(tags or {}),
                },
            }
            history.append(record)
            annotation["mos"] = record["after"]["mos"]
            annotation["tags"] = deepcopy(record["after"]["tags"])
            annotation["updated_at"] = now
            annotation["quality_checked_at"] = now
            self._write_json_file(self._annotation_item_path(task, int(item_index)), annotation)
            task["annotations_storage"] = "items"
            self._write_state(state)
            return annotation_with_qc_summary(annotation)

    def undo_quality_check(self, task_id: str, item_index: int, username: str) -> dict[str, Any]:
        username = (username or "").strip()
        if not username:
            raise ValueError("username is required")
        with self._lock:
            state = self._read_state()
            task = self._require_task(state, task_id)
            annotation = self._read_annotation(task, int(item_index))
            if annotation is None:
                raise ValueError("annotation is required before quality check undo")

            history = annotation.get("qc_history", [])
            target = next(
                (
                    record
                    for record in reversed(history)
                    if isinstance(record, dict)
                    and str(record.get("username") or "") == username
                    and not record.get("undone_at")
                ),
                None,
            )
            if target is None:
                raise ValueError("current user has no quality check edit to undo")

            now = utc_now()
            target["undone_at"] = now
            target["undone_by"] = username
            final = qc_base_result(annotation)
            for record in active_qc_history(annotation):
                after = record.get("after") if isinstance(record, dict) else None
                if not isinstance(after, dict):
                    continue
                final = {
                    "mos": after.get("mos"),
                    "tags": deepcopy(after.get("tags", {})),
                }

            annotation["mos"] = int(final["mos"])
            annotation["tags"] = normalize_annotation_tags(final.get("tags", {}))
            annotation["updated_at"] = now
            annotation["quality_checked_at"] = now
            self._write_json_file(self._annotation_item_path(task, int(item_index)), annotation)
            task["annotations_storage"] = "items"
            self._write_state(state)
            return annotation_with_qc_summary(annotation)

    def get_results(
        self,
        task_id: str,
        threshold: int = 4,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            task = self._require_task(self._read_state(), task_id)
            annotations = self._read_annotations(task)
            results = []
            for key, annotation in sorted(annotations.items(), key=lambda pair: int(pair[0])):
                if int(annotation.get("mos", 0)) < int(threshold):
                    continue
                if not result_matches_filters(annotation, filters):
                    continue
                item_index = int(key)
                item = self._read_item(task, item_index)
                results.append(
                    {
                        "item_index": item_index,
                        "src_image": item["src_image"],
                        "dst_image": item["dst_image"],
                        "original_tags": item.get("labels", {}),
                        "tags": annotation.get("tags", {}),
                        "mos": annotation.get("mos"),
                        "username": annotation.get("username"),
                        "updated_at": annotation.get("updated_at"),
                        "qc_reviewers": qc_reviewers(annotation),
                        "qc_history": deepcopy(annotation.get("qc_history", [])),
                    }
                )
            return results

    def get_result_filter_options(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._require_task(self._read_state(), task_id)
            annotations = self._read_annotations(task)
            return {
                "mos": sorted(
                    {int(annotation.get("mos")) for annotation in annotations.values() if annotation.get("mos") is not None}
                ),
                "annotators": sorted(
                    {
                        str(annotation.get("username"))
                        for annotation in annotations.values()
                        if annotation.get("username")
                    }
                ),
                "label_options": deepcopy(task.get("label_options") or visible_label_options(self._read_items(task))),
            }

    def get_statistics(
        self,
        task_id: str,
        filters: dict[str, Any] | None = None,
        combinations: list[list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            task = self._require_task(self._read_state(), task_id)
            annotations = [
                annotation
                for _, annotation in sorted(self._read_annotations(task).items(), key=lambda pair: int(pair[0]))
                if result_matches_filters(annotation, filters)
            ]
            label_options = deepcopy(task.get("label_options") or visible_label_options(self._read_items(task)))
            label_paths: list[list[str]] = []
            for group in label_options:
                for dimension in group.get("dimensions", []):
                    path = [str(group["name"]), str(dimension["name"])]
                    if path not in label_paths:
                        label_paths.append(path)
            for annotation in annotations:
                for path in flatten_label_paths(annotation.get("tags", {})):
                    if path not in label_paths:
                        label_paths.append(path)

            annotator_counter: Counter[str] = Counter()
            mos_counter: Counter[str] = Counter()
            label_counters = {json.dumps(path, ensure_ascii=False): Counter() for path in label_paths}

            for annotation in annotations:
                username = stringify_stat_value(annotation.get("username"))
                if username is not None:
                    annotator_counter[username] += 1
                mos = stringify_stat_value(annotation.get("mos"))
                if mos is not None:
                    mos_counter[mos] += 1
                for path in label_paths:
                    key = json.dumps(path, ensure_ascii=False)
                    for value in stat_values(nested_get(annotation.get("tags", {}), path)):
                        label_counters[key][value] += 1

            combo_results = []
            for dimensions in combinations or []:
                if not isinstance(dimensions, list) or len(dimensions) < 2 or len(dimensions) > 3:
                    continue
                normalized_dimensions = []
                seen_keys = set()
                for dimension in dimensions:
                    if not isinstance(dimension, dict):
                        continue
                    key = dimension_key(dimension)
                    if not key or key in seen_keys:
                        continue
                    seen_keys.add(key)
                    normalized_dimensions.append(deepcopy(dimension))
                if len(normalized_dimensions) < 2:
                    continue

                combo_counter: Counter[str] = Counter()
                for annotation in annotations:
                    parts = [combo_dimension_value(annotation, dimension) for dimension in normalized_dimensions]
                    if any(part is None for part in parts):
                        continue
                    combo_counter[" + ".join(str(part) for part in parts)] += 1
                combo_results.append(
                    {
                        "dimensions": normalized_dimensions,
                        "title": " + ".join(
                            "MOS"
                            if dimension.get("type") == "mos"
                            else "annotator"
                            if dimension.get("type") == "annotator"
                            else label_dimension_title([str(part) for part in dimension.get("path", [])])
                            for dimension in normalized_dimensions
                        ),
                        "items": stat_items(combo_counter),
                    }
                )

            return {
                "total": len(annotations),
                "filter_options": {
                    "mos": sorted(
                        {int(annotation.get("mos")) for annotation in annotations if annotation.get("mos") is not None}
                    ),
                    "annotators": sorted(
                        {
                            str(annotation.get("username"))
                            for annotation in annotations
                            if annotation.get("username")
                        }
                    ),
                    "label_options": label_options,
                },
                "available_dimensions": [
                    {"type": "mos", "label": "MOS"},
                    {"type": "annotator", "label": "annotator"},
                    *[
                        {"type": "label", "path": path, "label": label_dimension_title(path)}
                        for path in label_paths
                    ],
                ],
                "annotators": {"title": "annotator", "items": stat_items(annotator_counter)},
                "mos": {"title": "MOS", "items": stat_items(mos_counter)},
                "labels": [
                    {
                        "path": path,
                        "title": label_dimension_title(path),
                        "items": stat_items(label_counters[json.dumps(path, ensure_ascii=False)]),
                    }
                    for path in label_paths
                    if label_counters[json.dumps(path, ensure_ascii=False)]
                ],
                "combinations": combo_results,
            }

    def get_annotated_results(self, task_id: str) -> tuple[str, list[dict[str, Any]]]:
        with self._lock:
            task = self._require_task(self._read_state(), task_id)
            annotations = self._read_annotations(task)
            results = []
            for key, annotation in sorted(annotations.items(), key=lambda pair: int(pair[0])):
                item_index = int(key)
                item = self._read_item(task, item_index)
                results.append(
                    {
                        "item_index": item_index,
                        "src_image": item["src_image"],
                        "dst_image": item["dst_image"],
                        "original_tags": item.get("labels", {}),
                        "tags": annotation.get("tags", {}),
                        "mos": annotation.get("mos"),
                        "username": annotation.get("username"),
                        "updated_at": annotation.get("updated_at"),
                        "qc_reviewers": qc_reviewers(annotation),
                        "qc_history": deepcopy(annotation.get("qc_history", [])),
                    }
                )
            return task["name"], results

    def image_path(self, task_id: str, item_index: int, kind: str) -> Path:
        task = self._require_task(self._read_state(), task_id)
        item = self._read_item(task, int(item_index))
        field = "src_image" if kind == "src" else "dst_image"
        raw_path = Path(str(item[field]))
        return raw_path if raw_path.is_absolute() else Path(task["root_dir"]) / raw_path

    def _task_summary(self, task: dict[str, Any]) -> dict[str, Any]:
        assigned = sum(1 for subtask in task["subtasks"] if subtask["assigned_to"])
        completed = sum(1 for subtask in task["subtasks"] if subtask["completed_at"])
        return {
            "id": task["id"],
            "name": task["name"],
            "root_dir": task["root_dir"],
            "jsonl_path": task["jsonl_path"],
            "annotation_dir": task.get("annotation_dir", ""),
            "chunk_size": task["chunk_size"],
            "created_at": task["created_at"],
            "item_count": task.get("item_count", len(task.get("items", []))),
            "subtask_count": len(task["subtasks"]),
            "assigned_count": assigned,
            "completed_count": completed,
            "annotation_count": task.get("annotation_count", len(task.get("annotations", {}))),
        }

    def _subtask_payload(self, task: dict[str, Any], subtask: dict[str, Any]) -> dict[str, Any]:
        items = []
        task_items = self._read_subtask_items(task, subtask)
        for offset, index in enumerate(subtask["item_indexes"]):
            item = task_items[offset]
            annotation = self._read_annotation(task, index)
            labels = item.get("labels", {})
            items.append(
                {
                    "item_index": index,
                    "src_image": item["src_image"],
                    "dst_image": item["dst_image"],
                    "labels": labels,
                    "tags": labels,
                    "annotation": annotation,
                }
            )
        payload = deepcopy(subtask)
        payload["items"] = items
        payload["label_options"] = deepcopy(task.get("label_options") or visible_label_options(self._read_items(task)))
        return payload

    def _active_subtask_for_user(self, task: dict[str, Any], username: str) -> dict[str, Any] | None:
        for subtask in task["subtasks"]:
            if subtask["assigned_to"] == username and subtask["completed_at"] is None:
                return subtask
        return None

    def _find_task(self, state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
        return next((task for task in state["tasks"] if task["id"] == task_id), None)

    def _require_task(self, state: dict[str, Any], task_id: str) -> dict[str, Any]:
        task = self._find_task(state, task_id)
        if not task:
            raise KeyError("task not found")
        return task

    def _find_subtask(self, task: dict[str, Any], subtask_id: str) -> dict[str, Any] | None:
        return next((subtask for subtask in task["subtasks"] if subtask["id"] == subtask_id), None)

    def _require_subtask(self, task: dict[str, Any], subtask_id: str) -> dict[str, Any]:
        subtask = self._find_subtask(task, subtask_id)
        if not subtask:
            raise KeyError("瀛愪换鍔′笉瀛樺湪")
        return subtask


class CreateTaskJobs:
    def __init__(self, store: AnnotationStore):
        self.store = store
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "status": "running",
            "progress": 0,
            "message": "waiting to start",
            "task": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(target=self._run, args=(job_id, payload), daemon=True)
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

    def _run(self, job_id: str, payload: dict[str, Any]) -> None:
        def progress(percent: int, message: str) -> None:
            self._update(job_id, progress=max(0, min(100, int(percent))), message=message)

        try:
            task = self.store.create_task(
                payload.get("name", ""),
                payload.get("root_dir", ""),
                payload.get("jsonl_path", ""),
                int(payload.get("chunk_size") or 100),
                payload.get("annotation_dir", ""),
                progress_callback=progress,
            )
            self._update(
                job_id,
                status="completed",
                progress=100,
                message="浠诲姟鍒涘缓瀹屾垚",
                task=self.store._task_summary(task),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced through job status for the UI
            self._update(job_id, status="failed", error=str(exc), message=str(exc))


store = AnnotationStore()
create_jobs = CreateTaskJobs(store)
app = Flask(__name__, template_folder="templates", static_folder="static")


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


@app.get("/api/tasks")
def api_tasks():
    return jsonify({"tasks": store.list_tasks()})


@app.post("/api/tasks")
def api_create_task():
    data = request.get_json(force=True) or {}
    task = store.create_task(
        data.get("name", ""),
        data.get("root_dir", ""),
        data.get("jsonl_path", ""),
        int(data.get("chunk_size") or 100),
        data.get("annotation_dir", ""),
    )
    return jsonify({"task": store._task_summary(task)}), 201


@app.post("/api/tasks/jobs")
def api_create_task_job():
    data = request.get_json(force=True) or {}
    create_jobs.store = store
    job = create_jobs.start(data)
    return jsonify({"job": job}), 202


@app.get("/api/tasks/jobs/<job_id>")
def api_get_create_task_job(job_id: str):
    job = create_jobs.get(job_id)
    if job is None:
        return jsonify({"error": "create task job not found"}), 404
    return jsonify({"job": job})


@app.delete("/api/tasks/<task_id>")
def api_delete_task(task_id: str):
    if not store.delete_task(task_id):
        return jsonify({"error": "task not found"}), 404
    return jsonify({"deleted": True})


@app.post("/api/tasks/<task_id>/labels/refresh")
def api_refresh_task_labels(task_id: str):
    return jsonify({"result": store.refresh_task_labels(task_id)})


@app.post("/api/tasks/<task_id>/assign")
def api_assign_subtask(task_id: str):
    data = request.get_json(force=True) or {}
    subtask = store.assign_subtask(task_id, data.get("username", ""))
    if subtask is None:
        return jsonify({"subtask": None, "message": "no assignable subtask"}), 200
    return jsonify({"subtask": subtask})


@app.get("/api/tasks/<task_id>/subtasks/<subtask_id>")
def api_get_subtask(task_id: str, subtask_id: str):
    subtask = store.get_subtask(task_id, subtask_id)
    if subtask is None:
        abort(404)
    return jsonify({"subtask": subtask})


@app.post("/api/tasks/<task_id>/subtasks/<subtask_id>/annotations")
def api_save_annotation(task_id: str, subtask_id: str):
    data = request.get_json(force=True) or {}
    annotation = store.save_annotation(
        task_id,
        subtask_id,
        int(data.get("item_index")),
        data.get("username", ""),
        int(data.get("mos")),
        data.get("tags") or {},
    )
    return jsonify({"annotation": annotation})


@app.get("/api/tasks/<task_id>/results")
def api_results(task_id: str):
    threshold = int(request.args.get("threshold", 4))
    filters = None
    raw_filters = request.args.get("filters")
    if raw_filters:
        filters = json.loads(raw_filters)
        if not isinstance(filters, dict):
            filters = None
    return jsonify(
        {
            "results": store.get_results(task_id, threshold=threshold, filters=filters),
            "filter_options": store.get_result_filter_options(task_id),
        }
    )


@app.post("/api/tasks/<task_id>/results/<int:item_index>/qc")
def api_save_quality_check(task_id: str, item_index: int):
    data = request.get_json(force=True) or {}
    annotation = store.save_quality_check(
        task_id,
        item_index,
        data.get("username", ""),
        int(data.get("mos")),
        data.get("tags") or {},
    )
    return jsonify({"annotation": annotation})


@app.delete("/api/tasks/<task_id>/results/<int:item_index>/qc")
def api_undo_quality_check(task_id: str, item_index: int):
    data = request.get_json(force=True) or {}
    annotation = store.undo_quality_check(task_id, item_index, data.get("username", ""))
    return jsonify({"annotation": annotation})


@app.get("/api/tasks/<task_id>/statistics")
def api_statistics(task_id: str):
    filters = None
    raw_filters = request.args.get("filters")
    if raw_filters:
        filters = json.loads(raw_filters)
        if not isinstance(filters, dict):
            filters = None
    combinations = []
    raw_combinations = request.args.get("combinations")
    if raw_combinations:
        combinations = json.loads(raw_combinations)
        if not isinstance(combinations, list):
            combinations = []
    return jsonify({"statistics": store.get_statistics(task_id, filters=filters, combinations=combinations)})


@app.get("/api/tasks/<task_id>/download")
def api_download_results(task_id: str):
    export_format = (request.args.get("format") or "jsonl").lower()
    task_name, results = store.get_annotated_results(task_id)
    rows = [export_row(result) for result in results]

    if export_format == "jsonl":
        content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        if content:
            content += "\n"
        output = BytesIO(content.encode("utf-8"))
        output.seek(0)
        return send_file(
            output,
            mimetype="application/x-ndjson; charset=utf-8",
            as_attachment=True,
            download_name=safe_download_name(task_name, "annotations.jsonl"),
        )

    if export_format in {"xlsx", "excel"}:
        headers = ["item_index", "src_image", "dst_image", "original_tags", "tags", "mos", "username", "updated_at"]
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "annotations"
        sheet.append(headers)
        for row in rows:
            sheet.append([json_cell(row.get(header)) for header in headers])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=safe_download_name(task_name, "annotations.xlsx"),
        )

    return jsonify({"error": "涓嶆敮鎸佺殑涓嬭浇鏍煎紡"}), 400


@app.get("/api/tasks/<task_id>/images/<int:item_index>/<kind>")
def api_image(task_id: str, item_index: int, kind: str):
    if kind not in {"src", "dst"}:
        abort(404)
    image_path = store.image_path(task_id, item_index, kind)
    if not image_path.exists() or not image_path.is_file():
        abort(404)
    mimetype = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    if request.args.get("original") in {"1", "true", "yes"}:
        return send_file(image_path.resolve(), mimetype=mimetype)
    preview_path, preview_mimetype = resized_image_file(image_path, store.preview_cache_dir(task_id))
    return send_file(preview_path, mimetype=preview_mimetype, conditional=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("ANNOTATIONS_PORT", "5055")), debug=False, threaded=True)
