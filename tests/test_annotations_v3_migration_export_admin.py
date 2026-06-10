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


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_dataset(tmp_path, monkeypatch):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / "items.jsonl"
    write_jsonl(
        source_jsonl,
        [
            {"item_id": "sha256:item-0", "src_image": "ori/0.png", "dst_image": "gen/0.png", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"item_id": "sha256:item-1", "src_image": "ori/1.png", "dst_image": "gen/1.png", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    return datasets.create_dataset(
        {
            "dataset_id": "dataset-1",
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "order": {"mode": "natural"},
        }
    ), data_dir


def make_v2_task(tmp_path):
    task_dir = tmp_path / "v2-task"
    write_json(
        task_dir / "items.json",
        [
            {
                "external_id": "legacy-0",
                "original_image": "ori/0.png",
                "generated_image": "gen/0.png",
                "object_labels": {"输入图": {"菜品种类": "中餐"}},
                "prompt": "make food",
                "prompt_path": "prompts/0.txt",
            },
            {
                "external_id": "legacy-1",
                "src_image": "ori/1.png",
                "dst_image": "gen/1.png",
                "labels": {"输入图": {"菜品种类": "西餐"}},
            },
        ],
    )
    write_json(
        task_dir / "records.json",
        {
            "0": {
                "rough": {"username": "rougher", "values": {"quality": {"mos": 4, "has_issue": False}}},
                "fine": {"values": {"quality": {"mos": 5, "has_issue": False}}},
                "rough_annotations": [{"username": "a", "value": 4}],
                "fine_annotations": [{"username": "b", "value": 5}],
                "sampled": True,
                "sample_bucket": "bucket-a",
                "corrected_labels": {"输入图": {"菜品种类": "中餐"}},
                "username": "labeler",
            }
        },
    )
    write_json(
        task_dir / "label_schema.json",
        {"fields": [{"path": ["labels", "输入图", "菜品种类"], "kind": "single_select", "options": ["中餐", "西餐"]}]},
    )
    return task_dir


def test_migrate_v2_items(tmp_path, monkeypatch):
    from web.annotations_v3 import datasets, migration, storage

    monkeypatch.setenv(storage.DATA_DIR_ENV, str(tmp_path / "v3-data"))
    report = migration.migrate_v2_task(str(make_v2_task(tmp_path)), name="migrated")
    dataset_id = report["dataset_id"]
    items = datasets.load_items(dataset_id)

    assert report["status"] == "completed"
    assert report["migrated_items"] == 2
    assert len(items) == 2
    assert items[0]["item_index"] == 0
    assert items[0]["src_image"] == "ori/0.png"
    assert items[0]["dst_image"] == "gen/0.png"
    assert items[0]["labels"] == {"输入图": {"菜品种类": "中餐"}}
    assert items[0]["prompt"] == "make food"
    assert items[0]["prompt_path"] == "prompts/0.txt"
    assert datasets.get_dataset(dataset_id)["order"]["mode"] == "natural"


def test_migrate_v2_records(tmp_path, monkeypatch):
    from web.annotations_v3 import migration, storage

    monkeypatch.setenv(storage.DATA_DIR_ENV, str(tmp_path / "v3-data"))
    report = migration.migrate_v2_task(str(make_v2_task(tmp_path)), name="migrated")
    dataset_id = report["dataset_id"]
    ds_dir = storage.dataset_dir(dataset_id)
    records_doc = read_json(ds_dir / "records.json")
    first = next(iter(records_doc.values()))

    assert report["migrated_records"] == 1
    assert first["rough"]["status"] == "effective"
    assert first["rough"]["username"] == "rougher"
    assert first["fine"]["username"] == "migration"
    assert first["legacy_annotations"]["rough"][0]["username"] == "a"
    assert first["sample"]["sample_version"] == 1
    assert first["sample"]["sampled"] is True
    assert first["label"]["status"] == "effective"
    assert first["label"]["username"] == "labeler"
    assignments_doc = read_json(ds_dir / "assignments.json")
    assert assignments_doc["blocks"][0]["assignment_id"] == "migration"
    assert assignments_doc["blocks"][0]["audit_only"] is True
    schema_doc = read_json(ds_dir / "labels_schema_snapshot.json")
    assert schema_doc["source"] == "migration_v2"
    assert read_json(ds_dir / "migration_report.json")["dataset_id"] == dataset_id


def test_export_jsonl(tmp_path, monkeypatch):
    from web.annotations_v3 import export
    from web.annotations_v3.app import create_app

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    write_json(
        data_dir / "datasets" / dataset_id / "records.json",
        {
            "sha256:item-0": {
                "rough": {"status": "effective", "values": {"quality": {"mos": 5}}},
                "fine": {"status": "invalidated", "values": {"quality": {"mos": 1}}},
                "sample": {"sample_version": 1, "sampled": True},
            }
        },
    )

    rows = [json.loads(line) for line in export.export_jsonl(dataset_id).splitlines()]
    assert [row["order_rank"] for row in rows] == [0, 1]
    assert rows[0]["item_id"] == "sha256:item-0"
    assert rows[0]["labels"] == {"输入图": {"菜品种类": "中餐"}}
    assert "rough" in rows[0]["records"]
    assert "fine" not in rows[0]["records"]
    assert rows[0]["sample"] == {"sample_version": 1, "sampled": True}

    with_invalidated = [json.loads(line) for line in export.export_jsonl(dataset_id, include_invalidated=True).splitlines()]
    assert "fine" in with_invalidated[0]["records"]

    response = create_app().test_client().get(f"/api/datasets/{dataset_id}/download?include_invalidated=true")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/x-ndjson")
    assert response.headers["Content-Disposition"] == f"attachment; filename={dataset_id}.jsonl"


def test_admin_invalidate_and_refresh_snapshot(tmp_path, monkeypatch):
    from web.annotations_v3 import admin, assignments, records

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    records_path = data_dir / "datasets" / dataset_id / "records.json"
    write_json(
        records_path,
        {
            "sha256:item-0": {
                "rough": {
                    "record_id": "rough-record",
                    "assignment_id": "old-assignment",
                    "username": "alice",
                    "values": {"quality": {"mos": 3, "has_issue": False, "issue_tags": []}},
                    "version": "sha256:old",
                    "status": "effective",
                }
            }
        },
    )

    invalidated = admin.invalidate_record(dataset_id, "sha256:item-0", "rough", "admin", "bad value")
    assert invalidated["status"] == "invalidated"
    assert invalidated["invalidated_by"] == "admin"
    assert invalidated["invalidated_reason"] == "bad value"
    assert "invalidated_at" in invalidated

    snapshot = assignments.get_or_create_candidate_snapshot(dataset_id, "rough", force_refresh=True)
    assignment = {
        "assignment_id": "new-assignment",
        "dataset_id": dataset_id,
        "stage": "rough",
        "candidate_snapshot_id": snapshot["snapshot_id"],
        "candidate_hash": snapshot["candidate_hash"],
        "item_ids": ["sha256:item-0"],
        "username": "bob",
        "status": "claimed",
        "claimed_at": time.time(),
        "expires_at": time.time() + 3600,
        "completed_count": 0,
        "total_count": 1,
    }
    write_json(data_dir / "datasets" / dataset_id / "assignments.json", {"version": 1, "blocks": [assignment]})
    saved = records.save_annotation_patch(
        dataset_id,
        "sha256:item-0",
        {
            "assignment_id": "new-assignment",
            "stage": "rough",
            "username": "bob",
            "base_version": "sha256:old",
            "changes": [
                {"op": "set", "path": ["quality", "mos"], "value": 5},
                {"op": "set", "path": ["quality", "has_issue"], "value": False},
                {"op": "set", "path": ["quality", "issue_tags"], "value": []},
            ],
        },
    )
    assert saved["record"]["status"] == "effective"

    write_json(
        data_dir / "datasets" / dataset_id / "assignments.json",
        {
            "version": 1,
            "blocks": [
                {
                    "assignment_id": "active",
                    "dataset_id": dataset_id,
                    "stage": "rough",
                    "status": "claimed",
                    "expires_at": time.time() + 3600,
                    "item_ids": ["sha256:item-1"],
                }
            ],
        },
    )
    try:
        admin.refresh_candidate_snapshot(dataset_id, "rough")
    except admin.AdminError as exc:
        assert exc.code == "ACTIVE_ASSIGNMENT_EXISTS"
    else:
        raise AssertionError("active assignment should block refresh")

    write_json(data_dir / "datasets" / dataset_id / "assignments.json", {"version": 1, "blocks": []})
    refreshed = admin.refresh_candidate_snapshot(dataset_id, "rough")
    assert refreshed["snapshot_id"].startswith("rough-snap-")
