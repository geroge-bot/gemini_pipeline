import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def write_source_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def make_dataset(tmp_path, monkeypatch, count=5, dataset_id="dataset-1"):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / f"{dataset_id}.jsonl"
    write_source_jsonl(
        source_jsonl,
        [
            {
                "item_id": f"sha256:item-{index}",
                "src_image": f"ori/{index}.jpg",
                "dst_image": f"gen/{index}.jpg",
            }
            for index in range(count)
        ],
    )
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": dataset_id,
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "order": {"mode": "natural"},
        }
    )
    return dataset_doc, data_dir


def test_dataset_lock_and_item_helpers(tmp_path, monkeypatch):
    from web.annotations_v3 import datasets, storage

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch, count=3)

    lock = storage.dataset_lock(dataset_doc["dataset_id"])
    assert lock is storage.dataset_lock(dataset_doc["dataset_id"])
    assert lock is not storage.dataset_lock("other-dataset")

    items = datasets.load_items(dataset_doc["dataset_id"])
    manifest = datasets.load_order_manifest(dataset_doc["dataset_id"])

    assert [item["item_id"] for item in items] == ["sha256:item-0", "sha256:item-1", "sha256:item-2"]
    assert manifest["dataset_id"] == dataset_doc["dataset_id"]
    assert datasets.item_rank_map(dataset_doc["dataset_id"]) == {
        "sha256:item-0": 0,
        "sha256:item-1": 1,
        "sha256:item-2": 2,
    }


def test_candidate_snapshots_select_stage_candidates_and_reuse_boundaries(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, storage

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=5)
    dataset_id = dataset_doc["dataset_id"]
    records_path = data_dir / "datasets" / dataset_id / "records.json"
    write_json(
        records_path,
        {
            "sha256:item-0": {
                "rough": {"status": "effective", "values": {"quality": {"has_issue": False}}},
                "sample": {"sampled": True},
            },
            "sha256:item-1": {
                "rough": {"status": "effective", "values": {"quality": {"has_issue": True}}},
                "sample": {"sampled": False},
            },
            "sha256:item-3": {"rough": {"status": "draft", "values": {"quality": {"has_issue": False}}}},
            "sha256:item-4": {
                "rough": {"status": "effective", "values": {"quality": {"has_issue": False}}},
                "sample": {"sampled": True},
            },
        },
    )

    assert assignments.stage_candidate_item_ids(dataset_id, "rough") == [
        "sha256:item-0",
        "sha256:item-1",
        "sha256:item-2",
        "sha256:item-3",
        "sha256:item-4",
    ]
    assert assignments.stage_candidate_item_ids(dataset_id, "fine") == ["sha256:item-0", "sha256:item-4"]
    assert assignments.stage_candidate_item_ids(dataset_id, "label") == ["sha256:item-0", "sha256:item-4"]

    snapshot = assignments.get_or_create_candidate_snapshot(dataset_id, "rough", block_size=2)
    assert snapshot["snapshot_id"] == "rough-snap-0001"
    assert snapshot["dataset_id"] == dataset_id
    assert snapshot["stage"] == "rough"
    assert snapshot["order_version"] == 1
    assert snapshot["candidate_hash"] == assignments.candidate_hash(snapshot["item_ids"])
    assert isinstance(snapshot["created_at"], float)
    assert snapshot["item_count"] == 5
    assert snapshot["item_ids"] == [
        "sha256:item-0",
        "sha256:item-1",
        "sha256:item-2",
        "sha256:item-3",
        "sha256:item-4",
    ]
    assert snapshot["block_size"] == 2
    assert read_json(data_dir / "datasets" / dataset_id / "candidate_snapshots.json")["snapshots"] == [snapshot]

    assert assignments.get_or_create_candidate_snapshot(dataset_id, "rough", block_size=2) == snapshot
    fine_snapshot = assignments.get_or_create_candidate_snapshot(dataset_id, "fine", block_size=2)
    assert fine_snapshot["snapshot_id"] == "fine-snap-0001"
    assert fine_snapshot["item_ids"] == ["sha256:item-0", "sha256:item-4"]

    storage.write_json_atomic(
        data_dir / "datasets" / dataset_id / "assignments.json",
        {
            "version": 1,
            "blocks": [
                {
                    "stage": "fine",
                    "candidate_snapshot_id": fine_snapshot["snapshot_id"],
                    "block_index": 0,
                    "status": "completed",
                },
            ],
        },
    )
    write_json(
        records_path,
        {
            "sha256:item-0": {"rough": {"status": "effective", "values": {"quality": {"has_issue": False}}}},
            "sha256:item-1": {"rough": {"status": "effective", "values": {"quality": {"has_issue": False}}}},
        },
    )

    refreshed = assignments.get_or_create_candidate_snapshot(dataset_id, "fine", block_size=2)
    assert refreshed["snapshot_id"] == "fine-snap-0002"
    assert refreshed["item_ids"] == ["sha256:item-0", "sha256:item-1"]
    assert assignments.get_or_create_candidate_snapshot(dataset_id, "rough", block_size=2) == snapshot


