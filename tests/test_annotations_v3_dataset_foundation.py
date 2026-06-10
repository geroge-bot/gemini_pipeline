import json
import sys
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


REQUIRED_FOUNDATION_TAGS = {
    "storage.json_atomic",
    "storage.json_default_copy",
    "storage.jsonl",
    "items.normalize",
    "items.validation",
    "order.natural",
    "order.shuffled",
    "dataset.create_files",
    "dataset.read_helpers",
    "api.dataset_routes",
    "api.error_routes",
}


def covers(*tags):
    def decorator(test_func):
        test_func.foundation_tags = set(tags)
        return test_func

    return decorator


def test_foundation_feature_tests_are_tagged():
    tagged = set()
    unknown_tags = set()
    for name, value in globals().items():
        if not name.startswith("test_") or value is test_foundation_feature_tests_are_tagged:
            continue
        feature_tags = getattr(value, "foundation_tags", set())
        tagged.update(feature_tags)
        unknown_tags.update(feature_tags - REQUIRED_FOUNDATION_TAGS)

    assert unknown_tags == set()
    assert REQUIRED_FOUNDATION_TAGS <= tagged


@covers("storage.json_atomic", "storage.json_default_copy")
def test_storage_json_helpers_write_atomically_and_return_default_copy(tmp_path):
    from web.annotations_v3 import storage

    target = tmp_path / "nested" / "state.json"
    storage.write_json_atomic(target, {"name": "中文数据集", "items": [1, 2]})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"name": "中文数据集", "items": [1, 2]}
    assert list(target.parent.glob("*.tmp")) == []

    default = {"datasets": []}
    first = storage.read_json(tmp_path / "missing.json", default)
    first["datasets"].append({"dataset_id": "changed"})

    assert storage.read_json(tmp_path / "missing.json", default) == {"datasets": []}
    assert default == {"datasets": []}


@covers("storage.jsonl")
def test_storage_jsonl_helpers_read_line_numbered_objects(tmp_path):
    from web.annotations_v3 import storage

    target = tmp_path / "items" / "items.jsonl"
    rows = [{"src_image": "原图/a.jpg"}, {"dst_image": "输出图/a.jpg"}]

    storage.write_jsonl(target, rows)

    assert target.read_text(encoding="utf-8").splitlines() == [
        '{"src_image":"原图/a.jpg"}',
        '{"dst_image":"输出图/a.jpg"}',
    ]
    assert storage.read_jsonl_objects(target) == [(1, rows[0]), (2, rows[1])]


