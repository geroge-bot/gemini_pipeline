import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import random
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import DEFAULT_MODEL_ANALYSIS, DEFAULT_SERVICE_TEXT
from pipeline.modules.description import DescriptionModule, _strip_images_from_messages
from pipeline.utils.api_usage_logger import log_result_saved
from pipeline.utils.client_factory import create_client_from_service
from pipeline.utils.file_ops import image_to_base64


DEFAULT_MAX_WORKERS = 50
PairDescriber = Callable[[Path, Path], dict]


def _path_from_record(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def _resolve_input_path(input_root: Path, value: str | Path) -> Path:
    path = _path_from_record(value)
    if path.is_absolute():
        return path
    return input_root / path


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


def _last_call_id_from_callable(func: PairDescriber) -> str | None:
    client = getattr(func, "api_client", None)
    return getattr(client, "last_call_id", None)


def describe_pair_with_module(
    original_path: str | Path,
    generated_path: str | Path,
    *,
    client,
    model: str,
) -> dict:
    module = DescriptionModule(model=model)
    orig_b64 = image_to_base64(str(original_path))
    gen_b64 = image_to_base64(str(generated_path))

    question, persona, step1_messages = module._generate_question(client, model, orig_b64, gen_b64)
    answer, step2_messages = module._generate_answer(
        client, model, persona, question, orig_b64, gen_b64
    )

    conversation_history = []
    if step1_messages:
        conversation_history.extend(_strip_images_from_messages(step1_messages))
        conversation_history.append({"role": "assistant", "content": question})
    conversation_history.extend(_strip_images_from_messages(step2_messages))
    conversation_history.append({"role": "assistant", "content": answer})

    return {
        "persona": persona,
        "question": question,
        "answer": answer,
        "conversation_history": conversation_history,
    }


def describe_pairs_jsonl(
    *,
    jsonl_path: str | Path,
    output_dir: str | Path,
    input_root: str | Path | None = None,
    describe_func: PairDescriber,
    overwrite: bool = False,
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
            src_image = record["src_image"]
            dst_image = record["dst_image"]
            original_path = _resolve_input_path(input_root, src_image)
            generated_path = _resolve_input_path(input_root, dst_image)
            out_json = get_output_json_path(output_dir, dst_image)

            if out_json.exists() and not overwrite:
                print(f"[{index}] [SKIP] exists: {out_json}")
                stats["skipped"] += 1
                continue

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
        line_no: int,
        src_image: str,
        dst_image: str,
        original_path: Path,
        generated_path: Path,
        out_json: Path,
    ) -> tuple[int, str, Path]:
        description = describe_func(original_path, generated_path)
        payload = {
            "src_image": src_image,
            "dst_image": dst_image,
            "original_image_path": str(original_path),
            "generated_image_path": str(generated_path),
            "description": description,
        }
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log_result_saved(
            call_id=_last_call_id_from_callable(describe_func),
            result_path=str(out_json),
            result_kind="json",
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
                line_no=line_no,
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
        description="Run DescriptionModule for image pairs listed in a jsonl file."
    )
    parser.add_argument("jsonl_path", help="Input jsonl file with src_image and dst_image fields.")
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory where label JSON files will be written with dst_image relative paths.",
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
    parser.add_argument("--model", default=DEFAULT_MODEL_ANALYSIS, help="Model for DescriptionModule.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output JSON files.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N new records.")
    parser.add_argument(
        "--max_workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Maximum number of concurrent description tasks.",
    )
    parser.add_argument(
        "--delay_seconds",
        type=float,
        default=0.0,
        help="Optional delay between API calls after the first processed pair.",
    )
    parser.add_argument("--random_seed", type=int, default=42, help="Random seed for question sampling.")
    args = parser.parse_args()

    random.seed(args.random_seed)
    client = create_client_from_service(args.service)

    def describe_func(original_path: Path, generated_path: Path) -> dict:
        return describe_pair_with_module(
            original_path,
            generated_path,
            client=client,
            model=args.model,
        )
    describe_func.api_client = client

    stats = describe_pairs_jsonl(
        jsonl_path=args.jsonl_path,
        output_dir=args.output_dir,
        input_root=args.input_root,
        describe_func=describe_func,
        overwrite=args.overwrite,
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
