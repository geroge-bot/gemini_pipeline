import argparse
import json
from pathlib import Path

from scripts.move_filtered import parse_generated_image_name


DEFAULT_INPUT_DIR = r"D:\202604-美食数据-历史数据整理\整理后"
IMAGE_ROOT_NAMES = {r"原始图片", r"生成图片"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def find_original_path(input_dir: Path, generated_path: Path, parsed: dict) -> Path | None:
    relative_parent = generated_path.parent.relative_to(input_dir)
    parts = list(relative_parent.parts)
    if not parts or parts[0] != r"生成图片":
        return None

    original_root = input_dir / r"原始图片" / Path(*parts[1:])
    original_stem = parsed["original_stem"]
    for ext in IMAGE_EXTENSIONS:
        candidate = original_root / f"{original_stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def build_pairs_jsonl(input_dir: str | Path, output_jsonl: str | Path) -> dict[str, int]:
    input_dir = Path(input_dir).resolve()
    output_jsonl = Path(output_jsonl).resolve()

    records: list[dict[str, str]] = []
    missing_originals = 0
    skipped_non_pairs = 0

    for image_path in sorted(input_dir.rglob("*")):
        if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        parsed = parse_generated_image_name(image_path)
        if not parsed:
            skipped_non_pairs += 1
            continue

        relative_path = image_path.relative_to(input_dir)
        if not relative_path.parts or relative_path.parts[0] not in IMAGE_ROOT_NAMES:
            skipped_non_pairs += 1
            continue

        original_path = find_original_path(input_dir, image_path, parsed)
        if original_path is None:
            missing_originals += 1
            continue

        records.append(
            {
                "src_image": str(original_path.relative_to(input_dir)),
                "dst_image": str(relative_path),
            }
        )

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    return {
        "pairs": len(records),
        "missing_originals": missing_originals,
        "skipped_non_pairs": skipped_non_pairs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export image pairs from a整理后 folder into jsonl.")
    parser.add_argument(
        "--input_dir",
        default=DEFAULT_INPUT_DIR,
        help="Root folder that contains 原始图片 and 生成图片.",
    )
    parser.add_argument("--output_jsonl", required=True, help="Output jsonl file path.")
    args = parser.parse_args()

    stats = build_pairs_jsonl(args.input_dir, args.output_jsonl)
    print(
        "Done: "
        f"pairs={stats['pairs']}, "
        f"missing_originals={stats['missing_originals']}, "
        f"skipped_non_pairs={stats['skipped_non_pairs']}"
    )


if __name__ == "__main__":
    main()
