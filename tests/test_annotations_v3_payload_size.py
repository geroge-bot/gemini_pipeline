import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_dataset(tmp_path, monkeypatch, count=50):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / "items.jsonl"
    write_jsonl(
        source_jsonl,
        [
            {
                "item_id": f"sha256:item-{index}",
                "src_image": f"ori/{index}.png",
                "dst_image": f"gen/{index}.png",
                "labels": {"输入图": {"菜品种类": "中餐", "标签": ["热菜", "主食"]}},
            }
            for index in range(count)
        ],
    )
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": "dataset-1",
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "order": {"mode": "natural"},
        }
    )
    fields = [
        {
            "field_id": f"labels.输入图.field_{index}",
            "path": ["labels", "输入图", f"field_{index}"],
            "kind": "text",
        }
        for index in range(20)
    ]
    write_json(data_dir / "datasets" / dataset_doc["dataset_id"] / "labels_schema_snapshot.json", {"version": 1, "fields": fields})
    assets = {
        f"sha256:item-{index}": {
            "src": {"status": "ready", "preview": {"url": f"/asset/src/{index}", "width": 800, "height": 600}},
            "dst": {"status": "ready", "preview": {"url": f"/asset/dst/{index}", "width": 800, "height": 600}},
        }
        for index in range(count)
    }
    write_json(
        data_dir / "datasets" / dataset_doc["dataset_id"] / "preview_cache" / "manifest.json",
        {"version": 1, "dataset_id": dataset_doc["dataset_id"], "assets": assets},
    )
    return dataset_doc


def test_assignment_payload_size_budgets(tmp_path, monkeypatch):
    from web.annotations_v3.app import create_app

    dataset_doc = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    client = create_app().test_client()

    claim_response = client.post(f"/api/datasets/{dataset_id}/assignments/claim", json={"stage": "rough", "username": "alice"})
    assert claim_response.status_code == 200
    assert len(claim_response.data) < 300_000
    claim = claim_response.get_json()
    assert max(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in claim["items"]) < 15_000

    assignment_id = claim["assignment"]["assignment_id"]
    items_response = client.get(f"/api/datasets/{dataset_id}/assignments/{assignment_id}/items")
    assert items_response.status_code == 200
    assert len(items_response.data) < 300_000

    manifest_response = client.get(f"/api/datasets/{dataset_id}/assets/manifest?item_ids=sha256:item-1")
    assert manifest_response.status_code == 200
    manifest = manifest_response.get_json()
    assert list(manifest["assets"]) == ["sha256:item-1"]
