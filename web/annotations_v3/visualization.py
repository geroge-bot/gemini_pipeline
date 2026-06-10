from __future__ import annotations

from typing import Any

from web.annotations_v3 import assets, datasets, records


def visualization_results(dataset_id: str, stage: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page = max(int(page), 1)
    page_size = max(min(int(page_size), 200), 1)
    ranks = datasets.item_rank_map(dataset_id)
    records_doc = records.load_records(dataset_id)
    all_items = sorted(datasets.load_items(dataset_id), key=lambda item: ranks[item["item_id"]])
    start = (page - 1) * page_size
    selected = all_items[start : start + page_size]
    rows = []
    for item in selected:
        item_id = item["item_id"]
        item_records = records_doc.get(item_id, {})
        rows.append(
            {
                "item_id": item_id,
                "item_index": item["item_index"],
                "order_rank": ranks[item_id],
                "src_image": item["src_image"],
                "dst_image": item["dst_image"],
                "image_assets": assets.asset_entry_for_item(dataset_id, item_id),
                "annotation_context": records.annotation_context(dataset_id, item_id, "visualize", None),
                "sample": item_records.get("sample", {}),
                "stage_record": item_records.get(stage, {}) if stage in {"rough", "fine", "label"} else {},
            }
        )
    return {
        "stage": stage,
        "page": page,
        "page_size": page_size,
        "total": len(all_items),
        "rows": rows,
    }
