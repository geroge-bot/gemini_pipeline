import json
import sys
import time
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def write_source_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def make_image(path, size=(1600, 1200), color=(30, 120, 220)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path)


def make_dataset(tmp_path, monkeypatch, rows=None, dataset_id="dataset-1"):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    root_dir = tmp_path / "images"
    source_jsonl = tmp_path / f"{dataset_id}.jsonl"
    if rows is None:
        rows = [
            {
                "item_id": "sha256:item-0",
                "src_image": "ori/0.png",
                "dst_image": "gen/0.png",
            }
        ]
    write_source_jsonl(source_jsonl, rows)
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": dataset_id,
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "root_dir": str(root_dir),
            "order": {"mode": "natural"},
        }
    )
    return dataset_doc, data_dir, root_dir


def test_manifest_helpers(tmp_path, monkeypatch):
    from web.annotations_v3 import assets

    dataset_doc, data_dir, _ = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]

    assert assets.load_manifest(dataset_id) == {
        "version": 1,
        "dataset_id": dataset_id,
        "assets": {},
    }
    assert assets.asset_entry_for_item(dataset_id, "sha256:item-0") == {
        "src": {"status": "missing"},
        "dst": {"status": "missing"},
    }

    manifest = {
        "version": 1,
        "dataset_id": dataset_id,
        "assets": {"sha256:item-0": {"src": {"status": "ready"}, "dst": {"status": "missing"}}},
    }
    assets.save_manifest(dataset_id, manifest)

    assert read_json(data_dir / "datasets" / dataset_id / "preview_cache" / "manifest.json") == manifest
    assert assets.manifest_for_items(dataset_id, ["sha256:item-0", "sha256:item-1"]) == {
        "version": 1,
        "dataset_id": dataset_id,
        "assets": {
            "sha256:item-0": {"src": {"status": "ready"}, "dst": {"status": "missing"}},
            "sha256:item-1": {"src": {"status": "missing"}, "dst": {"status": "missing"}},
        },
    }


def test_generate_item_assets_creates_derivatives_reuses_and_records_missing_sides(tmp_path, monkeypatch):
    from web.annotations_v3 import assets

    dataset_doc, data_dir, root_dir = make_dataset(
        tmp_path,
        monkeypatch,
        rows=[
            {"item_id": "sha256:item-0", "src_image": "ori/0.png", "dst_image": "gen/0.png"},
            {"item_id": "sha256:item-1", "src_image": "ori/1.png", "dst_image": "gen/missing.png"},
        ],
    )
    dataset_id = dataset_doc["dataset_id"]
    make_image(root_dir / "ori" / "0.png", size=(1800, 900), color=(20, 120, 220))
    make_image(root_dir / "gen" / "0.png", size=(640, 480), color=(220, 120, 20))
    make_image(root_dir / "ori" / "1.png", size=(500, 300), color=(20, 220, 120))

    entry = assets.generate_item_assets(dataset_id, "sha256:item-0")

    for side in ("src", "dst"):
        assert entry[side]["status"] == "ready"
        assert entry[side]["source_hash"]
        for spec, max_edge in assets.SPECS.items():
            derivative = entry[side][spec]
            assert max(derivative["width"], derivative["height"]) <= max_edge
            assert derivative["bytes"] > 0
            assert derivative["etag"].startswith("sha256:")
            assert derivative["url"].startswith(f"/api/datasets/{dataset_id}/assets/")
            asset_id = derivative["url"].rsplit("/", 1)[-1]
            assert asset_id.endswith(".webp")
            assert asset_id.split("_")[-1].removesuffix(".webp") in derivative["etag"]
            assert (data_dir / "datasets" / dataset_id / "preview_cache" / asset_id).exists()

    repeated = assets.generate_item_assets(dataset_id, "sha256:item-0")
    assert repeated == entry

    original_src_thumb_url = entry["src"]["thumb"]["url"]
    make_image(root_dir / "ori" / "0.png", size=(1800, 900), color=(200, 30, 90))
    regenerated = assets.generate_item_assets(dataset_id, "sha256:item-0")
    assert regenerated["src"]["thumb"]["url"] != original_src_thumb_url
    assert regenerated["dst"] == entry["dst"]

    missing = assets.generate_item_assets(dataset_id, "sha256:item-1")
    assert missing["src"]["status"] == "ready"
    assert missing["dst"]["status"] == "error"
    assert "image not found" in missing["dst"]["error"]