def test_claim_assignment_blocks_reuse_active_assignments_and_advance_blocks(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch, count=45)
    dataset_id = dataset_doc["dataset_id"]

    alice = assignments.claim_assignment(dataset_id, "rough", "alice")
    alice_again = assignments.claim_assignment(dataset_id, "rough", "alice")
    bob = assignments.claim_assignment(dataset_id, "rough", "bob")
    carol = assignments.claim_assignment(dataset_id, "rough", "carol")

    assert alice_again["assignment"]["assignment_id"] == alice["assignment"]["assignment_id"]
    assert alice["assignment"]["block_index"] == 0
    assert alice["assignment"]["total_count"] == 20
    assert [item["order_rank"] for item in alice["items"]] == list(range(20))
    assert bob["assignment"]["block_index"] == 1
    assert [item["order_rank"] for item in bob["items"]] == list(range(20, 40))
    assert carol["assignment"]["block_index"] == 2
    assert carol["assignment"]["total_count"] == 5
    assert [item["order_rank"] for item in carol["items"]] == list(range(40, 45))


def test_claim_assignment_ignores_completed_active_status_and_reuses_expired_blocks(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, storage

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=25)
    dataset_id = dataset_doc["dataset_id"]
    first = assignments.claim_assignment(dataset_id, "rough", "alice")
    assignments_path = data_dir / "datasets" / dataset_id / "assignments.json"
    doc = read_json(assignments_path)
    doc["blocks"][0]["status"] = "completed"
    storage.write_json_atomic(assignments_path, doc)

    next_for_alice = assignments.claim_assignment(dataset_id, "rough", "alice")
    assert next_for_alice["assignment"]["block_index"] == 1

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=25, dataset_id="dataset-expired")
    dataset_id = dataset_doc["dataset_id"]
    expired = assignments.claim_assignment(dataset_id, "rough", "alice")
    assignments_path = data_dir / "datasets" / dataset_id / "assignments.json"
    doc = read_json(assignments_path)
    doc["blocks"][0]["expires_at"] = 0
    storage.write_json_atomic(assignments_path, doc)

    reclaimed = assignments.claim_assignment(dataset_id, "rough", "bob")
    assert reclaimed["assignment"]["block_index"] == expired["assignment"]["block_index"]
    assert reclaimed["assignment"]["assignment_id"] != expired["assignment"]["assignment_id"]


