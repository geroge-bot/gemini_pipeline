import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import random
import sys
import time
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import DEFAULT_MODEL_ANALYSIS, DEFAULT_SERVICE_TEXT
from pipeline.modules.user_guide_generator import UserGuideGeneratorModule, has_compose_user_guide
from pipeline.utils.api_usage_logger import log_result_saved
from pipeline.utils.client_factory import create_client_from_service
from scripts.describe_pairs_jsonl import describe_pair_with_module
from scripts.label_pairs_jsonl import (
    DEFAULT_MAX_WORKERS,
    _get_pair_fields,
    _load_existing_json,
    _relative_output_path,
    _resolve_input_path,
    get_output_json_path,
    iter_jsonl_records,
    label_pair_with_module,
)


PairDescriber = Callable[[Path, Path], dict]
PairLabeler = Callable[[Path, Path], dict]
PairUserGuideGenerator = Callable[[Path, Path], dict]


def _has_non_empty_field(payload: dict, field_name: str) -> bool:
    return payload.get(field_name) is not None


def _last_call_id_from_callable(func: Callable[[Path, Path], dict]) -> str | None:
    client = getattr(func, "api_client", None)
    return getattr(client, "last_call_id", None)


def describe_and_label_pairs_jsonl(
    *,
    jsonl_path: str | Path,
    output_dir: str | Path,
    input_root: str | Path | None = None,
    describe_func: PairDescriber,
    label_func: PairLabeler,
    user_guide_func: PairUserGuideGenerator,
    limit: int | None = None,
    delay_seconds: float = 0.0,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, int]:
    jsonl_path = Path(jsonl_path).resolve()
    input_root = Path(input_root).resolve() if input_root else jsonl_path.parent
    output_dir = Path(output_dir).resolve()

    stats = {"processed": 0, "skipped": 0, "failed": 0}
    max_workers = max(1, max_workers)

    pending_records: list[tuple[int, int, str, str, Path, Path, Path, dict]] = []
    for index, (line_no, record) in enumerate(iter_jsonl_records(jsonl_path), 1):
        try:
            src_image, dst_image = _get_pair_fields(record)
            original_path = _resolve_input_path(input_root, src_image)
            generated_path = _resolve_input_path(input_root, dst_image)
            generated_relative_path = _relative_output_path(input_root, dst_image)
            out_json = get_output_json_path(output_dir, generated_relative_path)
            payload = _load_existing_json(out_json)

            has_description = _has_non_empty_field(payload, "description")
            has_labels = _has_non_empty_field(payload, "labels")
            has_user_guide = has_compose_user_guide(payload.get("user_guide"))
            if has_description and has_labels and has_user_guide:
                print(f"[{index}] [SKIP] description, labels, and user_guide exist: {out_json}")
                stats["skipped"] += 1
                continue

            pending_records.append(
                (index, line_no, src_image, dst_image, original_path, generated_path, out_json, payload)
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
        payload: dict,
    ) -> tuple[int, str, Path]:
        call_ids = []
        if not _has_non_empty_field(payload, "description"):
            payload["description"] = describe_func(original_path, generated_path)
            call_ids.append(_last_call_id_from_callable(describe_func))
        if not _has_non_empty_field(payload, "labels"):
            payload["labels"] = label_func(original_path, generated_path)
            call_ids.append(_last_call_id_from_callable(label_func))
        if not has_compose_user_guide(payload.get("user_guide")):
            payload["user_guide"] = user_guide_func(original_path, generated_path)
            call_ids.append(_last_call_id_from_callable(user_guide_func))

        payload.update(
            {
                "src_image": src_image,
                "dst_image": dst_image,
                "original_image_path": str(original_path),
                "generated_image_path": str(generated_path),
            }
        )
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for call_id in call_ids:
            log_result_saved(
                call_id=call_id,
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
            payload,
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
                payload=payload,
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
        description=(
            "Run DescriptionModule and TwoImageLabelingModule for image pairs listed "
            "in a jsonl file."
        )
    )
    parser.add_argument(
        "jsonl_path",
        help="Input jsonl file with src_image/dst_image or original_image_path/generated_image_path.",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory where JSON files will be written with generated-image relative paths.",
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
    parser.add_argument(
        "--description_model",
        default=DEFAULT_MODEL_ANALYSIS,
        help="Model for DescriptionModule.",
    )
    parser.add_argument(
        "--label_model",
        default=DEFAULT_MODEL_ANALYSIS,
        help="Model for TwoImageLabelingModule.",
    )
    parser.add_argument(
        "--user_guide_model",
        default=DEFAULT_MODEL_ANALYSIS,
        help="Model for UserGuideGeneratorModule.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most N records.")
    parser.add_argument(
        "--max_workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Maximum number of concurrent pair tasks.",
    )
    parser.add_argument(
        "--delay_seconds",
        type=float,
        default=0.0,
        help="Optional delay between submitted pair tasks after the first.",
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
            model=args.description_model,
        )
    describe_func.api_client = client

    def label_func(original_path: Path, generated_path: Path) -> dict:
        return label_pair_with_module(
            original_path,
            generated_path,
            client=client,
            model=args.label_model,
        )
    label_func.api_client = client

    def user_guide_func(original_path: Path, generated_path: Path) -> dict:
        module = UserGuideGeneratorModule(model=args.user_guide_model)
        return module._generate_user_guide(
            client=client,
            model=args.user_guide_model,
            original_image_path=str(original_path),
            generated_image_path=str(generated_path),
        )
    user_guide_func.api_client = client

    stats = describe_and_label_pairs_jsonl(
        jsonl_path=args.jsonl_path,
        output_dir=args.output_dir,
        input_root=args.input_root,
        describe_func=describe_func,
        label_func=label_func,
        user_guide_func=user_guide_func,
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
