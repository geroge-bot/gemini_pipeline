import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("web/annotations_v2/data")


def iter_json_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        if expanded.is_dir():
            files.extend(sorted(candidate for candidate in expanded.rglob("*.json") if candidate.is_file()))
        elif expanded.is_file():
            files.append(expanded)
        else:
            print(f"MISSING {expanded}")
    return files


def recover_extra_data_json(text: str) -> tuple[Any, int] | None:
    decoder = json.JSONDecoder()
    try:
        data, end_index = decoder.raw_decode(text)
    except json.JSONDecodeError:
        return None
    if text[end_index:].strip():
        return data, end_index
    return None


def write_json_atomic(path: Path, data: Any) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def backup_file(path: Path, backup_dir: Path | None) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_name = f"{path.name}.{timestamp}.bak"
    if backup_dir is None:
        backup_path = path.with_name(backup_name)
    else:
        backup_path = backup_dir.expanduser() / path.resolve().anchor.strip("/").replace("/", "_") / str(path.resolve()).lstrip("/")
        backup_path = backup_path.with_name(backup_name)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return backup_path


def inspect_or_repair(path: Path, apply: bool, backup_dir: Path | None) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR read-failed {path}: {exc}")
        return 2

    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        recovered = recover_extra_data_json(text)
        if recovered is None:
            print(f"BROKEN unrecoverable {path}: {exc}")
            return 2
        data, end_index = recovered
        trailing_bytes = len(text[end_index:].encode("utf-8"))
        if not apply:
            print(f"RECOVERABLE extra-data {path}: keep first JSON value, discard trailing {trailing_bytes} bytes")
            return 1
        backup_path = backup_file(path, backup_dir)
        write_json_atomic(path, data)
        print(f"REPAIRED extra-data {path}: backup={backup_path}, discarded trailing {trailing_bytes} bytes")
        return 0
    print(f"OK {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or repair annotation v2 JSON state files damaged by trailing extra JSON data."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[DEFAULT_DATA_ROOT],
        help="JSON files or directories to scan. Defaults to web/annotations_v2/data.",
    )
    parser.add_argument("--apply", action="store_true", help="Rewrite recoverable files. Default is dry-run only.")
    parser.add_argument("--backup-dir", type=Path, help="Directory for backups when --apply is used.")
    args = parser.parse_args(argv)

    files = iter_json_files(args.paths)
    if not files:
        print("No JSON files found.")
        return 2

    worst_status = 0
    for path in files:
        status = inspect_or_repair(path, args.apply, args.backup_dir)
        worst_status = max(worst_status, status)
    return worst_status


if __name__ == "__main__":
    raise SystemExit(main())
