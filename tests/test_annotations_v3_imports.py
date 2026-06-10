import json
import sys
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


def make_dataset(tmp_path, monkeypatch, rows=None):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / "items.jsonl"
    rows = rows or [
        {
            "item_id": "sha256:item-0",
            "external_id": "ext-0",
            "src_image": "ori/0.png",
            "dst_image": "gen/0.png",
            "labels": {"输入图": {"菜品种类": "中餐", "辣度": "低"}},
        },
        {
            "item_id": "sha256:item-1",
            "external_id": "ext-1",
            "src_image": "ori/1.png",
            "dst_image": "gen/1.png",
            "labels": {"输入图": {"菜品种类": "西餐"}},
        },
    ]
    write_jsonl(source_jsonl, rows)
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": "dataset-1",
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "order": {"mode": "natural"},
        }
    )
    write_json(
        data_dir / "datasets" / dataset_doc["dataset_id"] / "labels_schema_snapshot.json",
        {
            "version": 3,
            "fields": [
                {
                    "field_id": "labels.输入图.菜品种类",
                    "path": ["labels", "输入图", "菜品种类"],
                    "kind": "single_select",
                    "options": ["中餐", "西餐"],
                },
                {
                    "field_id": "labels.输入图.辣度",
                    "path": ["labels", "输入图", "辣度"],
                    "kind": "single_select",
                    "options": ["低", "中", "高"],
                },
            ],
            "path_aliases": {"legacy/category": ["输入图", "菜品种类"]},
            "value_aliases": {"输入图/菜品种类": {"cn": "中餐"}, "输入图/辣度": {"mild": "低"}},
            "unknown_policy": "reject",
        },
    )
    return dataset_doc, data_dir


def test_envelope_conversion():
    from web.annotations_v3 import imports

    envelope = {
        "format": "annotations_v3.labels",
        "version": 1,
        "match": {"item_id": "sha256:item-0"},
        "labels": {"values": [{"path": ["输入图", "菜品种类"], "value": "中餐"}]},
    }
    assert imports.normalize_envelope(envelope) == envelope

    legacy = imports.normalize_envelope(
        {
            "external_id": "ext-0",
            "labels": {"输入图": {"菜品种类": "中餐"}},
            "corrected_labels": {"输入图": {"辣度": "低"}},
            "rough_annotations": {"username": "rough-user", "values": {"quality": {"mos": 4}}},
            "fine_annotations": {"username": "fine-user", "values": {"quality": {"mos": 5}}},
        }
    )
    assert legacy["format"] == "annotations_v3.labels"
    assert legacy["match"] == {"external_id": "ext-0"}
    assert legacy["labels"]["values"] == [{"path": ["输入图", "菜品种类"], "value": "中餐"}]
    assert legacy["annotations"]["label_correction"]["values"] == [{"path": ["输入图", "辣度"], "value": "低"}]
    assert legacy["annotations"]["rough"]["username"] == "rough-user"
    assert legacy["annotations"]["fine"]["username"] == "fine-user"

    object_labels = imports.normalize_envelope({"item_id": "sha256:item-0", "object_labels": {"输入图": {"辣度": "中"}}})
    assert object_labels["labels"]["values"] == [{"path": ["输入图", "辣度"], "value": "中"}]

    try:
        imports.normalize_envelope({"item_id": "sha256:item-0"})
    except imports.ImportRowError as exc:
        assert exc.code == "EMPTY_IMPORT_ROW"
    else:
        raise AssertionError("empty import row should fail")


def test_import_matching_priority_and_unmatched_report(tmp_path, monkeypatch):
    from web.annotations_v3 import imports

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch)
    indexes = imports.build_match_indexes(dataset_doc["dataset_id"])
    assert imports.match_item_id(indexes, {"item_id": "sha256:item-0", "external_id": "ext-1"}) == "sha256:item-0"
    assert imports.match_item_id(indexes, {"external_id": "ext-1", "src_image": "ori/0.png", "dst_image": "gen/0.png"}) == "sha256:item-1"
    assert imports.match_item_id(indexes, {"src_image": "ori/0.png", "dst_image": "gen/0.png", "item_index": 1}) == "sha256:item-0"
    assert imports.match_item_id(indexes, {"item_index": 1}) == "sha256:item-1"
    assert imports.match_item_id(indexes, {"item_id": "missing"}) is None

    import_path = tmp_path / "unmatched.jsonl"
    write_jsonl(import_path, [{"item_id": "missing", "labels": {"输入图": {"菜品种类": "中餐"}}}])
    report = imports.run_import(dataset_doc["dataset_id"], str(import_path), "dry_run", "patch_labels", "audit_only")
    assert report["total_rows"] == 1
    assert report["unmatched_rows"] == 1
    assert report["errors"][0]["code"] == "ITEM_NOT_MATCHED"


def test_label_normalization(tmp_path, monkeypatch):
    from web.annotations_v3 import imports

    dataset_doc, _ = make_dataset(tmp_path, monkeypatch)
    accepted, warnings, errors = imports.normalize_label_values(
        dataset_doc["dataset_id"],
        [
            {"path": ["legacy", "category"], "value": "cn"},
            {"path": ["输入图", "辣度"], "value": "mild"},
            {"path": ["输入图", "辣度"], "value": "高"},
            {"path": ["输入图", "不存在"], "value": "x"},
        ],
    )

    assert accepted == {
        "输入图/菜品种类": {"path": ["输入图", "菜品种类"], "value": "中餐"},
        "输入图/辣度": {"path": ["输入图", "辣度"], "value": "高"},
    }
    assert [warning["code"] for warning in warnings] == [
        "VALUE_ALIAS_USED",
        "VALUE_ALIAS_USED",
        "DUPLICATE_LABEL_PATH",
    ]
    assert errors == [{"code": "UNKNOWN_LABEL_PATH", "message": "未知标签路径", "path": ["输入图", "不存在"]}]


