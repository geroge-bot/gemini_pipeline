import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_source_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def sample_rows(count=4):
    categories = ["中餐", "西餐", "中餐", None]
    rows = []
    for index in range(count):
        row = {
            "item_id": f"sha256:item-{index}",
            "src_image": f"ori/{index}.jpg",
            "dst_image": f"gen/{index}.jpg",
        }
        category = categories[index % len(categories)]
        if category is not None:
            row["labels"] = {"输入图": {"菜品种类": category}}
        rows.append(row)
    return rows


def make_dataset(tmp_path, monkeypatch, rows=None, dataset_id="dataset-1", order=None):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / f"{dataset_id}.jsonl"
    write_source_jsonl(source_jsonl, rows or sample_rows())
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": dataset_id,
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "order": order or {"mode": "natural"},
        }
    )
    return dataset_doc, data_dir


def sampled_item_ids(data_dir, dataset_id):
    records_doc = read_json(data_dir / "datasets" / dataset_id / "records.json")
    return sorted(
        item_id
        for item_id, item_doc in records_doc.items()
        if item_doc.get("sample", {}).get("sampled") is True
    )


def test_sample_buckets_from_original_and_effective_values(tmp_path, monkeypatch):
    from web.annotations_v3 import sampling

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    write_json(
        data_dir / "datasets" / dataset_id / "records.json",
        {
            "sha256:item-0": {
                "rough": {
                    "status": "effective",
                    "values": {"labels": {"输入图": {"菜品种类": "粗筛中餐"}}},
                },
                "sample": {"sampled": True, "sample_version": 1},
            },
            "sha256:item-1": {
                "fine": {
                    "status": "effective",
                    "values": {"labels": {"输入图": {"菜品种类": "精筛西餐"}}},
                }
            },
            "sha256:item-2": {
                "label": {
                    "status": "effective",
                    "values": {"labels": {"输入图": {"菜品种类": ["热菜", "主食"]}}},
                }
            },
        },
    )

    result = sampling.sample_buckets(dataset_id, [["labels", "输入图", "菜品种类"]])

    assert result["selected_label_paths"] == [["labels", "输入图", "菜品种类"]]
    assert result["buckets"] == [
        {"bucket": "labels/输入图/菜品种类=__missing__", "count": 1, "sampled_count": 0},
        {"bucket": "labels/输入图/菜品种类=主食,热菜", "count": 1, "sampled_count": 0},
        {"bucket": "labels/输入图/菜品种类=粗筛中餐", "count": 1, "sampled_count": 1},
        {"bucket": "labels/输入图/菜品种类=精筛西餐", "count": 1, "sampled_count": 0},
    ]


