import json
import random
import re
import shutil
from pathlib import Path

from PIL import Image, ImageOps


JSONL_PATH = Path(r"D:\美食数据\20260427\no_error\0427_no_error.jsonl")
IMAGE_ROOT = Path(r"D:\美食数据\20260427\no_error")
OUTPUT_ROOT = Path(r"D:\美食数据\20260427\sample")
STITCHED_ROOT = OUTPUT_ROOT / "拼接图片"
SAMPLE_SIZE = 1000
RANDOM_SEED = None


def safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative path: {value!r}")
    return path


def copy_image(relative_value: str, copied: set[Path]) -> Path:
    relative_path = safe_relative_path(relative_value)
    source = IMAGE_ROOT / relative_path
    destination = OUTPUT_ROOT / relative_path

    if not source.is_file():
        raise FileNotFoundError(str(source))

    if destination not in copied:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.add(destination)

    return destination


def clean_name(value: str, limit: int = 90) -> str:
    name = Path(value).stem
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name[:limit] or "image"


def stitch_images(left_path: Path, right_path: Path, output_path: Path) -> None:
    with Image.open(left_path) as left_raw, Image.open(right_path) as right_raw:
        left = ImageOps.exif_transpose(left_raw).convert("RGB")
        right = ImageOps.exif_transpose(right_raw).convert("RGB")

        width = left.width + right.width
        height = max(left.height, right.height)
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(left, (0, (height - left.height) // 2))
        canvas.paste(right, (left.width, (height - right.height) // 2))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="JPEG", quality=95, subsampling=0)


def main() -> None:
    with JSONL_PATH.open("r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]

    if len(records) < SAMPLE_SIZE:
        raise RuntimeError(f"Only {len(records)} records found, cannot sample {SAMPLE_SIZE}.")

    sampled = random.Random(RANDOM_SEED).sample(records, SAMPLE_SIZE)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    STITCHED_ROOT.mkdir(parents=True, exist_ok=True)

    copied: set[Path] = set()
    completed: list[dict] = []
    failures: list[dict[str, str]] = []

    for index, record in enumerate(sampled, start=1):
        try:
            src_copied = copy_image(record["src_image"], copied)
            dst_copied = copy_image(record["dst_image"], copied)

            output_name = f"{index:04d}_{clean_name(record['dst_image'])}.jpg"
            stitched_path = STITCHED_ROOT / output_name
            stitch_images(src_copied, dst_copied, stitched_path)
        except Exception as exc:
            failures.append(
                {
                    "sample_index": str(index),
                    "error": str(exc),
                    "record": json.dumps(record, ensure_ascii=False),
                }
            )
            continue

        enriched = dict(record)
        enriched["sample_index"] = index
        enriched["sample_src_image"] = str(src_copied.relative_to(OUTPUT_ROOT))
        enriched["sample_dst_image"] = str(dst_copied.relative_to(OUTPUT_ROOT))
        enriched["stitched_image"] = str(stitched_path.relative_to(OUTPUT_ROOT))
        completed.append(enriched)

    sample_jsonl = OUTPUT_ROOT / "0427_no_error_sample_1000.jsonl"
    with sample_jsonl.open("w", encoding="utf-8", newline="\n") as handle:
        for record in completed:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if failures:
        failure_jsonl = OUTPUT_ROOT / "stitch_failures.jsonl"
        with failure_jsonl.open("w", encoding="utf-8", newline="\n") as handle:
            for item in failures:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"TOTAL_RECORDS={len(records)}")
    print(f"SAMPLED_RECORDS={len(sampled)}")
    print(f"COMPLETED_RECORDS={len(completed)}")
    print(f"UNIQUE_FILES_COPIED={len(copied)}")
    print(f"STITCHED_IMAGES={len(completed)}")
    print(f"FAILURES={len(failures)}")
    print(f"OUTPUT_ROOT={OUTPUT_ROOT}")
    print(f"STITCHED_ROOT={STITCHED_ROOT}")
    print(f"SAMPLE_JSONL={sample_jsonl}")


if __name__ == "__main__":
    main()
