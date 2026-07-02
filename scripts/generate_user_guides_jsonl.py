import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import DEFAULT_MODEL_ANALYSIS, DEFAULT_SERVICE_TEXT
from pipeline.modules.user_guide_generator import (
    USER_GUIDE_FIELDS,
    UserGuideGeneratorModule,
    has_compose_user_guide,
)
from pipeline.utils.client_factory import create_client_from_service


DEFAULT_MAX_WORKERS = 50
DEFAULT_CHECKPOINT_INTERVAL = 50
DEFAULT_GUIDE_FIELD = "用户指引"
GuideGenerator = Callable[[Path, Path], Any]


def iter_jsonl_records(jsonl_path: str | Path) -> Iterable[tuple[int, dict]]:
    with Path(jsonl_path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            yield line_no, json.loads(line)


def _get_pair_fields(record: dict) -> tuple[str, str]:
    original = (
        record.get("input_image")
        or record.get("src_image")
        or record.get("original_image_path")
    )
    generated = (
        record.get("output_image")
        or record.get("dst_image")
        or record.get("generated_image_path")
    )
    if not original:
        raise ValueError("Missing source image field: expected input_image, src_image, or original_image_path")
    if not generated:
        raise ValueError("Missing target image field: expected output_image, dst_image, or generated_image_path")
    return original, generated


def _resolve_path(input_root: Path, value: str | Path) -> Path:
    path = value if isinstance(value, Path) else Path(value)
    if path.is_absolute():
        return path
    return input_root / path


def generate_guide_with_module(
    original_path: str | Path,
    generated_path: str | Path,
    *,
    client,
    model: str,
) -> dict:
    module = UserGuideGeneratorModule(model=model)
    return module._generate_user_guide(
        client=client,
        model=model,
        original_image_path=str(original_path),
        generated_image_path=str(generated_path),
    )


def format_guide_preview(guide: Any) -> str:
    if isinstance(guide, list):
        previews = []
        for index, item in enumerate(guide, 1):
            if isinstance(item, dict):
                text = " | ".join(str(item[field]) for field in USER_GUIDE_FIELDS if field in item)
                previews.append(f"{index}. {text}")
        return " || ".join(previews)
    if isinstance(guide, dict):
        return " | ".join(str(guide[field]) for field in USER_GUIDE_FIELDS if field in guide)
    return str(guide)


def has_successful_guide(value: object) -> bool:
    return has_compose_user_guide(value)


def write_jsonl_checkpoint(
    *,
    records: list[tuple[int, dict]],
    output_path: Path,
    backup: bool,
    backup_written: bool,
) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for _line_no, record in records) + "\n",
        encoding="utf-8",
    )
    if backup and not backup_written and output_path.exists():
        backup_path = output_path.with_suffix(output_path.suffix + ".bak")
        backup_path.write_text(output_path.read_text(encoding="utf-8"), encoding="utf-8")
        backup_written = True
    tmp_path.replace(output_path)
    return backup_written