def test_claim_assignment_skips_effective_records_in_released_or_open_blocks(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, storage

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=3)
    dataset_id = dataset_doc["dataset_id"]
    first = assignments.claim_assignment(dataset_id, "rough", "alice")
    assignments_path = data_dir / "datasets" / dataset_id / "assignments.json"
    records_path = data_dir / "datasets" / dataset_id / "records.json"
    doc = read_json(assignments_path)
    doc["blocks"][0]["status"] = "released"
    storage.write_json_atomic(assignments_path, doc)
    write_json(records_path, {"sha256:item-0": {"rough": {"status": "effective"}}})

    reclaimed = assignments.claim_assignment(dataset_id, "rough", "bob")

    assert first["assignment"]["block_index"] == reclaimed["assignment"]["block_index"]
    assert [item["item_id"] for item in reclaimed["items"]] == ["sha256:item-1", "sha256:item-2"]
    assert reclaimed["assignment"]["skipped_item_ids"] == ["sha256:item-0"]
    assert reclaimed["assignment"]["skipped_items"] == [
        {"item_id": "sha256:item-0", "reason": "already_effective_record"}
    ]

    doc = read_json(assignments_path)
    for block in doc["blocks"]:
        block["status"] = "released"
    storage.write_json_atomic(assignments_path, doc)
    write_json(
        records_path,
        {
            "sha256:item-0": {"rough": {"status": "effective"}},
            "sha256:item-1": {"rough": {"status": "effective"}},
            "sha256:item-2": {"rough": {"status": "effective"}},
        },
    )

    with pytest.raises(ValueError, match="没有可领取的 assignment block"):
        assignments.claim_assignment(dataset_id, "rough", "carol")


def test_assignment_api_claim_get_items_and_release(tmp_path, monkeypatch):
    from web.annotations_v3 import storage
    from web.annotations_v3.app import create_app

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch, count=3)
    dataset_id = dataset_doc["dataset_id"]
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    claim_response = client.post(
        f"/api/datasets/{dataset_id}/assignments/claim",
        json={"stage": "rough", "username": "alice"},
    )
    assert claim_response.status_code == 200
    claim_payload = claim_response.get_json()
    assignment_id = claim_payload["assignment"]["assignment_id"]
    assert claim_payload["assignment"]["dataset_id"] == dataset_id
    assert claim_payload["assignment"]["status"] == "claimed"
    assert [item["order_rank"] for item in claim_payload["items"]] == [0, 1, 2]
    assert claim_payload["items"][0]["image_assets"] == {"src": {"status": "missing"}, "dst": {"status": "missing"}}
    assert claim_payload["items"][0]["annotation_context"]["annotation_schema_version"] == 1

    items_response = client.get(f"/api/datasets/{dataset_id}/assignments/{assignment_id}/items")
    assert items_response.status_code == 200
    assert items_response.get_json()["assignment"]["assignment_id"] == assignment_id

    release_response = client.post(
        f"/api/datasets/{dataset_id}/assignments/{assignment_id}/release",
        json={"username": "alice"},
    )
    assert release_response.status_code == 200
    assert release_response.get_json() == {"assignment_id": assignment_id, "status": "released"}
    assignments_doc = read_json(storage.dataset_dir(dataset_id) / "assignments.json")
    assert assignments_doc["blocks"][0]["status"] == "released"
    assert assignments_doc["blocks"][0]["released_by"] == "alice"


def test_assignment_api_returns_json_errors(tmp_path, monkeypatch):
    from web.annotations_v3.app import create_app

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch, count=3)
    dataset_id = dataset_doc["dataset_id"]
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    invalid_stage = client.post(
        f"/api/datasets/{dataset_id}/assignments/claim",
        json={"stage": "unknown", "username": "alice"},
    )
    assert invalid_stage.status_code == 400
    assert invalid_stage.get_json() == {"error": "stage 必须是 rough、fine 或 label"}

    unknown_items = client.get(f"/api/datasets/{dataset_id}/assignments/missing/items")
    assert unknown_items.status_code == 404
    assert unknown_items.get_json() == {"error": "assignment not found"}

    unknown_release = client.post(
        f"/api/datasets/{dataset_id}/assignments/missing/release",
        json={"username": "alice"},
    )
    assert unknown_release.status_code == 404
    assert unknown_release.get_json() == {"error": "assignment not found"}
