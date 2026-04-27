import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import DEFAULT_MODEL_ANALYSIS, DEFAULT_SERVICE_TEXT
from pipeline.modules.two_image_labeler import TwoImageLabelingModule
from pipeline.utils.client_factory import create_client_from_service


DEFAULT_MAX_WORKERS = 50
PairLabeler = Callable[[Path, Path], dict]


def _path_from_record(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def _resolve_input_path(input_root: Path, value: str | Path) -> Path:
    path = _path_from_record(value)
    if path.is_absolute():
        return path
    return input_root / path


def _relative_output_path(input_root: Path, value: str | Path) -> Path:
    path = _path_from_record(value)
    if not path.is_absolute():
        return path

    try:
        return path.relative_to(input_root)
    except ValueError:
        return Path(path.name)


def get_output_json_path(output_dir: str | Path, generated_relative_path: str | Path) -> Path:
    generated_relative_path = _path_from_record(generated_relative_path)
    return Path(output_dir) / generated_relative_path.with_suffix(".json")


def iter_jsonl_records(jsonl_path: str | Path) -> Iterable[tuple[int, dict]]:
    with Path(jsonl_path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            yield line_no, json.loads(line)


def _get_pair_fields(record: dict) -> tuple[str, str]:
    original = record.get("src_image") or record.get("original_image_path")
    generated = record.get("dst_image") or record.get("generated_image_path")
    if not original:
        raise ValueError("Missing source image field: expected src_image or original_image_path")
    if not generated:
        raise ValueError("Missing target image field: expected dst_image or generated_image_path")
    return original, generated


def _load_existing_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Existing JSON is not an object: {path}")
    return data


def label_pair_with_module(
    original_path: str | Path,
    generated_path: str | Path,
    *,
    client,
    model: str,
    module_factory=TwoImageLabelingModule,
) -> dict:
    module = module_factory(model=model)
    return module._label_pair(
        client=client,
        model=model,
        original_image_path=str(original_path),
        generated_image_path=str(generated_path),
    )


def label_pairs_jsonl(
    *,
    jsonl_path: str | Path,
    output_dir: str | Path,
    input_root: str | Path | None = None,
    label_func: PairLabeler,
    limit: int | None = None,
    delay_seconds: float = 0.0,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, int]:
    jsonl_path = Path(jsonl_path).resolve()
    input_root = Path(input_root).resolve() if input_root else jsonl_path.parent
    output_dir = Path(output_dir).resolve()

    stats = {"processed": 0, "skipped": 0, "failed": 0}
    max_workers = max(1, max_workers)

    pending_records: list[tuple[int, int, str, str, Path, Path, Path]] = []
    for index, (line_no, record) in enumerate(iter_jsonl_records(jsonl_path), 1):
        try:
            src_image, dst_image = _get_pair_fields(record)
            original_path = _resolve_input_path(input_root, src_image)
            generated_path = _resolve_input_path(input_root, dst_image)
            generated_relative_path = _relative_output_path(input_root, dst_image)
            out_json = get_output_json_path(output_dir, generated_relative_path)

            pending_records.append(
                (index, line_no, src_image, dst_image, original_path, generated_path, out_json)
            )
            if limit is not None and len(pending_records) >= limit:
                break
        except Exception as exc:
            print(f"[{index}] [ERROR] line={line_no}: {exc}")
            stats["failed"] += 1

    def run_one(
        *,
        index: int,
        src_image: str,
        dst_image: str,
        original_path: Path,
        generated_path: Path,
        out_json: Path,
    ) -> tuple[int, str, Path]:
        labels = label_func(original_path, generated_path)
        payload = _load_existing_json(out_json)
        payload.update(
            {
                "src_image": src_image,
                "dst_image": dst_image,
                "original_image_path": str(original_path),
                "generated_image_path": str(generated_path),
                "labels": labels,
            }
        )
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return index, dst_image, out_json

    futures = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for submit_idx, (
            index,
            line_no,
            src_image,
            dst_image,
            original_path,
            generated_path,
            out_json,
        ) in enumerate(pending_records):
            if delay_seconds > 0 and submit_idx > 0:
                time.sleep(delay_seconds)
            future = executor.submit(
                run_one,
                index=index,
                src_image=src_image,
                dst_image=dst_image,
                original_path=original_path,
                generated_path=generated_path,
                out_json=out_json,
            )
            futures[future] = (index, line_no)

        for future in as_completed(futures):
            index, line_no = futures[future]
            try:
                completed_index, dst_image, out_json = future.result()
                print(f"[{completed_index}] [OK] {dst_image} -> {out_json}")
                stats["processed"] += 1
            except Exception as exc:
                print(f"[{index}] [ERROR] line={line_no}: {exc}")
                stats["failed"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run TwoImageLabelingModule for image pairs listed in a jsonl file."
    )
    parser.add_argument(
        "jsonl_path",
        help="Input jsonl file with src_image/dst_image or original_image_path/generated_image_path.",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory where label JSON files will be written with generated-image relative paths.",
    )
    parser.add_argument(
        "--input_root",
        default=None,
        help="Root directory for relative paths in jsonl. Defaults to the jsonl file directory.",
    )
    parser.add_argument(
        "--service",
        default=DEFAULT_SERVICE_TEXT,
        help="Text service name from pipeline/utils/services.md.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_ANALYSIS, help="Model for labeling.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N new records.")
    parser.add_argument(
        "--max_workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Maximum number of concurrent labeling tasks.",
    )
    parser.add_argument(
        "--delay_seconds",
        type=float,
        default=0.0,
        help="Optional delay between API calls after the first processed pair.",
    )
    args = parser.parse_args()

    client = create_client_from_service(args.service)

    def label_func(original_path: Path, generated_path: Path) -> dict:
        return label_pair_with_module(
            original_path,
            generated_path,
            client=client,
            model=args.model,
        )

    stats = label_pairs_jsonl(
        jsonl_path=args.jsonl_path,
        output_dir=args.output_dir,
        input_root=args.input_root,
        label_func=label_func,
        limit=args.limit,
        delay_seconds=args.delay_seconds,
        max_workers=args.max_workers,
    )
    print(
        "Done: "
        f"processed={stats['processed']}, "
        f"skipped={stats['skipped']}, "
        f"failed={stats['failed']}"
    )


if __name__ == "__main__":
    main()