def generate_user_guides_jsonl(
    *,
    jsonl_path: str | Path,
    output_path: str | Path | None = None,
    guide_func: GuideGenerator,
    input_root: str | Path | None = None,
    guide_field: str = DEFAULT_GUIDE_FIELD,
    overwrite: bool = False,
    limit: int | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    backup: bool = True,
    checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
) -> dict[str, int]:
    jsonl_path = Path(jsonl_path).resolve()
    output_path = Path(output_path).resolve() if output_path else jsonl_path
    input_root = Path(input_root).resolve() if input_root else jsonl_path.parent
    max_workers = max(1, max_workers)
    checkpoint_interval = max(1, checkpoint_interval)

    records: list[tuple[int, dict]] = list(iter_jsonl_records(jsonl_path))
    stats = {"processed": 0, "skipped": 0, "failed": 0}

    pending: list[tuple[int, int, dict, Path, Path]] = []
    for index, (line_no, record) in enumerate(records, 1):
        try:
            if has_successful_guide(record.get(guide_field)) and not overwrite:
                stats["skipped"] += 1
                continue

            src_image, dst_image = _get_pair_fields(record)
            original_path = _resolve_path(input_root, src_image)
            generated_path = _resolve_path(input_root, dst_image)
            pending.append((index, line_no, record, original_path, generated_path))
            if limit is not None and len(pending) >= limit:
                break
        except Exception as exc:
            record[guide_field] = {"error": str(exc)}
            stats["failed"] += 1
            print(f"[{index}] [ERROR] line={line_no}: {exc}")

    def run_one(
        *,
        index: int,
        line_no: int,
        original_path: Path,
        generated_path: Path,
    ) -> tuple[int, int, dict]:
        guide = guide_func(original_path, generated_path)
        return index, line_no, guide

    futures = {}
    completed_since_checkpoint = 0
    backup_written = False
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, line_no, _record, original_path, generated_path in pending:
            future = executor.submit(
                run_one,
                index=index,
                line_no=line_no,
                original_path=original_path,
                generated_path=generated_path,
            )
            futures[future] = (index, line_no)

        for future in as_completed(futures):
            index, line_no = futures[future]
            record = records[index - 1][1]
            try:
                _completed_index, _completed_line_no, guide = future.result()
                record[guide_field] = guide
                stats["processed"] += 1
                completed_since_checkpoint += 1
                print(f"[{index}] [OK] {format_guide_preview(guide)}")
            except Exception as exc:
                record[guide_field] = {"error": str(exc)}
                stats["failed"] += 1
                completed_since_checkpoint += 1
                print(f"[{index}] [ERROR] line={line_no}: {exc}")

            if completed_since_checkpoint >= checkpoint_interval:
                backup_written = write_jsonl_checkpoint(
                    records=records,
                    output_path=output_path,
                    backup=backup,
                    backup_written=backup_written,
                )
                completed_since_checkpoint = 0

    write_jsonl_checkpoint(
        records=records,
        output_path=output_path,
        backup=backup,
        backup_written=backup_written,
    )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate concise user guides for image pairs in a jsonl file and write them back."
    )
    parser.add_argument("jsonl_path", help="Input jsonl file with input_image/output_image fields.")
    parser.add_argument(
        "--output_path",
        default=None,
        help="Optional destination jsonl path. Defaults to overwriting jsonl_path.",
    )
    parser.add_argument(
        "--input_root",
        default=None,
        help="Root directory for relative image paths. Defaults to the jsonl file directory.",
    )
    parser.add_argument(
        "--service",
        default=DEFAULT_SERVICE_TEXT,
        help="Text service name from pipeline/utils/services.md.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_ANALYSIS, help="Vision text model.")
    parser.add_argument(
        "--guide_field",
        default=DEFAULT_GUIDE_FIELD,
        help="Field name to write into each jsonl record.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Regenerate existing guide field values.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N records.")
    parser.add_argument(
        "--max_workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Maximum number of concurrent guide generation tasks.",
    )
    parser.add_argument("--no_backup", action="store_true", help="Do not create a .bak beside the jsonl file.")
    parser.add_argument(
        "--checkpoint_interval",
        type=int,
        default=DEFAULT_CHECKPOINT_INTERVAL,
        help="Write intermediate results every N completed records so reruns can resume.",
    )
    args = parser.parse_args()

    thread_state = threading.local()

    def get_thread_client():
        client = getattr(thread_state, "client", None)
        if client is None:
            client = create_client_from_service(args.service)
            thread_state.client = client
        return client

    def guide_func(original_path: Path, generated_path: Path) -> dict:
        return generate_guide_with_module(
            original_path,
            generated_path,
            client=get_thread_client(),
            model=args.model,
        )

    stats = generate_user_guides_jsonl(
        jsonl_path=args.jsonl_path,
        output_path=args.output_path,
        guide_func=guide_func,
        input_root=args.input_root,
        guide_field=args.guide_field,
        overwrite=args.overwrite,
        limit=args.limit,
        max_workers=args.max_workers,
        backup=not args.no_backup,
        checkpoint_interval=args.checkpoint_interval,
    )
    print(
        "Done: "
        f"processed={stats['processed']}, "
        f"skipped={stats['skipped']}, "
        f"failed={stats['failed']}"
    )


if __name__ == "__main__":
    main()
