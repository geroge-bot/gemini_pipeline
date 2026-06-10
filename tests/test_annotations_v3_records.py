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


def make_dataset(tmp_path, monkeypatch, count=3, dataset_id="dataset-1"):
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
                "labels": {"输入图": {"菜品种类": "中餐"}} if index == 0 else {},
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


def test_schema_fields(tmp_path, monkeypatch):
    from web.annotations_v3 import schema

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    write_json(
        data_dir / "datasets" / dataset_id / "labels_schema_snapshot.json",
        {
            "version": 2,
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

    quality_fields = schema.quality_fields("rough")
    assert [field["field_id"] for field in quality_fields] == [
        "quality.mos",
        "quality.has_issue",
        "quality.issue_tags",
    ]
    assert quality_fields[0]["kind"] == "score"
    assert quality_fields[0]["options"] == [1, 2, 3, 4, 5]
    assert quality_fields[2]["required_when"] == {"path": ["quality", "has_issue"], "equals": True}

    rough_fields = schema.fields_for_stage(dataset_id, "rough")
    assert rough_fields[-1]["field_id"] == "labels.输入图.菜品种类"
    assert all(field["editable"] is True for field in rough_fields)

    visualize_fields = schema.fields_for_stage(dataset_id, "visualize")
    assert all(field["readonly"] is True for field in visualize_fields)
    assert "editable" not in visualize_fields[0]


def test_patch_validation(tmp_path, monkeypatch):
    from web.annotations_v3 import schema

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    write_json(
        data_dir / "datasets" / dataset_id / "labels_schema_snapshot.json",
        {
            "version": 1,
            "fields": [
                {
                    "field_id": "labels.输入图.菜品种类",
                    "path": ["labels", "输入图", "菜品种类"],
                    "kind": "single_select",
                    "options": ["中餐", "西餐"],
                },
                {
                    "field_id": "labels.输入图.标签",
                    "path": ["labels", "输入图", "标签"],
                    "kind": "multi_select",
                    "options": ["热菜", "主食"],
                },
                {"field_id": "labels.note", "path": ["labels", "note"], "kind": "text"},
            ],
        },
    )
    fields = schema.fields_for_stage(dataset_id, "rough")

    for value in (0, 6, "5"):
        with pytest.raises(schema.ValidationError) as exc_info:
            schema.apply_changes({}, fields, [{"op": "set", "path": ["quality", "mos"], "value": value}])
        assert exc_info.value.code == "INVALID_PATCH"

    for value in ("true", 1, None):
        with pytest.raises(schema.ValidationError) as exc_info:
            schema.apply_changes({}, fields, [{"op": "set", "path": ["quality", "has_issue"], "value": value}])
        assert exc_info.value.code == "INVALID_PATCH"

    for value in (["中餐"], "未知"):
        with pytest.raises(schema.ValidationError) as exc_info:
            schema.apply_changes({}, fields, [{"op": "set", "path": ["labels", "输入图", "菜品种类"], "value": value}])
        assert exc_info.value.code == "INVALID_PATCH"

    values = schema.apply_changes(
        {},
        fields,
        [
            {"op": "set", "path": ["quality", "mos"], "value": 5},
            {"op": "set", "path": ["quality", "has_issue"], "value": False},
            {"op": "set", "path": ["quality", "issue_tags"], "value": ["主体问题"]},
            {"op": "set", "path": ["labels", "输入图", "菜品种类"], "value": "中餐"},
            {"op": "set", "path": ["labels", "输入图", "标签"], "value": ["热菜", "热菜", "主食"]},
            {"op": "set", "path": ["labels", "note"], "value": None},
        ],
    )

    assert values["quality"] == {"mos": 5, "has_issue": False, "issue_tags": []}
    assert values["labels"]["输入图"]["菜品种类"] == "中餐"
    assert values["labels"]["输入图"]["标签"] == ["热菜", "主食"]
    assert values["labels"]["note"] == ""

    with pytest.raises(schema.ValidationError) as exc_info:
        schema.apply_changes({}, fields, [{"op": "set", "path": ["labels", "missing"], "value": "x"}])
    assert exc_info.value.code == "INVALID_PATCH"

    with pytest.raises(schema.ValidationError) as exc_info:
        schema.apply_changes({}, fields, [{"op": "set", "path": ["labels", "输入图", "标签"], "value": ["未知"]}])
    assert exc_info.value.code == "INVALID_PATCH"


def test_annotation_context_includes_original_stage_results_and_user_record(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, records

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=2)
    dataset_id = dataset_doc["dataset_id"]
    write_json(
        data_dir / "datasets" / dataset_id / "labels_schema_snapshot.json",
        {
            "version": 3,
            "fields": [
                {
                    "field_id": "labels.输入图.菜品种类",
                    "path": ["labels", "输入图", "菜品种类"],
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
                    "record_id": "rough-record",
                    "assignment_id": "rough-assignment",
                    "username": "alice",
                    "values": {"quality": {"mos": 4, "has_issue": False, "issue_tags": []}, "labels": {}},
                    "version": "sha256:rough",
                    "status": "effective",
                },
                "fine": {
                    "record_id": "fine-record",
                    "assignment_id": "fine-assignment",
                    "username": "bob",
                    "values": {"quality": {"mos": 5, "has_issue": False, "issue_tags": []}, "labels": {}},
                    "version": "sha256:fine",
                    "status": "effective",
                },
            }
        },
    )

    context = records.annotation_context(dataset_id, "sha256:item-0", "fine", "bob")

    assert context["annotation_schema_version"] == 3
    assert context["values"]["original"]["labels"] == {"输入图": {"菜品种类": "中餐"}}
    assert context["values"]["stage_results"]["rough"]["version"] == "sha256:rough"
    assert context["values"]["user_record"]["record_id"] == "fine-record"
    assert context["values"]["draft"]["quality"]["mos"] == 5
    assert context["version"] == "sha256:fine"

    write_json(
        data_dir / "datasets" / dataset_id / "records.json",
        {
            "sha256:item-0": {
                "rough": {
                    "record_id": "rough-record",
                    "assignment_id": "rough-assignment",
                    "username": "alice",
                    "values": {"quality": {"mos": 4, "has_issue": False, "issue_tags": []}, "labels": {}},
                    "version": "sha256:rough",
                    "status": "effective",
                }
            }
        },
    )
    assignment_payload = assignments.claim_assignment(dataset_id, "fine", "bob")
    item_context = assignment_payload["items"][0]["annotation_context"]
    assert item_context["values"]["stage_results"]["rough"]["record_id"] == "rough-record"
    assert item_context["version"] is None


def valid_patch(assignment_id, username="alice", stage="rough", base_version=None):
    return {
        "assignment_id": assignment_id,
        "stage": stage,
        "username": username,
        "base_version": base_version,
        "changes": [
            {"op": "set", "path": ["quality", "mos"], "value": 5},
            {"op": "set", "path": ["quality", "has_issue"], "value": False},
            {"op": "set", "path": ["quality", "issue_tags"], "value": []},
        ],
        "client_timing": {"opened_at": 1.0, "saved_at": 2.0},
    }


def test_save_annotation_patch_writes_records_and_updates_assignment_progress(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, records

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=2)
    dataset_id = dataset_doc["dataset_id"]
    claim = assignments.claim_assignment(dataset_id, "rough", "alice")
    assignment_id = claim["assignment"]["assignment_id"]

    first = records.save_annotation_patch(dataset_id, "sha256:item-0", valid_patch(assignment_id))

    assert first["record"]["status"] == "effective"
    assert first["record"]["assignment_id"] == assignment_id
    assert first["record"]["username"] == "alice"
    assert first["record"]["values"]["quality"]["mos"] == 5
    assert first["record"]["version"].startswith("sha256:")
    assert first["assignment"]["completed_count"] == 1
    assert first["assignment"]["status"] == "claimed"
    assert first["annotation_context"]["version"] == first["record"]["version"]
    persisted = read_json(data_dir / "datasets" / dataset_id / "records.json")
    assert persisted["sha256:item-0"]["rough"]["record_id"] == first["record"]["record_id"]

    second = records.save_annotation_patch(dataset_id, "sha256:item-1", valid_patch(assignment_id))

    assert second["assignment"]["completed_count"] == 2
    assert second["assignment"]["total_count"] == 2
    assert second["assignment"]["status"] == "completed"


def test_save_annotation_patch_rejects_conflicts_and_invalid_assignments(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, records, storage

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=25)
    dataset_id = dataset_doc["dataset_id"]
    claim = assignments.claim_assignment(dataset_id, "rough", "alice")
    assignment_id = claim["assignment"]["assignment_id"]
    first = records.save_annotation_patch(dataset_id, "sha256:item-0", valid_patch(assignment_id))

    with pytest.raises(records.RecordServiceError) as exc_info:
        records.save_annotation_patch(dataset_id, "sha256:item-0", valid_patch(assignment_id, base_version="sha256:old"))
    assert exc_info.value.code == "VERSION_CONFLICT"
    assert exc_info.value.status_code == 409
    assert exc_info.value.latest["version"] == first["record"]["version"]

    with pytest.raises(records.RecordServiceError) as exc_info:
        records.save_annotation_patch(dataset_id, "sha256:item-24", valid_patch(assignment_id))
    assert exc_info.value.code == "ITEM_NOT_IN_ASSIGNMENT"

    with pytest.raises(records.RecordServiceError) as exc_info:
        records.save_annotation_patch(dataset_id, "sha256:item-1", valid_patch(assignment_id, username="bob"))
    assert exc_info.value.code == "ASSIGNMENT_NOT_OWNED"

    assignments_path = data_dir / "datasets" / dataset_id / "assignments.json"
    assignments_doc = read_json(assignments_path)
    assignments_doc["blocks"][0]["expires_at"] = 0
    storage.write_json_atomic(assignments_path, assignments_doc)
    with pytest.raises(records.RecordServiceError) as exc_info:
        records.save_annotation_patch(dataset_id, "sha256:item-1", valid_patch(assignment_id))
    assert exc_info.value.code == "ASSIGNMENT_EXPIRED"
    assignments_doc["blocks"][0]["expires_at"] = 99999999999
    storage.write_json_atomic(assignments_path, assignments_doc)

    assignments_doc = read_json(assignments_path)
    other_assignment = dict(assignments_doc["blocks"][0])
    other_assignment["assignment_id"] = "rough-b9999-other"
    other_assignment["username"] = "bob"
    other_assignment["item_ids"] = ["sha256:item-0"]
    assignments_doc["blocks"].append(other_assignment)
    storage.write_json_atomic(assignments_path, assignments_doc)
    with pytest.raises(records.RecordServiceError) as exc_info:
        records.save_annotation_patch(dataset_id, "sha256:item-0", valid_patch("rough-b9999-other", username="bob"))
    assert exc_info.value.code == "ITEM_ALREADY_COMPLETED"

    assignments_doc = read_json(assignments_path)
    assignments_doc["blocks"][0]["candidate_hash"] = "sha256:mismatch"
    storage.write_json_atomic(assignments_path, assignments_doc)
    with pytest.raises(records.RecordServiceError) as exc_info:
        records.save_annotation_patch(dataset_id, "sha256:item-1", valid_patch(assignment_id))
    assert exc_info.value.code == "ASSIGNMENT_SNAPSHOT_MISMATCH"


def test_save_annotation_patch_rejects_stage_gate_closed_and_tracks_skips(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments, records

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch, count=2)
    dataset_id = dataset_doc["dataset_id"]
    records_path = data_dir / "datasets" / dataset_id / "records.json"
    write_json(
        records_path,
        {
            "sha256:item-0": {"rough": {"status": "effective", "values": {"quality": {"has_issue": False}}}},
            "sha256:item-1": {"rough": {"status": "effective", "values": {"quality": {"has_issue": False}}}},
        },
    )
    claim = assignments.claim_assignment(dataset_id, "fine", "alice")
    assignment_id = claim["assignment"]["assignment_id"]
    write_json(
        records_path,
        {
            "sha256:item-0": {"rough": {"status": "effective", "values": {"quality": {"has_issue": True}}}},
            "sha256:item-1": {"rough": {"status": "effective", "values": {"quality": {"has_issue": False}}}},
        },
    )

    with pytest.raises(records.RecordServiceError) as exc_info:
        records.save_annotation_patch(
            dataset_id,
            "sha256:item-0",
            valid_patch(assignment_id, stage="fine"),
        )

    assert exc_info.value.code == "STAGE_GATE_CLOSED"
    assignments_doc = read_json(data_dir / "datasets" / dataset_id / "assignments.json")
    assert assignments_doc["blocks"][0]["skipped_item_ids"] == ["sha256:item-0"]

    saved = records.save_annotation_patch(dataset_id, "sha256:item-1", valid_patch(assignment_id, stage="fine"))
    assert saved["assignment"]["completed_count"] == 1
    assert saved["assignment"]["total_count"] == 2
    assert saved["assignment"]["status"] == "claimed"


def test_annotation_patch_api_returns_success_and_structured_errors(tmp_path, monkeypatch):
    from web.annotations_v3 import assignments
    from web.annotations_v3.app import create_app

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch, count=2)
    dataset_id = dataset_doc["dataset_id"]
    claim = assignments.claim_assignment(dataset_id, "rough", "alice")
    assignment_id = claim["assignment"]["assignment_id"]
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    invalid = client.post(
        f"/api/datasets/{dataset_id}/items/sha256:item-0/annotation-patch",
        json={
            "assignment_id": assignment_id,
            "stage": "rough",
            "username": "alice",
            "base_version": None,
            "changes": [{"op": "set", "path": ["quality", "mos"], "value": 6}],
        },
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "INVALID_PATCH"

    success = client.post(
        f"/api/datasets/{dataset_id}/items/sha256:item-0/annotation-patch",
        json=valid_patch(assignment_id),
    )
    assert success.status_code == 200
    record = success.get_json()["record"]
    assert record["status"] == "effective"

    conflict = client.post(
        f"/api/datasets/{dataset_id}/items/sha256:item-0/annotation-patch",
        json=valid_patch(assignment_id, base_version="sha256:old"),
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "VERSION_CONFLICT"
    assert conflict.get_json()["latest"]["version"] == record["version"]