@covers("storage.jsonl")
def test_storage_jsonl_reader_rejects_invalid_json_and_non_object_rows(tmp_path):
    from web.annotations_v3 import storage

    invalid_json = tmp_path / "invalid.jsonl"
    invalid_json.write_text('{"src_image": "a.jpg"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="第 1 行不是合法 JSON"):
        storage.read_jsonl_objects(invalid_json)

    non_object = tmp_path / "non_object.jsonl"
    non_object.write_text('["a.jpg"]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="第 1 行必须是 JSON 对象"):
        storage.read_jsonl_objects(non_object)


@covers("items.normalize")
def test_normalize_items_generates_stable_ids_and_defaults():
    from web.annotations_v3 import datasets

    raw_rows = [
        (
            1,
            {
                "src_image": " ori/a.jpg ",
                "dst_image": " gen/a.jpg ",
                "labels": {"输入图": {"菜品种类": "中餐"}},
                "prompt": "make it brighter",
                "prompt_path": "prompts/a.txt",
            },
        ),
        (
            2,
            {
                "item_id": "sha256:provided",
                "source_row_id": "vendor-row-2",
                "external_id": "external-2",
                "src_image": "ori/b.jpg",
                "dst_image": "gen/b.jpg",
            },
        ),
    ]

    items = datasets.normalize_items(raw_rows)
    repeated = datasets.normalize_items(raw_rows)

    assert items[0]["item_id"].startswith("sha256:")
    assert items[0]["item_id"] == repeated[0]["item_id"]
    assert items[0]["source_row_id"] == "row-000001"
    assert items[0]["external_id"] is None
    assert items[0]["src_image"] == "ori/a.jpg"
    assert items[0]["dst_image"] == "gen/a.jpg"
    assert items[0]["item_index"] == 0
    assert items[0]["labels"] == {"输入图": {"菜品种类": "中餐"}}
    assert items[0]["prompt"] == "make it brighter"
    assert items[0]["prompt_path"] == "prompts/a.txt"
    assert items[1]["item_id"] == "sha256:provided"
    assert items[1]["source_row_id"] == "vendor-row-2"
    assert items[1]["external_id"] == "external-2"
    assert items[1]["item_index"] == 1


@covers("items.validation")
def test_normalize_items_rejects_duplicate_item_ids_and_missing_paths():
    from web.annotations_v3 import datasets

    with pytest.raises(ValueError, match="重复 item_id: sha256:duplicate"):
        datasets.normalize_items(
            [
                (1, {"item_id": "sha256:duplicate", "src_image": "a.jpg", "dst_image": "b.jpg"}),
                (2, {"item_id": "sha256:duplicate", "src_image": "c.jpg", "dst_image": "d.jpg"}),
            ]
        )

    with pytest.raises(ValueError, match="第 7 行缺少 src_image 或 dst_image"):
        datasets.normalize_items([(7, {"src_image": "a.jpg"})])


def make_items(count):
    return [
        {
            "item_id": f"sha256:item-{index}",
            "item_index": index,
            "source_row_id": f"row-{index + 1:06d}",
            "external_id": None,
            "src_image": f"src/{index}.jpg",
            "dst_image": f"dst/{index}.jpg",
        }
        for index in range(count)
    ]


@covers("order.natural")
def test_build_order_manifest_preserves_natural_item_order():
    from web.annotations_v3 import datasets

    items = make_items(3)
    order_config = datasets.normalize_order_config({"mode": "natural"})

    manifest = datasets.build_order_manifest("dataset-1", items, order_config, 123.0)

    assert manifest["version"] == 1
    assert manifest["dataset_id"] == "dataset-1"
    assert manifest["mode"] == "natural"
    assert manifest["seed"] is None
    assert manifest["created_at"] == 123.0
    assert manifest["item_count"] == 3
    assert manifest["order"] == [
        {"rank": 0, "item_id": "sha256:item-0", "item_index": 0},
        {"rank": 1, "item_id": "sha256:item-1", "item_index": 1},
        {"rank": 2, "item_id": "sha256:item-2", "item_index": 2},
    ]


@covers("order.shuffled")
def test_build_order_manifest_shuffles_reproducibly_with_local_seed():
    from web.annotations_v3 import datasets

    items = make_items(5)
    order_config = datasets.normalize_order_config({"mode": "shuffled", "seed": "seed-a"})

    first = datasets.build_order_manifest("dataset-1", items, order_config, 123.0)
    second = datasets.build_order_manifest("dataset-1", items, order_config, 123.0)

    first_indexes = [entry["item_index"] for entry in first["order"]]
    assert first_indexes == [entry["item_index"] for entry in second["order"]]
    assert first_indexes != [0, 1, 2, 3, 4]
    assert sorted(first_indexes) == [0, 1, 2, 3, 4]
    assert all(set(entry) == {"rank", "item_id", "item_index"} for entry in first["order"])


@covers("order.shuffled")
def test_normalize_order_config_validates_mode_and_generates_shuffle_seed():
    from web.annotations_v3 import datasets

    order_config = datasets.normalize_order_config({"mode": "shuffled"})

    assert order_config["mode"] == "shuffled"
    assert isinstance(order_config["seed"], str)
    assert order_config["seed"]
    assert order_config["scope"] == "dataset"
    assert order_config["persist_manifest"] is True
    assert order_config["version"] == 1

    with pytest.raises(ValueError, match="order.mode 必须是 natural 或 shuffled"):
        datasets.normalize_order_config({"mode": "random"})


@covers("dataset.create_files")
def test_create_dataset_persists_foundation_files(tmp_path, monkeypatch):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / "source" / "items.jsonl"
    root_dir = tmp_path / "images"
    write_source_jsonl(
        source_jsonl,
        [
            {"src_image": "ori/a.jpg", "dst_image": "gen/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "ori/b.jpg", "dst_image": "gen/b.jpg", "external_id": "vendor-b"},
        ],
    )
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))

    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": "dataset-1",
            "name": "food batch a",
            "source_jsonl": str(source_jsonl),
            "root_dir": str(root_dir),
            "order": {"mode": "shuffled", "seed": "seed-a"},
        }
    )

    dataset_dir = data_dir / "datasets" / "dataset-1"
    state = read_json(data_dir / "state.json")
    persisted_dataset = read_json(dataset_dir / "dataset.json")
    items = [row for _, row in storage.read_jsonl_objects(dataset_dir / "items.jsonl")]
    order_manifest = read_json(dataset_dir / "order_manifest.json")

    assert state["version"] == 1
    assert state["datasets"] == [datasets.dataset_summary(dataset_doc)]
    assert persisted_dataset["version"] == 1
    assert persisted_dataset["dataset_id"] == "dataset-1"
    assert persisted_dataset["name"] == "food batch a"
    assert persisted_dataset["root_dir"] == str(root_dir.resolve())
    assert persisted_dataset["source_jsonl"] == str(source_jsonl.resolve())
    assert persisted_dataset["item_count"] == 2
    assert persisted_dataset["order"] == {
        "mode": "shuffled",
        "seed": "seed-a",
        "scope": "dataset",
        "persist_manifest": True,
        "version": 1,
    }
    assert persisted_dataset["stages"] == datasets.default_stage_config()
    assert items[0]["item_index"] == 0
    assert items[0]["labels"] == {"输入图": {"菜品种类": "中餐"}}
    assert items[1]["external_id"] == "vendor-b"
    assert order_manifest["dataset_id"] == "dataset-1"
    assert order_manifest["item_count"] == 2
    assert sorted(entry["item_id"] for entry in order_manifest["order"]) == sorted(item["item_id"] for item in items)
    assert read_json(dataset_dir / "candidate_snapshots.json") == {"version": 1, "snapshots": []}
    assert read_json(dataset_dir / "records.json") == {}
    assert read_json(dataset_dir / "assignments.json") == {"version": 1, "blocks": []}
    assert read_json(dataset_dir / "labels_schema_snapshot.json")["version"] == 1
    assert read_json(dataset_dir / "preview_cache" / "manifest.json") == {
        "version": 1,
        "dataset_id": "dataset-1",
        "assets": {},
    }


