from __future__ import annotations

import time
from typing import Any

from web.annotations_v3 import assignments, records, storage


class AdminError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def invalidate_record(dataset_id: str, item_id: str, stage: str, username: str, reason: str) -> dict[str, Any]:
    with storage.dataset_lock(dataset_id):
        records_doc = records.load_records(dataset_id)
        record = records_doc.get(item_id, {}).get(stage)
        if not record:
            raise FileNotFoundError(item_id)
        record["status"] = "invalidated"
        record["invalidated_at"] = time.time()
        record["invalidated_by"] = username
        record["invalidated_reason"] = reason
        records.save_records(dataset_id, records_doc)
        return record


def refresh_candidate_snapshot(dataset_id: str, stage: str) -> dict[str, Any]:
    with storage.dataset_lock(dataset_id):
        doc = assignments._assignments_doc(dataset_id)
        active = [
            block
            for block in doc.get("blocks", [])
            if block.get("stage") == stage
            and block.get("status") == "claimed"
            and block.get("expires_at", 0) > time.time()
        ]
        if active:
            raise AdminError("ACTIVE_ASSIGNMENT_EXISTS", "存在未完成 assignment，不能刷新快照")
        return assignments.get_or_create_candidate_snapshot(dataset_id, stage, force_refresh=True)