def wait_for_job(client, dataset_id, job_id):
    for _ in range(50):
        response = client.get(f"/api/datasets/{dataset_id}/assets/jobs/{job_id}")
        assert response.status_code == 200
        job = response.get_json()
        if job["status"] == "completed":
            return job
        time.sleep(0.02)
    raise AssertionError("asset job did not complete")


def test_asset_job_and_manifest_api_warms_assignments_in_order(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments
    from web.annotations_v3.app import create_app

    rows = [
        {"item_id": "sha256:item-0", "src_image": "ori/0.png", "dst_image": "gen/0.png"},
        {"item_id": "sha256:item-1", "src_image": "ori/1.png", "dst_image": "gen/1.png"},
    ]
    dataset_doc, data_dir, root_dir = make_dataset(tmp_path, monkeypatch, rows=rows)
    dataset_id = dataset_doc["dataset_id"]
    make_image(root_dir / "ori" / "0.png", size=(800, 600), color=(20, 120, 220))
    make_image(root_dir / "gen" / "0.png", size=(800, 600), color=(220, 120, 20))
    make_image(root_dir / "ori" / "1.png", size=(640, 480), color=(20, 220, 120))
    make_image(root_dir / "gen" / "1.png", size=(640, 480), color=(120, 20, 220))
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    job_response = client.post(f"/api/datasets/{dataset_id}/assets/jobs", json={"item_ids": ["sha256:item-1"]})
    assert job_response.status_code == 200
    job = job_response.get_json()
    assert job["status"] in {"running", "completed"}
    assert job["total"] == 1
    assert job["completed"] == 0
    assert job["failed"] == 0
    completed = wait_for_job(client, dataset_id, job["job_id"])
    assert completed["status"] == "completed"
    assert completed["completed"] == 1
    assert completed["failed"] == 0
    assert read_json(data_dir / "datasets" / dataset_id / "preview_cache" / "jobs" / f"{job['job_id']}.json") == completed

    manifest_response = client.get(f"/api/datasets/{dataset_id}/assets/manifest?item_ids=sha256:item-1")
    assert manifest_response.status_code == 200
    assert list(manifest_response.get_json()["assets"]) == ["sha256:item-1"]

    claim = assignments.claim_assignment(dataset_id, "rough", "alice")
    item_one = next(item for item in claim["items"] if item["item_id"] == "sha256:item-1")
    assert item_one["image_assets"]["src"]["status"] == "ready"
    assert item_one["image_assets"]["dst"]["preview"]["url"].startswith(f"/api/datasets/{dataset_id}/assets/")
    item_zero = next(item for item in claim["items"] if item["item_id"] == "sha256:item-0")
    assert item_zero["image_assets"] == {"src": {"status": "missing"}, "dst": {"status": "missing"}}

    full_job_response = client.post(f"/api/datasets/{dataset_id}/assets/jobs", json={})
    full_job = wait_for_job(client, dataset_id, full_job_response.get_json()["job_id"])
    assert full_job["item_ids"] == ["sha256:item-0", "sha256:item-1"]
    assert full_job["completed"] == 2


def test_asset_serving_uses_immutable_cache_etag_and_range(tmp_path, monkeypatch):
    from web.annotations_v3 import assets
    from web.annotations_v3.app import create_app

    dataset_doc, _, root_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    make_image(root_dir / "ori" / "0.png", size=(800, 600), color=(20, 120, 220))
    make_image(root_dir / "gen" / "0.png", size=(800, 600), color=(220, 120, 20))
    entry = assets.generate_item_assets(dataset_id, "sha256:item-0")
    asset_id = entry["src"]["preview"]["url"].rsplit("/", 1)[-1]
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get(f"/api/datasets/{dataset_id}/assets/{asset_id}")
    assert response.status_code == 200
    assert response.data
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert response.headers["ETag"] == entry["src"]["preview"]["etag"]

    not_modified = client.get(
        f"/api/datasets/{dataset_id}/assets/{asset_id}",
        headers={"If-None-Match": response.headers["ETag"]},
    )
    assert not_modified.status_code == 304
    assert not_modified.headers["ETag"] == response.headers["ETag"]

    ranged = client.get(f"/api/datasets/{dataset_id}/assets/{asset_id}", headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206
    assert len(ranged.data) == 10
    assert ranged.headers["Content-Range"].startswith("bytes 0-9/")

    missing = client.get(f"/api/datasets/{dataset_id}/assets/missing.webp")
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "asset not found"}
