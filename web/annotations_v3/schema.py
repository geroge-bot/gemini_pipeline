from __future__ import annotations

from copy import deepcopy
from typing import Any

from web.annotations_v3 import storage


QUALITY_FIELDS = [
    {
        "field_id": "quality.mos",
        "path": ["quality", "mos"],
        "label": "MOS 分",
        "kind": "score",
        "required": True,
        "options": [1, 2, 3, 4, 5],
    },
    {
        "field_id": "quality.has_issue",
        "path": ["quality", "has_issue"],
        "label": "是否存在质量问题",
        "kind": "boolean",
        "required": True,
    },
    {
        "field_id": "quality.issue_tags",
        "path": ["quality", "issue_tags"],
        "label": "质量问题 tag",
        "kind": "multi_select",
        "required_when": {"path": ["quality", "has_issue"], "equals": True},
        "options": ["主体问题", "构图问题", "颜色问题"],
    },
]


def quality_fields(stage: str) -> list[dict[str, Any]]:
    return deepcopy(QUALITY_FIELDS)


def load_schema_snapshot(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(storage.dataset_dir(dataset_id) / "labels_schema_snapshot.json", {"version": 1, "fields": []})


def fields_for_stage(dataset_id: str, stage: str) -> list[dict[str, Any]]:
    fields = quality_fields(stage)
    fields.extend(deepcopy(load_schema_snapshot(dataset_id).get("fields", [])))
    readonly = stage == "visualize"
    for field in fields:
        if readonly:
            field["readonly"] = True
        else:
            field["editable"] = True
    return fields


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _path_key(path: list[str]) -> str:
    return "/".join(path)


def _field_map(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_path_key(field["path"]): field for field in fields}


def _set_nested(values: dict[str, Any], path: list[str], value: Any) -> None:
    target = values
    for part in path[:-1]:
        target = target.setdefault(part, {})
    target[path[-1]] = value


def normalize_value(field: dict[str, Any], value: Any) -> Any:
    kind = field["kind"]
    if kind == "score":
        if not isinstance(value, int) or value not in field.get("options", []):
            raise ValidationError("INVALID_PATCH", "MOS 必须是 1 到 5 的整数")
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValidationError("INVALID_PATCH", "boolean 字段必须是 true 或 false")
        return value
    if kind == "single_select":
        if not isinstance(value, str) or value not in field.get("options", []):
            raise ValidationError("INVALID_PATCH", "单选值不在 schema options 中")
        return value
    if kind == "multi_select":
        if not isinstance(value, list):
            raise ValidationError("INVALID_PATCH", "多选值必须是数组")
        normalized = []
        for entry in value:
            if entry not in field.get("options", []):
                raise ValidationError("INVALID_PATCH", "多选值不在 schema options 中")
            if entry not in normalized:
                normalized.append(entry)
        return normalized
    if kind == "text":
        if value is not None and not isinstance(value, str):
            raise ValidationError("INVALID_PATCH", "文本字段必须是字符串")
        return value or ""
    raise ValidationError("INVALID_PATCH", f"未知字段类型: {kind}")


def apply_changes(base_values: dict[str, Any], fields: list[dict[str, Any]], changes: list[dict[str, Any]]) -> dict[str, Any]:
    values = deepcopy(base_values)
    fields_by_path = _field_map(fields)
    for change in changes:
        if change.get("op") != "set" or not isinstance(change.get("path"), list):
            raise ValidationError("INVALID_PATCH", "patch 只支持 set 和数组 path")
        path = change["path"]
        field = fields_by_path.get(_path_key(path))
        if field is None:
            raise ValidationError("INVALID_PATCH", "未知 patch path")
        _set_nested(values, path, normalize_value(field, change.get("value")))
    if values.get("quality", {}).get("has_issue") is False:
        values.setdefault("quality", {})["issue_tags"] = []
    return values