@covers("dataset.read_helpers")
def test_dataset_read_helpers_return_persisted_documents(tmp_path, monkeypatch):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / "items.jsonl"
    write_source_jsonl(source_jsonl, [{"src_image": "ori/a.jpg", "dst_image": "gen/a.jpg"}])
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))

    dataset_doc = datasets.create_dataset(
        {
            "dataset_id": "dataset-1",
            "name": "food batch a",
            "source_jsonl": str(source_jsonl),
        }
    )

    assert datasets.list_datasets() == [datasets.dataset_summary(dataset_doc)]
    assert datasets.get_dataset("dataset-1") == dataset_doc
    assert datasets.get_order_manifest("dataset-1") == read_json(
        data_dir / "datasets" / "dataset-1" / "order_manifest.json"
    )

    with pytest.raises(FileNotFoundError):
        datasets.get_dataset("missing")

    with pytest.raises(FileNotFoundError):
        datasets.get_order_manifest("missing")


@covers("api.dataset_routes")
def test_dataset_api_routes_create_and_read_dataset(tmp_path, monkeypatch):
    from web.annotations_v3 import storage
    from web.annotations_v3.app import create_app

    data_dir = tmp_path / "v3-data"
    source_jsonl = tmp_path / "items.jsonl"
    write_source_jsonl(source_jsonl, [{"src_image": "ori/a.jpg", "dst_image": "gen/a.jpg"}])
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    assert client.get("/api/datasets").get_json() == {"datasets": []}

    create_response = client.post(
        "/api/datasets",
        json={
            "dataset_id": "dataset-1",
            "name": "food batch a",
            "source_jsonl": str(source_jsonl),
            "order": {"mode": "natural"},
        },
    )

    assert create_response.status_code == 201
    created = create_response.get_json()
    assert created["dataset_id"] == "dataset-1"
    assert created["item_count"] == 1
    assert created["order"]["mode"] == "natural"

    list_response = client.get("/api/datasets")
    assert list_response.status_code == 200
    assert list_response.get_json()["datasets"][0]["dataset_id"] == "dataset-1"

    get_response = client.get("/api/datasets/dataset-1")
    assert get_response.status_code == 200
    assert get_response.get_json()["name"] == "food batch a"

    manifest_response = client.get("/api/datasets/dataset-1/order-manifest")
    assert manifest_response.status_code == 200
    assert manifest_response.get_json()["order"] == [
        {"rank": 0, "item_id": created_manifest_item_id(data_dir), "item_index": 0}
    ]


@covers("api.error_routes")
def test_dataset_api_routes_return_json_errors(tmp_path, monkeypatch):
    from web.annotations_v3 import storage
    from web.annotations_v3.app import create_app

    monkeypatch.setenv(storage.DATA_DIR_ENV, str(tmp_path / "v3-data"))
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    bad_create = client.post("/api/datasets", json={"name": ""})
    assert bad_create.status_code == 400
    assert "error" in bad_create.get_json()

    missing_dataset = client.get("/api/datasets/missing")
    assert missing_dataset.status_code == 404
    assert missing_dataset.get_json() == {"error": "dataset not found"}

    missing_manifest = client.get("/api/datasets/missing/order-manifest")
    assert missing_manifest.status_code == 404
    assert missing_manifest.get_json() == {"error": "dataset not found"}


def created_manifest_item_id(data_dir):
    manifest = read_json(data_dir / "datasets" / "dataset-1" / "order_manifest.json")
    return manifest["order"][0]["item_id"]
