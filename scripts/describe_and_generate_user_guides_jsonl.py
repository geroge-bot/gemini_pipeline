import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import DEFAULT_MODEL_ANALYSIS, DEFAULT_SERVICE_TEXT  # noqa: E402
from pipeline.modules.description import (  # noqa: E402
    ANSWER_PROMPTS,
    DEFAULT_ANSWER_PROMPT_KEY,
)
from pipeline.modules.user_guide_generator import (  # noqa: E402
    UserGuideGeneratorModule,
    has_compose_user_guide,
)
from pipeline.utils.api_usage_logger import log_result_saved  # noqa: E402
from pipeline.utils.client_factory import create_client_from_service  # noqa: E402
from scripts.describe_pairs_jsonl import (  # noqa: E402
    DEFAULT_MAX_WORKERS,
    describe_pair_with_module,
    get_output_json_path,
    iter_jsonl_records,
)


PairDescriber = Callable[[Path, Path], dict]
PairUserGuideGenerator = Callable[[Path, Path], Any]


def _path_from_record(value: str | Path) -> Path:
    if isinstance(value, Path):
        return value
    return Path(value.replace("\\", "/"))


def _get_pair_fields(record: dict) -> tuple[str, str]:
    original = record.get("src_image") or record.get("original_image_path")
    generated = record.get("dst_image") or record.get("generated_image_path")
    if not original:
        raise ValueError(
            "Missing source image field: expected src_image or original_image_path"
        )
    if not generated:
        raise ValueError(
            "Missing target image field: expected dst_image or generated_image_path"
        )
    return original, generated


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


def _load_existing_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Existing JSON is not an object: {path}")
    return data


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _last_call_id_from_callable(func: Callable[[Path, Path], Any]) -> str | None:
    client = getattr(func, "api_client", None)
    return getattr(client, "last_call_id", None)


def generate_user_guide_with_module(
    original_path: str | Path,
    generated_path: str | Path,
    *,
    client,
    model: str,
) -> list[dict[str, Any]]:
    module = UserGuideGeneratorModule(model=model)
    return module._generate_user_guide(
        client=client,
        model=model,
        original_image_path=str(original_path),
        generated_image_path=str(generated_path),
    )