def test_import_dry_run_and_commit_policies(tmp_path, monkeypatch):
    from web.annotations_v3 import imports

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    records_path = data_dir / "datasets" / dataset_id / "records.json"
    import_path = tmp_path / "import.jsonl"
    write_jsonl(
        import_path,
        [
            {
                "item_id": "sha256:item-0",
                "labels": {"输入图": {"菜品种类": "西餐"}},
                "rough_annotations": {"username": "importer", "values": {"quality": {"mos": 4}}},
            }
        ],
    )

    dry = imports.run_import(dataset_id, str(import_path), "dry_run", "patch_labels", "import_as_external_snapshot")
    assert dry["matched_rows"] == 1
    assert dry["accepted_labels"] == 1
    assert read_json(records_path) == {}

    committed = imports.run_import(dataset_id, str(import_path), "commit", "patch_labels", "import_as_external_snapshot")
    records_doc = read_json(records_path)
    assert committed["updated_items"] == 1
    assert records_doc["sha256:item-0"]["imported_labels"]["输入图"]["菜品种类"]["value"] == "西餐"
    assert records_doc["sha256:item-0"]["external_stage_snapshots"][0]["annotations"]["rough"]["username"] == "importer"

    keep_path = tmp_path / "keep.jsonl"
    write_jsonl(keep_path, [{"item_id": "sha256:item-0", "labels": {"输入图": {"菜品种类": "中餐", "辣度": "高"}}}])
    imports.run_import(dataset_id, str(keep_path), "commit", "keep_existing", "audit_only")
    records_doc = read_json(records_path)
    assert records_doc["sha256:item-0"]["imported_labels"]["输入图"]["菜品种类"]["value"] == "西餐"
    assert records_doc["sha256:item-0"]["imported_labels"]["输入图"]["辣度"]["value"] == "高"

    replace_path = tmp_path / "replace.jsonl"
    write_jsonl(replace_path, [{"item_id": "sha256:item-0", "labels": {"输入图": {"辣度": "低"}}}])
    imports.run_import(dataset_id, str(replace_path), "commit", "replace_labels", "audit_only")
    records_doc = read_json(records_path)
    assert records_doc["sha256:item-0"]["imported_labels"] == {
        "输入图": {"辣度": {"value": "低", "import_id": records_doc["sha256:item-0"]["imported_labels"]["输入图"]["辣度"]["import_id"]}}
    }

    effective_path = tmp_path / "effective.jsonl"
    write_jsonl(effective_path, [{"item_id": "sha256:item-1", "labels": {"输入图": {"菜品种类": "中餐"}}, "fine_annotations": {"values": {"quality": {"mos": 5}}}}])
    effective = imports.run_import(dataset_id, str(effective_path), "commit", "patch_labels", "replace_effective_record")
    records_doc = read_json(records_path)
    assert records_doc["sha256:item-1"]["fine"]["status"] == "effective"
    assert records_doc["sha256:item-1"]["fine"]["import_id"] == effective["import_id"]

    repeat = imports.run_import(dataset_id, str(effective_path), "commit", "patch_labels", "audit_only")
    assert any(warning["code"] == "DUPLICATE_IMPORT_SOURCE" for warning in repeat["warnings"])


def test_import_api_and_page(tmp_path, monkeypatch):
    from web.annotations_v3.app import create_app

    dataset_doc, data_dir = make_dataset(tmp_path, monkeypatch)
    dataset_id = dataset_doc["dataset_id"]
    import_path = tmp_path / "api-import.jsonl"
    write_jsonl(import_path, [{"external_id": "ext-0", "labels": {"输入图": {"菜品种类": "西餐"}}}])
    client = create_app().test_client()

    validate = client.post(
        f"/api/datasets/{dataset_id}/imports/validate",
        json={"path": str(import_path), "merge_policy": "patch_labels", "stage_record_policy": "audit_only"},
    )
    assert validate.status_code == 200
    validate_report = validate.get_json()
    assert validate_report["matched_rows"] == 1
    assert read_json(data_dir / "datasets" / dataset_id / "records.json") == {}

    commit = client.post(
        f"/api/datasets/{dataset_id}/imports",
        json={"path": str(import_path), "merge_policy": "patch_labels", "stage_record_policy": "audit_only"},
    )
    assert commit.status_code == 200
    report = commit.get_json()
    assert report["updated_items"] == 1
    assert read_json(data_dir / "datasets" / dataset_id / "records.json")["sha256:item-0"]["imported_labels"]

    fetched = client.get(f"/api/datasets/{dataset_id}/imports/{report['import_id']}")
    assert fetched.status_code == 200
    assert fetched.get_json()["import_id"] == report["import_id"]

    errors = client.get(f"/api/datasets/{dataset_id}/imports/{report['import_id']}/errors")
    assert errors.status_code == 200
    assert errors.get_json() == {"errors": report["errors"]}

    page = client.get(f"/datasets/{dataset_id}/imports")
    assert page.status_code == 200
    assert b'data-page="imports"' in page.data
    assert b'id="importReport"' in page.data
