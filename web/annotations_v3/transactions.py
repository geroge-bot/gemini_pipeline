from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from web.annotations_v3 import storage


@contextmanager
def dataset_transaction(dataset_id: str) -> Iterator["DatasetTransaction"]:
    with storage.dataset_lock(dataset_id):
        tx = DatasetTransaction(dataset_id)
        try:
            yield tx
            tx.commit()
        finally:
            tx.cleanup()


class DatasetTransaction:
    def __init__(self, dataset_id: str):
        self.dataset_id = dataset_id
        self._staged: list[tuple[Path, Path]] = []

    def stage_json(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tx")
        storage.write_json_atomic(tmp_path, data)
        self._staged.append((tmp_path, path))

    def commit(self) -> None:
        backups: list[tuple[Path, Path | None]] = []
        try:
            for _, final_path in self._staged:
                if final_path.exists():
                    backup_path = final_path.with_name(f".{final_path.name}.{uuid.uuid4().hex}.bak")
                    os.replace(final_path, backup_path)
                    backups.append((final_path, backup_path))
                else:
                    backups.append((final_path, None))
            for index, (tmp_path, final_path) in enumerate(self._staged):
                if os.environ.get("ANNOTATIONS_V3_FAIL_TX_AFTER") == str(index):
                    raise RuntimeError("injected transaction failure")
                os.replace(tmp_path, final_path)
        except Exception:
            for final_path, backup_path in backups:
                if final_path.exists():
                    final_path.unlink()
                if backup_path is not None and backup_path.exists():
                    os.replace(backup_path, final_path)
            raise
        else:
            for _, backup_path in backups:
                if backup_path is not None and backup_path.exists():
                    backup_path.unlink()

    def cleanup(self) -> None:
        for tmp_path, _ in self._staged:
            if tmp_path.exists():
                tmp_path.unlink()


def assert_dataset_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    return path
