from __future__ import annotations

import hashlib
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from web.annotations_v3 import datasets, records, schema, storage


class ImportRowError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def to_path_values(labels: dict[str, Any]) -> list[dict[str, Any]]:
    values = []
    for group, group_value in labels.items():
        if isinstance(group_value, dict):
            for key, value in group_value.items():
                values.append({"path": [str(group), str(key)], "value": value})
    return values


def normalize_envelope(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("format") == "annotations_v3.labels":
        envelope = deepcopy(row)
    else:
        labels = row.get("labels") or row.get("object_labels") or row.get("original_labels") or {}
        corrected = row.get("corrected_labels") or {}
        envelope = {
            "format": "annotations_v3.labels",
            "version": 1,
            "match": {
                key: row.get(key)
                for key in ("item_id", "external_id", "src_image", "dst_image", "item_index")
                if key in row
            },
            "labels": {"schema": "legacy", "schema_version": "v2", "values": to_path_values(labels)},
            "annotations": {},
            "meta": {"source_system": "legacy_v2"},
        }
        if corrected:
            envelope["annotations"]["label_correction"] = {
                "username": row.get("username", "import"),
                "values": to_path_values(corrected),
            }
        for key in ("rough_annotations", "fine_annotations"):
            if key in row:
                stage = "rough" if key.startswith("rough") else "fine"
                envelope["annotations"][stage] = row[key]
    if not envelope.get("labels", {}).get("values") and not envelope.get("annotations"):
        raise ImportRowError("EMPTY_IMPORT_ROW", "导入行没有 labels 或 annotations")
    return envelope


def build_match_indexes(dataset_id: str) -> dict[str, dict[Any, str]]:
    indexes: dict[str, dict[Any, str]] = {"item_id": {}, "external_id": {}, "pair": {}, "item_index": {}}
    for item in datasets.load_items(dataset_id):
        indexes["item_id"][item["item_id"]] = item["item_id"]
        if item.get("external_id") is not None:
            indexes["external_id"][item["external_id"]] = item["item_id"]
        indexes["pair"][(item["src_image"], item["dst_image"])] = item["item_id"]
        indexes["item_index"][item["item_index"]] = item["item_id"]
    return indexes


def match_item_id(indexes: dict[str, dict[Any, str]], match: dict[str, Any]) -> str | None:
    if match.get("item_id") in indexes["item_id"]:
        return indexes["item_id"][match["item_id"]]
    if match.get("external_id") in indexes["external_id"]:
        return indexes["external_id"][match["external_id"]]
    pair = (match.get("src_image"), match.get("dst_image"))
    if pair in indexes["pair"]:
        return indexes["pair"][pair]
    if match.get("item_index") in indexes["item_index"]:
        return indexes["item_index"][match["item_index"]]
    return None


def path_key(path: list[str]) -> str:
    return "/".join(str(part) for part in path)


def canonical_paths(dataset_id: str) -> set[str]:
    known = set()
    for field in schema.load_schema_snapshot(dataset_id).get("fields", []):
        path = field.get("path") or []
        if path and path[0] == "labels":
            path = path[1:]
        known.add(path_key(path))
    return known


def normalize_label_values(
    dataset_id: str,
    values: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot = schema.load_schema_snapshot(dataset_id)
    path_aliases = snapshot.get("path_aliases", {})
    value_aliases = snapshot.get("value_aliases", {})
    known = canonical_paths(dataset_id)
    accepted: dict[str, Any] = {}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for label in values:
        raw_path = [str(part) for part in label.get("path") or []]
        key = path_key(raw_path)
        path = path_aliases.get(key, raw_path)
        normalized_key = path_key(path)
        if normalized_key not in known:
            errors.append({"code": "UNKNOWN_LABEL_PATH", "message": "未知标签路径", "path": path})
            continue
        value = label.get("value")
        alias_map = value_aliases.get(normalized_key, {})
        if value in alias_map:
            warnings.append(
                {
                    "code": "VALUE_ALIAS_USED",
                    "message": f"{value} normalized to {alias_map[value]}",
                    "path": path,
                }
            )
            value = alias_map[value]
        if normalized_key in accepted:
            warnings.append({"code": "DUPLICATE_LABEL_PATH", "message": "同一行重复标签路径，后值覆盖前值", "path": path})
        accepted[normalized_key] = {"path": path, "value": value}
    return accepted, warnings, errors


def imports_dir(dataset_id: str) -> Path:
    return storage.dataset_dir(dataset_id) / "imports"


def import_report_path(dataset_id: str, import_id: str) -> Path:
    return imports_dir(dataset_id) / f"{import_id}.json"


def save_import_report(dataset_id: str, report: dict[str, Any]) -> None:
    storage.write_json_atomic(import_report_path(dataset_id, report["import_id"]), report)


def load_import_report(dataset_id: str, import_id: str) -> dict[str, Any]:
    report = storage.read_json(import_report_path(dataset_id, import_id), None)
    if report is None:
        raise FileNotFoundError(import_id)
    return report


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def import_source_seen(dataset_id: str, source_hash: str) -> bool:
    directory = imports_dir(dataset_id)
    if not directory.exists():
        return False
    for report_path in directory.glob("*.json"):
        report = storage.read_json(report_path, {})
        if report.get("source_hash") == source_hash and report.get("mode") != "dry_run":
            return True
    return False


def set_nested(target: dict[str, Any], path: list[str], value: Any) -> bool:
    current = target
    for part in path[:-1]:
        current = current.setdefault(part, {})
    old_value = current.get(path[-1])
    if old_value == value:
        return False
    current[path[-1]] = value
    return True


def get_nested(target: dict[str, Any], path: list[str]) -> Any:
    current: Any = target
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return "__missing__"
        current = current[part]
    return current


def apply_label_merge(item_doc: dict[str, Any], accepted: dict[str, Any], merge_policy: str, import_id: str) -> bool:
    if merge_policy not in {"patch_labels", "replace_labels", "keep_existing"}:
        raise ImportRowError("INVALID_MERGE_POLICY", "未知 merge_policy")
    labels = item_doc.setdefault("imported_labels", {})
    changed = False
    if merge_policy == "replace_labels":
        next_labels: dict[str, Any] = {}
        for value in accepted.values():
            set_nested(next_labels, value["path"], {"value": value["value"], "import_id": import_id})
        changed = labels != next_labels
        item_doc["imported_labels"] = next_labels
        return changed
    for value in accepted.values():
        if merge_policy == "keep_existing" and get_nested(labels, value["path"]) != "__missing__":
            continue
        changed = set_nested(labels, value["path"], {"value": value["value"], "import_id": import_id}) or changed
    return changed


def apply_stage_policy(
    item_doc: dict[str, Any],
    annotations: dict[str, Any],
    policy: str,
    import_id: str,
    meta: dict[str, Any],
) -> bool:
    if not annotations:
        return False
    if policy == "audit_only":
        item_doc.setdefault("import_audit", []).append({"import_id": import_id, "annotations": annotations, "meta": meta})
        return True
    if policy == "import_as_external_snapshot":
        item_doc.setdefault("external_stage_snapshots", []).append(
            {"import_id": import_id, "annotations": annotations, "meta": meta}
        )
        return True
    if policy == "replace_effective_record":
        for stage, value in annotations.items():
            if stage not in {"rough", "fine", "label", "label_correction"}:
                continue
            normalized_stage = "label" if stage == "label_correction" else stage
            previous = deepcopy(item_doc.get(normalized_stage))
            record = {
                "record_id": str(uuid.uuid4()),
                "assignment_id": "import",
                "username": value.get("username", "import") if isinstance(value, dict) else "import",
                "values": value.get("values", value) if isinstance(value, dict) else value,
                "version": "",
                "status": "effective",
                "updated_at": time.time(),
                "import_id": import_id,
                "source_system": meta.get("source_system"),
                "replaced_record": previous,
            }
            record["version"] = records.record_version(record)
            item_doc[normalized_stage] = record
        return True
    raise ImportRowError("INVALID_STAGE_RECORD_POLICY", "未知 stage_record_policy")


def run_import(
    dataset_id: str,
    path: str,
    mode: str,
    merge_policy: str,
    stage_record_policy: str,
) -> dict[str, Any]:
    if mode not in {"dry_run", "commit"}:
        raise ImportRowError("INVALID_IMPORT_MODE", "未知 import mode")
    import_id = uuid.uuid4().hex
    source_path = Path(path)
    source_hash = file_hash(source_path)
    report = {
        "import_id": import_id,
        "mode": mode,
        "source_hash": source_hash,
        "status": "completed",
        "total_rows": 0,
        "matched_rows": 0,
        "updated_items": 0,
        "unchanged_rows": 0,
        "unmatched_rows": 0,
        "accepted_labels": 0,
        "rejected_labels": 0,
        "warnings": [],
        "errors": [],
    }
    rows = storage.read_jsonl_objects(source_path)
    with storage.dataset_lock(dataset_id):
        indexes = build_match_indexes(dataset_id)
        records_doc = records.load_records(dataset_id)
        if mode == "commit" and import_source_seen(dataset_id, source_hash):
            report["warnings"].append({"code": "DUPLICATE_IMPORT_SOURCE", "message": "同一源文件 hash 已导入过"})
        for line_number, row in rows:
            report["total_rows"] += 1
            try:
                envelope = normalize_envelope(row)
                item_id = match_item_id(indexes, envelope.get("match", {}))
                if item_id is None:
                    report["unmatched_rows"] += 1
                    report["errors"].append({"line": line_number, "code": "ITEM_NOT_MATCHED", "message": "未匹配到 item"})
                    continue
                report["matched_rows"] += 1
                accepted, warnings, errors = normalize_label_values(
                    dataset_id,
                    envelope.get("labels", {}).get("values", []),
                )
                report["warnings"].extend({**warning, "line": line_number} for warning in warnings)
                report["errors"].extend({**error, "line": line_number} for error in errors)
                report["accepted_labels"] += len(accepted)
                report["rejected_labels"] += len(errors)
                if mode == "dry_run":
                    continue
                item_doc = records_doc.setdefault(item_id, {})
                changed = apply_label_merge(item_doc, accepted, merge_policy, import_id)
                changed = (
                    apply_stage_policy(
                        item_doc,
                        envelope.get("annotations", {}),
                        stage_record_policy,
                        import_id,
                        envelope.get("meta", {}),
                    )
                    or changed
                )
                if changed:
                    report["updated_items"] += 1
                else:
                    report["unchanged_rows"] += 1
            except ImportRowError as exc:
                report["errors"].append({"line": line_number, "code": exc.code, "message": str(exc)})
        if mode == "commit":
            records.save_records(dataset_id, records_doc)
        save_import_report(dataset_id, report)
    return report