def test_run_sample_versions_history_releases_and_carries_label_records(tmp_path, monkeypatch):
    from web.annotations_v3 import sampling

    dataset_doc, data_dir = make_dataset(
        tmp_path,
        monkeypatch,
        rows=[
            {
                "item_id": f"sha256:item-{index}",
                "src_image": f"ori/{index}.jpg",
                "dst_image": f"gen/{index}.jpg",
                "labels": {"输入图": {"菜品种类": "全部"}},
            }
            for index in range(4)
        ],
    )
    dataset_id = dataset_doc["dataset_id"]

    first = sampling.run_sample(
        dataset_id,
        username="alice",
        selected_paths=[["labels", "输入图", "菜品种类"]],
        per_bucket=2,
        seed="one",
    )
    assert first == {
        "sample_version": 1,
        "sampled_count": 2,
        "released_label_assignments": 0,
        "carried_label_records": 0,
    }
    assert sampled_item_ids(data_dir, dataset_id) == ["sha256:item-0", "sha256:item-1"]

    records_path = data_dir / "datasets" / dataset_id / "records.json"
    records_doc = read_json(records_path)
    records_doc["sha256:item-1"]["label"] = {
        "record_id": "label-record",
        "assignment_id": "label-assignment",
        "username": "reviewer",
        "values": {"quality": {}, "labels": {"输入图": {"菜品种类": "全部"}}},
        "version": "sha256:label",
        "status": "effective",
        "updated_at": time.time(),
    }
    write_json(records_path, records_doc)
    write_json(
        data_dir / "datasets" / dataset_id / "assignments.json",
        {
            "version": 1,
            "blocks": [
                {
                    "assignment_id": "label-stale",
                    "dataset_id": dataset_id,
                    "stage": "label",
                    "status": "claimed",
                    "username": "bob",
                    "item_ids": ["sha256:item-0"],
                    "total_count": 1,
                    "claimed_at": time.time(),
                    "expires_at": time.time() + 3600,
                }
            ],
        },
    )

    second = sampling.run_sample(
        dataset_id,
        username="alice",
        selected_paths=[["labels", "输入图", "菜品种类"]],
        per_bucket=2,
        seed="two",
    )

    assert second == {
        "sample_version": 2,
        "sampled_count": 2,
        "released_label_assignments": 1,
        "carried_label_records": 1,
    }
    assert sampled_item_ids(data_dir, dataset_id) == ["sha256:item-1", "sha256:item-2"]
    records_doc = read_json(records_path)
    assert records_doc["sha256:item-1"]["sample"]["sample_version"] == 2
    assert records_doc["sha256:item-1"]["sample"]["sampled"] is True
    assert records_doc["sha256:item-1"]["sample"]["sample_bucket"] == "labels/输入图/菜品种类=全部"
    assert records_doc["sha256:item-1"]["sample"]["sampled_by"] == "alice"
    assert "sampled_at" in records_doc["sha256:item-1"]["sample"]
    assert records_doc["sha256:item-1"]["label"]["carried_from_sample_version"] == 1
    assert records_doc["sha256:item-0"]["sample"]["sampled"] is False
    assert records_doc["sha256:item-0"]["sample"]["sample_version"] == 2
    assert records_doc["sha256:item-0"]["sample_history"][0]["sample_version"] == 1
    assignments_doc = read_json(data_dir / "datasets" / dataset_id / "assignments.json")
    assert assignments_doc["blocks"][0]["status"] == "released"
    assert assignments_doc["blocks"][0]["released_reason"] == "sample_version_changed"

    copy_doc, copy_data_dir = make_dataset(
        tmp_path,
        monkeypatch,
        rows=[
            {
                "item_id": f"sha256:item-{index}",
                "src_image": f"ori/{index}.jpg",
                "dst_image": f"gen/{index}.jpg",
                "labels": {"输入图": {"菜品种类": "全部"}},
            }
            for index in range(4)
        ],
        dataset_id="dataset-copy",
    )
    sampling.run_sample(
        copy_doc["dataset_id"],
        username="alice",
        selected_paths=[["labels", "输入图", "菜品种类"]],
        per_bucket=2,
        seed="one",
    )
    assert sampled_item_ids(copy_data_dir, copy_doc["dataset_id"]) == ["sha256:item-0", "sha256:item-1"]


