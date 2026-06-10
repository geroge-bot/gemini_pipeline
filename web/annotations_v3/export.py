from __future__ import annotations

import json
from typing import Any

from web.annotations_v3 import datasets, records


def export_rows(dataset_id: str, include_invalidated: bool = False) -> list[dict[str, Any]]:
    ranks = datasets.item_rank_map(dataset_id)
    records_doc = records.load_records(dataset_id)
    rows = []
    for item in sorted(datasets.load_items(dataset_id), key=lambda value: ranks[value["item_id"]]):
        item_id = item["item_id"]
        item_records = records_doc.get(item_id, {})
        filtered_records = {}
        for key, value in item_records.items():
            if key in {"rough", "fine", "label"} and value.get("status") == "invalidated" and not include_invalidated:
                continue
            filtered_records[key] = value
        rows.append(
            {
                "item_id": item_id,
                "item_index": item["item_index"],
                "order_rank": ranks[item_id],
                "external_id": item.get("external_id"),
                "src_image": item["src_image"],
                "dst_image": item["dst_image"],
                "labels": item.get("labels", {}),
                "records": filtered_records,
                "sample": item_records.get("sample", {}),
            }
        )
    return rows


def export_jsonl(dataset_id: str, include_invalidated: bool = False) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in export_rows(dataset_id, include_invalidated))