def describe_and_generate_user_guides_jsonl(
    *,
    jsonl_path: str | Path,
    output_dir: str | Path,
    describe_func: PairDescriber,
    user_guide_func: PairUserGuideGenerator,
    input_root: str | Path | None = None,
    overwrite: bool = False,
    limit: int | None = None,
    sample_size: int | None = None,
    random_seed: int = 42,
    delay_seconds: float = 0.0,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, int]:
    """Generate description and user-guide labels for each image pair.

    Each worker processes one pair sequentially: description first, then user
    guide. Different pairs are processed concurrently. A successful description
    is checkpointed before user-guide generation so a failed second stage can be
    resumed without repeating the first API call.
    """
    jsonl_path = Path(jsonl_path).resolve()
    input_root = Path(input_root).resolve() if input_root else jsonl_path.parent
    output_dir = Path(output_dir).resolve()
    max_workers = max(1, max_workers)

    if limit is not None and sample_size is not None:
        raise ValueError("limit and sample_size cannot be used together")

    stats = {"processed": 0, "skipped": 0, "failed": 0}
    pending_records: list[
        tuple[int, int, str, str, Path, Path, Path, dict]
    ] = []

    for index, (line_no, record) in enumerate(iter_jsonl_records(jsonl_path), 1):
        try:
            src_image, dst_image = _get_pair_fields(record)
            original_path = _resolve_input_path(input_root, src_image)
            generated_path = _resolve_input_path(input_root, dst_image)
            generated_relative_path = _relative_output_path(input_root, dst_image)
            out_json = get_output_json_path(output_dir, generated_relative_path)
            payload = _load_existing_json(out_json)

            has_description = payload.get("description") is not None
            has_user_guide = has_compose_user_guide(payload.get("user_guide"))
            if not overwrite and has_description and has_user_guide:
                print(f"[{index}] [SKIP] description and user_guide exist: {out_json}")
                stats["skipped"] += 1
                continue

            pending_records.append(
                (
                    index,
                    line_no,
                    src_image,
                    dst_image,
                    original_path,
                    generated_path,
                    out_json,
                    payload,
                )
            )
            if limit is not None and len(pending_records) >= limit:
                break
        except Exception as exc:
            print(f"[{index}] [ERROR] line={line_no}: {exc}")
            stats["failed"] += 1

    if sample_size is not None:
        sample_size = max(0, sample_size)
        pending_records = random.Random(random_seed).sample(
            pending_records, min(sample_size, len(pending_records))
        )

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
        payload.update(
            {
                "src_image": src_image,
                "dst_image": dst_image,
                "original_image_path": str(original_path),
                "generated_image_path": str(generated_path),
            }
        )

        if overwrite or payload.get("description") is None:
            payload["description"] = describe_func(original_path, generated_path)
            _write_json(out_json, payload)
            log_result_saved(
                call_id=_last_call_id_from_callable(describe_func),
                result_path=str(out_json),
                result_kind="json",
            )

        if overwrite or not has_compose_user_guide(payload.get("user_guide")):
            payload["user_guide"] = user_guide_func(original_path, generated_path)
            _write_json(out_json, payload)
            log_result_saved(
                call_id=_last_call_id_from_callable(user_guide_func),
                result_path=str(out_json),
                result_kind="json",
            )

        return index, dst_image, out_json

    futures = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for submit_index, (
            index,
            line_no,
            src_image,
            dst_image,
            original_path,
            generated_path,
            out_json,
            payload,
        ) in enumerate(pending_records):
            if delay_seconds > 0 and submit_index > 0:
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
            "Run DescriptionModule followed by UserGuideGeneratorModule for "
            "image pairs listed in a jsonl file."
        )
    )
    parser.add_argument(
        "jsonl_path",
        help=(
            "Input jsonl file with src_image/dst_image or "
            "original_image_path/generated_image_path."
        ),
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory where JSON files will be written by generated-image path.",
    )
    parser.add_argument(
        "--input_root",
        default=None,
        help="Root for relative image paths. Defaults to the jsonl file directory.",
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
        "--user_guide_model",
        default=DEFAULT_MODEL_ANALYSIS,
        help="Model for UserGuideGeneratorModule.",
    )
    parser.add_argument(
        "--answer_prompt",
        choices=sorted(ANSWER_PROMPTS),
        default=DEFAULT_ANSWER_PROMPT_KEY,
        help="Named DescriptionModule answer prompt version.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate both labels even when they already exist.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most N pending pairs.")
    parser.add_argument(
        "--sample_size",
        type=int,
        default=None,
        help="Randomly sample N pending pairs before processing.",
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Maximum number of concurrently processed image pairs.",
    )
    parser.add_argument(
        "--delay_seconds",
        type=float,
        default=0.0,
        help="Optional delay between submitted pair tasks after the first.",
    )
    parser.add_argument(
        "--random_seed",
        type=int,
        default=42,
        help="Random seed for question generation and record sampling.",
    )
    args = parser.parse_args()

    random.seed(args.random_seed)
    client = create_client_from_service(args.service)

    def describe_func(original_path: Path, generated_path: Path) -> dict:
        return describe_pair_with_module(
            original_path,
            generated_path,
            client=client,
            model=args.description_model,
            answer_prompt_key=args.answer_prompt,
        )

    describe_func.api_client = client

    def user_guide_func(original_path: Path, generated_path: Path) -> list[dict[str, Any]]:
        return generate_user_guide_with_module(
            original_path,
            generated_path,
            client=client,
            model=args.user_guide_model,
        )

    user_guide_func.api_client = client

    stats = describe_and_generate_user_guides_jsonl(
        jsonl_path=args.jsonl_path,
        output_dir=args.output_dir,
        input_root=args.input_root,
        describe_func=describe_func,
        user_guide_func=user_guide_func,
        overwrite=args.overwrite,
        limit=args.limit,
        sample_size=args.sample_size,
        random_seed=args.random_seed,
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
