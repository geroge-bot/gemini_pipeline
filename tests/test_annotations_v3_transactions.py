import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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


def make_dataset(tmp_path, monkeypatch, count=3):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
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
            "order": {"mode": "natural"},
        }
    )
    return dataset_doc, data_dir


def rough_patch(assignment_id, username, base_version=None):
    return {
        "assignment_id": assignment_id,
        "stage": "rough",
        "username": username,
        "base_version": base_version,
        "changes": [
            {"op": "set", "path": ["quality", "mos"], "value": 5},
            {"op": "set", "path": ["quality", "has_issue"], "value": False},
            {"op": "set", "path": ["quality", "issue_tags"], "value": []},
        ],
    }


def test_claim_transaction_rolls_back_snapshot_and_assignment_on_injected_failure(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    snapshots_path = data_dir / "datasets" / dataset_id / "candidate_snapshots.json"
    assignments_path = data_dir / "datasets" / dataset_id / "assignments.json"
    before_snapshots = read_json(snapshots_path)
    before_assignments = read_json(assignments_path)
    monkeypatch.setenv("ANNOTATIONS_V3_FAIL_TX_AFTER", "1")

    try:
        assignments.claim_assignment(dataset_id, "rough", "alice")
    except RuntimeError as exc:
        assert "injected transaction failure" in str(exc)
    else:
        raise AssertionError("injected transaction failure should abort claim")

    assert read_json(snapshots_path) == before_snapshots
    assert read_json(assignments_path) == before_assignments


def test_save_transaction_rolls_back_records_and_assignment_on_injected_failure(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, records

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    claim = assignments.claim_assignment(dataset_id, "rough", "alice")
    records_path = data_dir / "datasets" / dataset_id / "records.json"
    assignments_path = data_dir / "datasets" / dataset_id / "assignments.json"
    before_records = read_json(records_path)
    before_assignments = read_json(assignments_path)
    monkeypatch.setenv("ANNOTATIONS_V3_FAIL_TX_AFTER", "1")

    try:
        records.save_annotation_patch(dataset_id, "sha256:item-0", rough_patch(claim["assignment"]["assignment_id"], "alice"))
    except RuntimeError as exc:
        assert "injected transaction failure" in str(exc)
    else:
        raise AssertionError("injected transaction failure should abort save")

    assert read_json(records_path) == before_records
    assert read_json(assignments_path) == before_assignments


def test_concurrent_claims_and_saves_are_serialized(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, records

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=25)
    dataset_id = dataset_doc["dataset_id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda user: assignments.claim_assignment(dataset_id, "rough", user), ["alice", "bob"]))

    assignment_ids = {claim["assignment"]["assignment_id"] for claim in claims}
    item_sets = [set(item["item_id"] for item in claim["items"]) for claim in claims]
    assert len(assignment_ids) == 2
    assert item_sets[0].isdisjoint(item_sets[1])

    snapshot = assignments.get_or_create_candidate_snapshot(dataset_id, "rough", force_refresh=True)
    now = time.time()
    write_json(
        data_dir / "datasets" / dataset_id / "assignments.json",
        {
            "version": 1,
            "blocks": [
                {
                    "assignment_id": "race-a",
                    "dataset_id": dataset_id,
                    "stage": "rough",
                    "candidate_snapshot_id": snapshot["snapshot_id"],
                    "candidate_hash": snapshot["candidate_hash"],
                    "item_ids": ["sha256:item-0"],
                    "username": "alice",
                    "status": "claimed",
                    "claimed_at": now,
                    "expires_at": now + 3600,
                    "completed_count": 0,
                    "total_count": 1,
                },
                {
                    "assignment_id": "race-b",
                    "dataset_id": dataset_id,
                    "stage": "rough",
                    "candidate_snapshot_id": snapshot["snapshot_id"],
                    "candidate_hash": snapshot["candidate_hash"],
                    "item_ids": ["sha256:item-0"],
                    "username": "bob",
                    "status": "claimed",
                    "claimed_at": now,
                    "expires_at": now + 3600,
                    "completed_count": 0,
                    "total_count": 1,
                },
            ],
        },
    )

    def save(args):
        assignment_id, username = args
        try:
            records.save_annotation_patch(dataset_id, "sha256:item-0", rough_patch(assignment_id, username))
            return "saved"
        except records.RecordServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, [("race-a", "alice"), ("race-b", "bob")]))

    assert sorted(results) == ["ITEM_ALREADY_COMPLETED", "saved"]
    records_doc = read_json(data_dir / "datasets" / dataset_id / "records.json")
    assert records_doc["sha256:item-0"]["rough"]["status"] == "effective"
