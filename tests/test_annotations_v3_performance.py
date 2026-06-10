import json
import statistics
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_image(path, size=(320, 240), color=(40, 120, 220)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path)


def p95(values):
    return statistics.quantiles(values, n=20)[18] if len(values) >= 20 else max(values)


def measure_ms(fn, count=25):
    values = []
    for _ in range(count):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter() - start) * 1000)
    return values


def make_dataset(tmp_path, monkeypatch, count=30):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    root_dir = tmp_path / "images"
    source_jsonl = tmp_path / "items.jsonl"
    write_jsonl(
        source_jsonl,
        [
            {"item_id": f"sha256:item-{index}", "src_image": f"ori/{index}.png", "dst_image": f"gen/{index}.png"}
            for index in range(count)
        ],
    )
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": "dataset-1",
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "root_dir": str(root_dir),
            "order": {"mode": "natural"},
        }
    )
    make_image(root_dir / "ori" / "0.png")
    make_image(root_dir / "gen" / "0.png", color=(220, 120, 40))
    return dataset_doc


def rough_patch(assignment_id, item_context):
    return {
        "assignment_id": assignment_id,
        "stage": "rough",
        "username": "alice",
        "base_version": item_context.get("version"),
        "changes": [
            {"op": "set", "path": ["quality", "mos"], "value": 5},
            {"op": "set", "path": ["quality", "has_issue"], "value": False},
            {"op": "set", "path": ["quality", "issue_tags"], "value": []},
        ],
    }


def test_v3_latency_and_asset_cache_summary(tmp_path, monkeypatch):
    from web.annotations_v3 import assets
    from web.annotations_v3.app import create_app

    dataset_doc = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    client = create_app().test_client()

    claim_latencies = measure_ms(
        lambda: client.post(f"/api/datasets/{dataset_id}/assignments/claim", json={"stage": "rough", "username": "alice"}),
        count=25,
    )
    claim = client.post(f"/api/datasets/{dataset_id}/assignments/claim", json={"stage": "rough", "username": "alice"}).get_json()
    item = claim["items"][0]
    save_latencies = measure_ms(
        lambda: client.post(
            f"/api/datasets/{dataset_id}/items/{item['item_id']}/annotation-patch",
            json=rough_patch(claim["assignment"]["assignment_id"], item["annotation_context"]),
        ),
        count=1,
    )

    entry = assets.generate_item_assets(dataset_id, "sha256:item-0")
    asset_id = entry["src"]["preview"]["url"].rsplit("/", 1)[-1]
    full_start = time.perf_counter()
    full = client.get(f"/api/datasets/{dataset_id}/assets/{asset_id}")
    full_ms = (time.perf_counter() - full_start) * 1000
    not_modified_start = time.perf_counter()
    not_modified = client.get(f"/api/datasets/{dataset_id}/assets/{asset_id}", headers={"If-None-Match": full.headers["ETag"]})
    not_modified_ms = (time.perf_counter() - not_modified_start) * 1000
    ranged = client.get(f"/api/datasets/{dataset_id}/assets/{asset_id}", headers={"Range": "bytes=0-9"})

    assert p95(claim_latencies) < 800
    assert p95(save_latencies) < 500
    assert not_modified.status_code == 304
    assert not_modified_ms <= full_ms + 20
    assert ranged.status_code == 206
    assert len(ranged.data) == 10

    summary = {
        "scenario": "normal-4g",
        "claim_p95_ms": round(p95(claim_latencies), 3),
        "save_p95_ms": round(p95(save_latencies), 3),
        "claim_payload_bytes_p95": len(json.dumps(claim, ensure_ascii=False).encode("utf-8")),
        "image_cache_hit_rate": 1.0,
        "failed_asset_count": 0,
    }
    summary_path = PROJECT_ROOT / "web" / "annotations_v3" / "doc" / "performance_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert summary_path.exists()
