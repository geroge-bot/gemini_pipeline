import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.convert_ori_to_std import parse_critique_log

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is optional for tests and headless runs.
    def tqdm(iterable, **kwargs):
        return iterable


DEFAULT_INPUT_DIR = r"D:\202604-美食数据-历史数据整理\咖啡厅"
DEFAULT_CRITIQUE_LOG_DIR = r"D:\202604-美食数据-历史数据整理\咖啡厅质检\critique_log"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
REJECT_LEVEL_KEYWORDS = (r"中等", r"严重", "medium", "severe")
ORIGINAL_OUTPUT_DIR_NAME = r"原始图片\202601-咖啡厅"
GENERATED_OUTPUT_DIR_NAME = r"生成图片\202601-咖啡厅"
SEED_RE = re.compile(r"^(?P<body>.+)_(?P<seed>\d{4,5})$")
PLAN_MARKER_RE = re.compile(r"_p(?P<plan>\d+)(?:_|$)")


def parse_generated_image_name(image_path: Path) -> Optional[dict[str, Any]]:
    if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None

    seed_match = SEED_RE.match(image_path.stem)
    if not seed_match:
        return None

    body = seed_match.group("body")
    seed = seed_match.group("seed")
    plan_match = PLAN_MARKER_RE.search(body)
    if not plan_match:
        return None

    plan_num = int(plan_match.group("plan"))
    original_stem = body[:plan_match.start()]
    log_stem = f"{original_stem}_p{plan_num}"

    return {
        "seed": seed,
        "plan_num": plan_num,
        "original_stem": original_stem,
        "log_stem": log_stem,
    }


def find_original_name(source_dir: Path, original_stem: str) -> Optional[str]:
    for ext in IMAGE_EXTENSIONS:
        candidate = source_dir / f"{original_stem}{ext}"
        if candidate.exists():
            return candidate.name
    return None


def load_validation_from_log(
    critique_log_dir: Path,
    rel_dir: Path,
    log_stem: str,
    seed: str,
    plan_num: int,
) -> dict[str, Any]:
    log_path = critique_log_dir / rel_dir / f"{log_stem}_critique_log_{seed}.txt"
    if not log_path.exists():
        return {}

    parsed = parse_critique_log(str(log_path))
    validation = parsed.get(plan_num, {})
    return validation if isinstance(validation, dict) else {}


def is_rejected(validation: Any) -> bool:
    if not isinstance(validation, dict):
        return False

    level = str(validation.get("level", "")).strip().lower()
    if not level:
        return False

    return any(keyword in level for keyword in REJECT_LEVEL_KEYWORDS)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def copy_keep_generated_pair(
    source_dir: Path,
    critique_log_dir: Path,
    target_dir: Path,
) -> dict[str, int]:
    source_dir = Path(source_dir).resolve()
    critique_log_dir = Path(critique_log_dir).resolve()
    target_dir = Path(target_dir).resolve()

    original_output_dir = target_dir / ORIGINAL_OUTPUT_DIR_NAME
    generated_output_dir = target_dir / GENERATED_OUTPUT_DIR_NAME
    original_output_dir.mkdir(parents=True, exist_ok=True)
    generated_output_dir.mkdir(parents=True, exist_ok=True)

    copied_originals: set[Path] = set()
    copied_generated = 0
    skipped_by_validation = 0
    missing_originals = 0
    missing_logs = 0

    for image_path in tqdm(sorted(source_dir.rglob("*")), desc="Processing images"):
        parsed = parse_generated_image_name(image_path)
        if not parsed:
            continue

        rel_dir = image_path.parent.relative_to(source_dir)
        validation = load_validation_from_log(
            critique_log_dir,
            rel_dir,
            parsed["log_stem"],
            parsed["seed"],
            parsed["plan_num"],
        )
        if not validation:
            missing_logs += 1

        if is_rejected(validation):
            skipped_by_validation += 1
            continue

        generated_target_dir = generated_output_dir / rel_dir
        generated_target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, generated_target_dir / image_path.name)

        source_json = image_path.with_suffix(".json")
        target_json = generated_target_dir / source_json.name
        json_data = load_json(source_json)
        json_data["validation"] = validation
        save_json(target_json, json_data)

        original_name = find_original_name(image_path.parent, parsed["original_stem"])
        if original_name is None:
            missing_originals += 1
            copied_generated += 1
            continue

        original_source = image_path.parent / original_name
        original_target_dir = original_output_dir / rel_dir
        original_target_dir.mkdir(parents=True, exist_ok=True)
        original_target = original_target_dir / original_name
        if original_target not in copied_originals:
            shutil.copy2(original_source, original_target)
            copied_originals.add(original_target)

        copied_generated += 1

    return {
        "copied_generated": copied_generated,
        "copied_original": len(copied_originals),
        "skipped_by_validation": skipped_by_validation,
        "missing_originals": missing_originals,
        "missing_logs": missing_logs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Move filtered data into separated original/generated folders.")
    parser.add_argument(
        "--input_dir",
        default=DEFAULT_INPUT_DIR,
        help="Source directory containing mixed original and generated images.",
    )
    parser.add_argument(
        "--critique_log_dir",
        default=DEFAULT_CRITIQUE_LOG_DIR,
        help="Directory containing critique_log files.",
    )
    parser.add_argument("--target_dir", required=True, help="Target output directory.")
    args = parser.parse_args()

    stats = copy_keep_generated_pair(args.input_dir, args.critique_log_dir, args.target_dir)
    print(
        "Done: "
        f"generated={stats['copied_generated']}, "
        f"original={stats['copied_original']}, "
        f"filtered={stats['skipped_by_validation']}, "
        f"missing_logs={stats['missing_logs']}, "
        f"missing_originals={stats['missing_originals']}"
    )


if __name__ == "__main__":
    main()
