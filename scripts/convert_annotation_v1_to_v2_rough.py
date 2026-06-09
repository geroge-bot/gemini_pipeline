"""Convert annotation v1 exports into annotation v2 import JSONL.

Usage:
    python scripts/convert_annotation_v1_to_v2_rough.py \
        --v1-jsonl /path/to/v1_filtered.jsonl \
        --v2-jsonl /path/to/v2_unfiltered.jsonl \
        --output-jsonl /path/to/v2_with_rough.jsonl

With optional second-stage review JSON:
    python scripts/convert_annotation_v1_to_v2_rough.py \
        --v1-jsonl /path/to/v1_filtered.jsonl \
        --v2-jsonl /path/to/v2_unfiltered.jsonl \
        --secondary-review-json /path/to/secondary_review.json \
        --output-jsonl /path/to/v2_with_rough_and_fine.jsonl

Rules:
    - v1 rows are matched to v2 rows by src_image/dst_image pair.
    - v1 mos and annotator are written to v2 rough_annotations.
    - Missing quality dimension means no quality issue.
    - Secondary review rows are matched by output image filename.
    - Secondary status=confirm reuses rough mos/quality as fine; other statuses set fine mos to 3.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


PATH_ANCHORS = ("原始图片", "生成图片")
QUALITY_SCORE_KEYS = ("质量维度评分", "质量评分", "质量分", "quality_score", "defect_score")
QUALITY_ISSUE_KEYS = ("质量问题", "瑕疵问题", "问题标签", "问题", "issues", "issue")
QUALITY_NOTE_KEYS = ("备注", "note", "说明")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
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
            rows.append(row)
    if not rows:
        raise ValueError("jsonl 文件为空")
    return rows


def load_json_dict(path: str | Path) -> dict[str, Any]:
    json_path = Path(path).expanduser()
    if not json_path.exists() or not json_path.is_file():
        raise ValueError(f"二次筛选 JSON 文件不存在：{json_path}")
    with json_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("二次筛选 JSON 文件必须是 dict 对象")
    return data


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_image_path(value: Any) -> str:
    path = str(value or "").replace("\\", "/").strip().strip('"').strip("'")
    path = path.lstrip("./")
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


def image_pair_key(row: dict[str, Any]) -> tuple[str, str]:
    return normalize_image_path(row.get("src_image")), normalize_image_path(row.get("dst_image"))


def normalize_filename(value: Any) -> str:
    path = str(value or "").replace("\\", "/").strip().strip('"').strip("'")
    return path.rsplit("/", 1)[-1].lower()


def clean_mos(value: Any) -> int:
    try:
        mos = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("MOS 分必须在 1-5 之间") from exc
    if mos < 1 or mos > 5:
        raise ValueError("MOS 分必须在 1-5 之间")
    return mos


def clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是", "有", "存在"}
    return bool(value)


def iter_dict_values(value: Any) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if isinstance(value, dict):
        values.append(value)
        for child in value.values():
            values.extend(iter_dict_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(iter_dict_values(child))
    return values


def first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for source in [row, row.get("tags", {})]:
        for candidate in iter_dict_values(source):
            for key in keys:
                if key in candidate and candidate[key] not in (None, ""):
                    return candidate[key]
    return None


def normalize_issues(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = value.replace("\n", ",").replace("；", ",").replace(";", ",").split(",")
    if not isinstance(value, list):
        value = [value]
    issues = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in issues:
            issues.append(text)
    return issues


def quality_payload(row: dict[str, Any], quality_pass_min: int) -> dict[str, Any]:
    explicit_defect = first_value(row, ("has_defect", "有瑕疵", "是否有瑕疵", "是否有质量问题"))
    quality_score = first_value(row, QUALITY_SCORE_KEYS)
    issues = normalize_issues(first_value(row, QUALITY_ISSUE_KEYS))
    note = str(first_value(row, QUALITY_NOTE_KEYS) or "").strip()
    if explicit_defect is not None:
        has_defect = clean_bool(explicit_defect)
    elif quality_score is not None:
        try:
            has_defect = int(quality_score) < int(quality_pass_min)
        except (TypeError, ValueError):
            has_defect = clean_bool(quality_score)
    else:
        has_defect = bool(issues)
    return {
        "has_defect": has_defect,
        "primary_issue": issues[0] if issues else "",
        "issues": issues,
        "other_issue": "",
        "note": note,
    }


def rough_annotation_from_v1(row: dict[str, Any], quality_pass_min: int) -> dict[str, Any]:
    username = str(row.get("username") or row.get("annotator") or row.get("labeler") or "imported").strip()
    annotation = {
        "username": username or "imported",
        "mos": clean_mos(row.get("mos")),
        **quality_payload(row, quality_pass_min),
    }
    if row.get("updated_at") not in (None, ""):
        annotation["updated_at"] = row.get("updated_at")
    return annotation


def has_existing_rough(row: dict[str, Any]) -> bool:
    if isinstance(row.get("rough"), dict):
        return True
    annotations = row.get("rough_annotations")
    return isinstance(annotations, list) and any(isinstance(entry, dict) for entry in annotations)


def has_existing_fine(row: dict[str, Any]) -> bool:
    if isinstance(row.get("fine"), dict):
        return True
    annotations = row.get("fine_annotations")
    return isinstance(annotations, list) and any(isinstance(entry, dict) for entry in annotations)


def first_screen_annotation(row: dict[str, Any], stage: str) -> dict[str, Any] | None:
    annotations = row.get(f"{stage}_annotations")
    if isinstance(annotations, list):
        for annotation in annotations:
            if isinstance(annotation, dict):
                return annotation
    annotation = row.get(stage)
    return annotation if isinstance(annotation, dict) else None


def build_v1_index(rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_count = 0
    for row in rows:
        key = image_pair_key(row)
        if key in index:
            duplicate_count += 1
            continue
        index[key] = row
    return index, duplicate_count


def build_secondary_review_index(data: dict[str, Any]) -> tuple[dict[str, tuple[int, dict[str, Any]]], int, int]:
    index: dict[str, tuple[int, dict[str, Any]]] = {}
    duplicate_count = 0
    valid_count = 0
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        review_id = valid_count
        valid_count += 1
        candidate_keys = []
        for candidate_key in [normalize_filename(key)]:
            if candidate_key and candidate_key not in candidate_keys:
                candidate_keys.append(candidate_key)
        review_data = value.get("data")
        if isinstance(review_data, dict):
            candidate_key = normalize_filename(review_data.get("file"))
            if candidate_key and candidate_key not in candidate_keys:
                candidate_keys.append(candidate_key)
        for candidate_key in candidate_keys:
            if candidate_key in index:
                duplicate_count += 1
                continue
            index[candidate_key] = (review_id, value)
    return index, valid_count, duplicate_count


def fine_annotation_from_secondary_review(
    rough_annotation: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    annotation = deepcopy(rough_annotation)
    annotation["username"] = str(
        review.get("reviewer") or review.get("assigned_to") or annotation.get("username") or "imported"
    ).strip() or "imported"
    if str(review.get("status") or "").strip().lower() != "confirm":
        annotation["mos"] = 3
    if review.get("review_time") not in (None, ""):
        annotation["updated_at"] = review.get("review_time")
    return annotation


def apply_secondary_review_fine_annotations(
    output_rows: list[dict[str, Any]],
    secondary_review_json: str | Path,
    *,
    overwrite_fine: bool = False,
) -> dict[str, int]:
    secondary_data = load_json_dict(secondary_review_json)
    secondary_index, secondary_count, duplicate_secondary_keys = build_secondary_review_index(secondary_data)
    matched_review_ids: set[int] = set()
    stats = {
        "secondary_rows": secondary_count,
        "secondary_matched": 0,
        "secondary_unmatched": 0,
        "secondary_missing_rough": 0,
        "skipped_existing_fine": 0,
        "duplicate_secondary_keys": duplicate_secondary_keys,
        "updated_fine_rows": 0,
    }

    for row in output_rows:
        key = normalize_filename(row.get("dst_image"))
        match = secondary_index.get(key)
        if match is None:
            continue
        review_id, review = match
        matched_review_ids.add(review_id)
        if has_existing_fine(row) and not overwrite_fine:
            stats["skipped_existing_fine"] += 1
            continue
        rough_annotation = first_screen_annotation(row, "rough")
        if rough_annotation is None:
            stats["secondary_missing_rough"] += 1
            continue
        fine_annotation = fine_annotation_from_secondary_review(rough_annotation, review)
        row["fine_annotations"] = [fine_annotation]
        row["fine"] = deepcopy(fine_annotation)
        stats["secondary_matched"] += 1
        stats["updated_fine_rows"] += 1

    stats["secondary_unmatched"] = secondary_count - len(matched_review_ids)
    return stats


def convert_annotation_v1_to_v2_rough(
    v1_jsonl: str | Path,
    v2_jsonl: str | Path,
    output_jsonl: str | Path,
    *,
    overwrite_rough: bool = False,
    overwrite_fine: bool = False,
    quality_pass_min: int = 4,
    secondary_review_json: str | Path | None = None,
) -> dict[str, int]:
    v1_rows = load_jsonl(v1_jsonl)
    v2_rows = load_jsonl(v2_jsonl)
    v1_index, duplicate_v1_pairs = build_v1_index(v1_rows)
    output_rows = deepcopy(v2_rows)
    matched_keys: set[tuple[str, str]] = set()
    stats = {
        "v1_rows": len(v1_rows),
        "v2_rows": len(v2_rows),
        "matched": 0,
        "unmatched_v1": 0,
        "skipped_existing_rough": 0,
        "duplicate_v1_pairs": duplicate_v1_pairs,
        "updated_v2_rows": 0,
    }

    for row in output_rows:
        key = image_pair_key(row)
        source_row = v1_index.get(key)
        if source_row is None:
            continue
        matched_keys.add(key)
        if has_existing_rough(row) and not overwrite_rough:
            stats["skipped_existing_rough"] += 1
            continue
        annotation = rough_annotation_from_v1(source_row, quality_pass_min)
        row["rough_annotations"] = [annotation]
        row["rough"] = deepcopy(annotation)
        stats["matched"] += 1
        stats["updated_v2_rows"] += 1

    stats["unmatched_v1"] = len([key for key in v1_index if key not in matched_keys])
    if secondary_review_json:
        stats.update(
            apply_secondary_review_fine_annotations(
                output_rows,
                secondary_review_json,
                overwrite_fine=overwrite_fine,
            )
        )
    write_jsonl(output_jsonl, output_rows)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert annotation v1 exported jsonl into annotation v2 rough-screening import jsonl."
    )
    parser.add_argument("--v1-jsonl", required=True, help="Annotation v1 exported and filtered jsonl.")
    parser.add_argument("--v2-jsonl", required=True, help="Annotation v2 exported unfiltered jsonl.")
    parser.add_argument("--output-jsonl", required=True, help="Output v2 jsonl with rough annotations filled.")
    parser.add_argument(
        "--secondary-review-json",
        default="",
        help="Optional second-stage review JSON dict. Matched by output image filename and written as fine annotations.",
    )
    parser.add_argument("--overwrite-rough", action="store_true", help="Overwrite existing v2 rough annotations.")
    parser.add_argument("--overwrite-fine", action="store_true", help="Overwrite existing v2 fine annotations.")
    parser.add_argument(
        "--quality-pass-min",
        type=int,
        default=4,
        help="Minimum quality score considered no quality issue when quality score exists. Default: 4.",
    )
    args = parser.parse_args()
    stats = convert_annotation_v1_to_v2_rough(
        args.v1_jsonl,
        args.v2_jsonl,
        args.output_jsonl,
        overwrite_rough=args.overwrite_rough,
        overwrite_fine=args.overwrite_fine,
        quality_pass_min=args.quality_pass_min,
        secondary_review_json=args.secondary_review_json or None,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