def test_label_candidates_follow_current_sample_version(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, sampling

    dataset_doc, data_dir = make_dataset(
        tmp_path,
        monkeypatch,
        rows=[
            {
                "item_id": f"sha256:item-{index}",
                "src_image": f"ori/{index}.jpg",
                "dst_image": f"gen/{index}.jpg",
                "labels": {"输入图": {"菜品种类": "全部"}},
            }
            for index in range(4)
        ],
    )
    dataset_id = dataset_doc["dataset_id"]
    sampling.run_sample(dataset_id, "alice", [["labels", "输入图", "菜品种类"]], 2, "one")

    first = assignments.get_or_create_candidate_snapshot(dataset_id, "label", block_size=1)

    assert first["sample_version"] == 1
    assert first["item_ids"] == ["sha256:item-0", "sha256:item-1"]

    sampling.run_sample(dataset_id, "alice", [["labels", "输入图", "菜品种类"]], 2, "two")
    second = assignments.get_or_create_candidate_snapshot(dataset_id, "label", block_size=1)

    assert second["snapshot_id"] == "label-snap-0002"
    assert second["sample_version"] == 2
    assert second["item_ids"] == ["sha256:item-1", "sha256:item-2"]
    assert [snap["sample_version"] for snap in read_json(data_dir / "datasets" / dataset_id / "candidate_snapshots.json")["snapshots"]] == [1, 2]


def test_visualization_results_api_includes_ordered_assets_context_and_sample(tmp_path, monkeypatch):
    from web.annotations_v3.app import create_app

    dataset_doc, data_dir = make_dataset(
        tmp_path,
        monkeypatch,
        rows=sample_rows(3),
        order={"mode": "shuffled", "seed": "visual-order"},
    )
    dataset_id = dataset_doc["dataset_id"]
    write_json(
        data_dir / "datasets" / dataset_id / "labels_schema_snapshot.json",
        {
            "version": 3,
            "fields": [
                {
                    "field_id": "labels.输入图.菜品种类",
                    "path": ["labels", "输入图", "菜品种类"],
                    "label": "菜品种类",
                    "kind": "single_select",
                    "options": ["中餐", "西餐"],
                }
            ],
        },
    )
    write_json(
        data_dir / "datasets" / dataset_id / "records.json",
        {
            "sha256:item-0": {
                "rough": {
                    "status": "effective",
                    "values": {"quality": {"mos": 5, "has_issue": False}, "labels": {}},
                    "version": "sha256:rough",
                },
                "sample": {"sample_version": 1, "sampled": True, "sample_bucket": "bucket-a"},
            },
            "sha256:item-1": {"sample": {"sample_version": 1, "sampled": False, "sample_bucket": None}},
            "sha256:item-2": {"sample": {"sample_version": 1, "sampled": True, "sample_bucket": "bucket-a"}},
        },
    )
    write_json(
        data_dir / "datasets" / dataset_id / "preview_cache" / "manifest.json",
        {
            "version": 1,
            "dataset_id": dataset_id,
            "assets": {
                "sha256:item-0": {"src": {"status": "ready"}, "dst": {"status": "missing"}},
            },
        },
    )
    client = create_app().test_client()

    response = client.get(f"/api/datasets/{dataset_id}/visualization-results?stage=sample&page=1&page_size=2")

    assert response.status_code == 200
    body = response.get_json()
    assert body["stage"] == "sample"
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] == 3
    assert len(body["rows"]) == 2
    assert [row["order_rank"] for row in body["rows"]] == sorted(row["order_rank"] for row in body["rows"])
    first_item_id = body["rows"][0]["item_id"]
    if first_item_id == "sha256:item-0":
        assert body["rows"][0]["image_assets"]["src"]["status"] == "ready"
    assert "fields" in body["rows"][0]["annotation_context"]
    assert all(field["readonly"] is True for field in body["rows"][0]["annotation_context"]["fields"])
    assert "sample" in body["rows"][0]

    second_page = client.get(f"/api/datasets/{dataset_id}/visualization-results?stage=sample&page=2&page_size=2")
    assert second_page.status_code == 200
    assert len(second_page.get_json()["rows"]) == 1


def test_sampling_and_visualization_pages_and_static_modules(tmp_path, monkeypatch):
    from web.annotations_v3.app import create_app

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    client = create_app().test_client()

    sample_page = client.get(f"/datasets/{dataset_id}/sample")
    assert sample_page.status_code == 200
    assert b'data-page="sample"' in sample_page.data
    assert b'id="bucketRows"' in sample_page.data

    visualize_page = client.get(f"/datasets/{dataset_id}/visualize?stage=rough")
    assert visualize_page.status_code == 200
    assert b'data-page="visualize"' in visualize_page.data
    assert b'id="resultsHost"' in visualize_page.data

    for path, expected in {
        "/static/samplePage.js": ["loadBuckets", "runSampling"],
        "/static/visualizePage.js": ["loadResults", "renderRows"],
    }.items():
        response = client.get(path)
        assert response.status_code == 200
        text = response.data.decode("utf-8")
        for name in expected:
            assert name in text
