import json
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_workspace_tmp():
    path = Path("annotations_test_tmp") / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_v2_task_creation_loads_jsonl_and_label_files():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    label_dir = tmp_path / "labels"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])
    write_json(label_dir / "src" / "a.json", {"labels": {"菜品种类": "中餐"}})
    write_json(label_dir / "dst" / "a.json", {"美学评分": 4})

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "v2 food",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "label_dir": str(label_dir),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )

    items = store.list_stage_items(task["id"], "rough")
    assert task["name"] == "v2 food"
    assert items[0]["labels"] == {"输入图": {"菜品种类": "中餐"}, "输出图": {"美学评分": 4}}
    assert store.summary(task["id"])["total"] == 1


def test_v2_task_creation_removes_non_canonical_ai_label_fields():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    label_dir = tmp_path / "labels"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {
                    "输出图": {
                        "景别": "中近景",
                        "景别_v2": "近景",
                        "景别_alt": "近景",
                        "图一": "污染字段",
                    }
                },
            }
        ],
    )
    write_json(label_dir / "dst" / "a.json", {"labels": {"景别_secondary": "近景", "美学评分": 4}})

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "sanitize labels",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "label_dir": str(label_dir),
        }
    )

    item = store.list_stage_items(task["id"], "rough")[0]
    assert item["labels"] == {"输出图": {"景别": "中近景", "美学评分": 4}}


def test_v2_task_payload_exposes_standard_label_choice_options():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "label choices",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )

    assert task["label_option_groups"][0]["name"] == "输入图"
    assert task["label_option_groups"][0]["dimensions"][1]["name"] == "菜品种类"
    assert "中餐" in task["label_option_groups"][0]["dimensions"][1]["options"]


def test_v2_delete_task_only_unregisters_task_and_preserves_data_dir():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"name": "delete me", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    task_data_dir = Path(task["data_dir"])
    items_path = task_data_dir / "items.json"
    records_path = task_data_dir / "records.json"

    deleted = store.delete_task(task["id"])

    assert deleted["id"] == task["id"]
    assert store.list_tasks() == []
    assert task_data_dir.exists()
    assert items_path.exists()
    assert records_path.exists()


def test_v2_stage_gates_sampling_label_correction_and_export():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "v2 food",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True, "issue_options": ["主体问题"]},
            "fine": {"min_mos": 4, "enable_defect": True},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 4, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": True, "issues": ["主体问题"]})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 5, "has_defect": False})

    fine_items = store.list_stage_items(task["id"], "fine")
    assert [item["item_index"] for item in fine_items] == [0, 2]

    store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "bob", "mos": 3, "has_defect": False})

    sample = store.sample(task["id"], {"target_count": 2, "min_per_bucket": 1})
    assert sample["sampled_count"] == 1
    assert sample["buckets"] == [{"bucket": "输入图/菜品种类=中餐", "candidate_count": 1, "sampled_count": 1}]
    assert [item["item_index"] for item in store.list_stage_items(task["id"], "label")] == [0]

    corrected = {"输入图": {"菜品种类": "融合菜"}}
    store.save_label(task["id"], 0, {"username": "carol", "labels": corrected})

    summary = store.summary(task["id"])
    assert summary["rough_passed"] == 2
    assert summary["fine_passed"] == 1
    assert summary["sampled"] == 1
    assert summary["label_completed"] == 1

    rows = [json.loads(line) for line in store.export_jsonl(task["id"]).splitlines()]
    assert rows[0]["rough"]["mos"] == 4
    assert rows[0]["fine"]["mos"] == 5
    assert rows[0]["sampled"] is True
    assert rows[0]["corrected_labels"] == corrected
    assert rows[1]["rough"]["has_defect"] is True


def test_v2_sampling_buckets_support_selected_counts_and_select_all():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {
                "src_image": "src/a.jpg",
                "dst_image": "dst/a.jpg",
                "labels": {"输入图": {"菜品种类": "中餐"}, "输出图": {"景别": "近景"}},
            },
            {
                "src_image": "src/b.jpg",
                "dst_image": "dst/b.jpg",
                "labels": {"输入图": {"菜品种类": "中餐"}, "输出图": {"景别": "近景"}},
            },
            {
                "src_image": "src/c.jpg",
                "dst_image": "dst/c.jpg",
                "labels": {"输入图": {"菜品种类": "西餐"}, "输出图": {"景别": "全景"}},
            },
            {
                "src_image": "src/d.jpg",
                "dst_image": "dst/d.jpg",
                "labels": {"输入图": {"菜品种类": "甜品"}, "输出图": {"景别": "特写"}},
            },
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "sample by bucket",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4},
            "fine": {"min_mos": 4},
        }
    )
    for item_index in range(4):
        store.save_rough(task["id"], item_index, {"username": "alice", "mos": 5, "has_defect": False})
        store.save_fine(task["id"], item_index, {"username": "bob", "mos": 5 if item_index != 3 else 3, "has_defect": False})

    buckets = store.sample_buckets(task["id"])

    assert buckets["candidate_count"] == 3
    assert buckets["buckets"] == [
        {"bucket": "输入图/菜品种类=中餐", "candidate_count": 2, "sampled_count": 0},
        {"bucket": "输入图/菜品种类=西餐", "candidate_count": 1, "sampled_count": 0},
        {"bucket": "输出图/景别=全景", "candidate_count": 1, "sampled_count": 0},
        {"bucket": "输出图/景别=近景", "candidate_count": 2, "sampled_count": 0},
    ]

    sample = store.sample(
        task["id"],
        {
            "selections": [
                {"bucket": "输出图/景别=近景", "count": 1},
                {"bucket": "输入图/菜品种类=西餐", "count": 5},
            ]
        },
    )

    assert sample["candidate_count"] == 3
    assert sample["sampled_count"] == 2
    assert sample["buckets"] == [
        {"bucket": "输入图/菜品种类=中餐", "candidate_count": 2, "sampled_count": 1},
        {"bucket": "输入图/菜品种类=西餐", "candidate_count": 1, "sampled_count": 1},
        {"bucket": "输出图/景别=全景", "candidate_count": 1, "sampled_count": 1},
        {"bucket": "输出图/景别=近景", "candidate_count": 2, "sampled_count": 1},
    ]
    assert len(store.list_stage_items(task["id"], "label")) == 2

    sample_all = store.sample(task["id"], {"select_all": True})

    assert sample_all["sampled_count"] == 3
    assert len(store.list_stage_items(task["id"], "label")) == 3


def test_v2_visualization_results_are_stage_specific():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "visualize",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True},
            "fine": {"min_mos": 4, "enable_defect": False},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": True})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "bob", "mos": 3, "has_defect": False})
    store.sample(task["id"], {"target_count": 2, "min_per_bucket": 1})
    store.save_label(task["id"], 0, {"username": "carol", "labels": {"输入图": {"菜品种类": "融合菜"}}})

    rough_total, rough_rows = store.get_visualization_results(task["id"], "rough", offset=0, limit=10)
    fine_total, fine_rows = store.get_visualization_results(task["id"], "fine", offset=0, limit=10)
    sample_total, sample_rows = store.get_visualization_results(task["id"], "sample", offset=0, limit=10)
    label_total, label_rows = store.get_visualization_results(task["id"], "label", offset=0, limit=10)

    assert rough_total == 3
    assert [row["item_index"] for row in rough_rows] == [0, 1, 2]
    assert rough_rows[1]["stage_passed"] is False
    assert rough_rows[1]["stage_annotations"][0]["has_defect"] is True

    assert fine_total == 2
    assert [row["item_index"] for row in fine_rows] == [0, 2]
    assert fine_rows[0]["stage_passed"] is True
    assert fine_rows[1]["stage_passed"] is False

    assert sample_total == 1
    assert sample_rows[0]["sampled"] is True
    assert sample_rows[0]["sample_bucket"] == "输入图/菜品种类=中餐"

    assert label_total == 1
    assert label_rows[0]["corrected_labels"] == {"输入图": {"菜品种类": "融合菜"}}
    assert label_rows[0]["label_username"] == "carol"
    assert label_rows[0]["src_relative_path"] == "src/a.jpg"
    assert label_rows[0]["dst_relative_path"] == "dst/a.jpg"


def test_v2_visualization_results_support_mos_defect_annotator_and_tag_filters():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "visualize filters",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True},
            "fine": {"min_mos": 4, "enable_defect": True},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "bob", "mos": 3, "has_defect": True})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 4, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "dora", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "erin", "mos": 4, "has_defect": True})

    _, mos_rows = store.get_visualization_results(task["id"], "rough", filters={"mos": [5]}, offset=0, limit=10)
    _, defect_rows = store.get_visualization_results(task["id"], "rough", filters={"has_defect": [True]}, offset=0, limit=10)
    _, annotator_rows = store.get_visualization_results(task["id"], "rough", filters={"annotators": ["alice"]}, offset=0, limit=10)
    _, tag_rows = store.get_visualization_results(
        task["id"],
        "rough",
        filters={"labels": [{"path": ["输入图", "菜品种类"], "values": ["西餐"]}]},
        offset=0,
        limit=10,
    )
    _, fine_rows = store.get_visualization_results(
        task["id"],
        "fine",
        filters={"has_defect": [True], "annotators": ["erin"]},
        offset=0,
        limit=10,
    )

    assert [row["item_index"] for row in mos_rows] == [0]
    assert [row["item_index"] for row in defect_rows] == [1]
    assert [row["item_index"] for row in annotator_rows] == [0, 2]
    assert [row["item_index"] for row in tag_rows] == [1]
    assert [row["item_index"] for row in fine_rows] == [2]


def test_v2_visualization_results_api_returns_filter_options_and_accepts_filters():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task(
            {
                "name": "api visualize filters",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            }
        )
        annotations_v2_app.store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
        annotations_v2_app.store.save_rough(task["id"], 1, {"username": "bob", "mos": 3, "has_defect": True})
        client = annotations_v2_app.app.test_client()

        filters = json.dumps({"mos": [5], "has_defect": [False], "annotators": ["alice"]}, ensure_ascii=False)
        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=rough&filters={filters}")
        payload = response.get_json()

        assert response.status_code == 200
        assert payload["total"] == 1
        assert payload["results"][0]["item_index"] == 0
        assert payload["filter_options"]["mos"] == [3, 5]
        assert payload["filter_options"]["has_defect"] == [False, True]
        assert payload["filter_options"]["annotators"] == ["alice", "bob"]
        assert payload["filter_options"]["label_options"] == [
            {"name": "输入图", "dimensions": [{"name": "菜品种类", "options": ["中餐", "西餐"]}]}
        ]
    finally:
        annotations_v2_app.store = old_store


def test_v2_visualization_results_api_pages_stage_rows():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        client = annotations_v2_app.app.test_client()
        task = client.post(
            "/api/tasks",
            json={"name": "api visualize", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)},
        ).get_json()["task"]
        client.post(f"/api/tasks/{task['id']}/items/0/rough", json={"username": "alice", "mos": 5, "has_defect": False})

        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=rough&page=0&limit=1")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["stage"] == "rough"
        assert payload["total"] == 1
        assert payload["page"] == 0
        assert payload["limit"] == 1
        assert payload["results"][0]["item_index"] == 0
        assert payload["results"][0]["stage_result"]["mos"] == 5
    finally:
        annotations_v2_app.store = old_store


def test_v2_sample_page_and_bucket_api_are_available():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task(
            {
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            }
        )
        annotations_v2_app.store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
        annotations_v2_app.store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
        client = annotations_v2_app.app.test_client()

        page_response = client.get(f"/dataset/sample/{task['id']}")
        bucket_response = client.get(f"/api/tasks/{task['id']}/sample-buckets")

        page_html = page_response.data.decode("utf-8")
        assert page_response.status_code == 200
        assert 'data-page="sample"' in page_html
        assert f'data-task-id="{task["id"]}"' in page_html
        assert 'id="selectAllSampleBtn"' in page_html
        assert 'id="sampleBucketList"' in page_html
        assert 'id="runSampleBtn"' in page_html

        assert bucket_response.status_code == 200
        payload = bucket_response.get_json()
        assert payload["result"]["candidate_count"] == 1
        assert payload["result"]["buckets"][0]["bucket"] == "输入图/菜品种类=中餐"
    finally:
        annotations_v2_app.store = old_store


def test_v2_visualization_results_reject_unknown_stage():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    try:
        client = annotations_v2_app.app.test_client()
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=other")
        assert response.status_code == 400
        assert response.get_json()["error"] == "未知可视化阶段"
    finally:
        annotations_v2_app.store = old_store
        annotations_v2_app.app.config.update(PROPAGATE_EXCEPTIONS=None)


def test_v2_import_annotations_jsonl_merges_screening_sampling_and_labels():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    import_path = tmp_path / "import.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    write_jsonl(
        import_path,
        [
            {
                "item_index": 0,
                "object_labels": {"输入图": {"菜品种类": "融合菜", "景别_alt": "污染字段"}},
                "rough_annotations": [
                    {"username": "r1", "mos": 5, "has_defect": False},
                    {"username": "r2", "mos": 4, "has_defect": False, "issues": ["主体问题"]},
                ],
                "fine": {"username": "f1", "mos": 5, "has_defect": False},
                "sampled": True,
                "sample_bucket": "外部平台分桶",
                "corrected_labels": {"输入图": {"菜品种类": "创意菜", "景别_v2": "污染字段"}},
                "label_username": "labeler",
            },
            {
                "src_image": "src/b.jpg",
                "dst_image": "dst/b.jpg",
                "rough": {"username": "r3", "mos": 3, "has_defect": True, "primary_issue": "主体问题"},
            },
            {"item_index": 99, "rough": {"username": "missing", "mos": 5, "has_defect": False}},
        ],
    )

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "import external",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4, "issue_options": ["主体问题"]},
            "fine": {"annotator_count": 1, "min_mos": 4},
        }
    )

    result = store.import_annotations_jsonl(task["id"], import_path)

    assert result["total_rows"] == 3
    assert result["imported_count"] == 2
    assert result["unmatched_count"] == 1
    assert result["updated_items"] == 1
    assert result["updated_records"] == 2
    assert result["unmatched_rows"] == [{"line": 3, "reason": "未匹配到任务图片"}]

    item = store.list_stage_items(task["id"], "rough")[0]
    assert item["labels"] == {"输入图": {"菜品种类": "融合菜"}}

    records = store._read_records(store._require_task(task["id"]))
    first_record = records["0"]
    assert [entry["username"] for entry in first_record["rough_annotations"]] == ["r1", "r2"]
    assert first_record["rough"]["mos"] == 4
    assert first_record["rough"]["has_defect"] is False
    assert first_record["fine"]["username"] == "f1"
    assert first_record["sampled"] is True
    assert first_record["sample_bucket"] == "外部平台分桶"
    assert first_record["label"]["username"] == "labeler"
    assert first_record["label"]["labels"] == {"输入图": {"菜品种类": "创意菜"}}

    second_record = records["1"]
    assert second_record["rough"]["username"] == "r3"
    assert second_record["rough"]["has_defect"] is True


def test_v2_import_annotations_api_accepts_jsonl_path():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    import_path = tmp_path / "import.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])
    write_jsonl(import_path, [{"item_index": 0, "rough": {"username": "alice", "mos": 5, "has_defect": False}}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        client = annotations_v2_app.app.test_client()

        response = client.post(f"/api/tasks/{task['id']}/import", json={"jsonl_path": str(import_path)})

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["result"]["imported_count"] == 1
        assert annotations_v2_app.store.summary(task["id"])["rough_completed"] == 1
    finally:
        annotations_v2_app.store = old_store


def test_v2_screening_assignment_keeps_per_user_records_and_caps_annotators():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg"},
            {"src_image": "src/d.jpg", "dst_image": "dst/d.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "multi user",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4},
            "fine": {"annotator_count": 2, "min_mos": 4},
        }
    )

    alice_first = store.list_stage_items(task["id"], "rough", username="alice")[0]["item_index"]
    bob_first = store.list_stage_items(task["id"], "rough", username="bob")[0]["item_index"]
    assert alice_first != bob_first

    alice_record = store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 0, {"username": "alice", "mos": 4, "has_defect": False})
    bob_record = store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})

    full_item_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "rough", username="carol")]
    assert 0 not in full_item_indexes
    assert alice_record["username"] == "alice"
    assert bob_record["username"] == "bob"

    records = store._read_records(store._require_task(task["id"]))
    assert [entry["username"] for entry in records["0"]["rough_annotations"]] == ["alice", "bob"]
    assert records["0"]["rough"]["mos"] == 4

    try:
        store.save_rough(task["id"], 0, {"username": "carol", "mos": 5, "has_defect": False})
    except ValueError as exc:
        assert str(exc) == "该图片的粗筛标注人数已达到上限"
    else:
        raise AssertionError("expected rough annotator cap to reject the third user")


def test_v2_stage_items_do_not_reload_records_already_annotated_by_same_user():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "no repeated assignment",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4},
            "fine": {"annotator_count": 2, "min_mos": 4},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    alice_rough_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "rough", username="alice")]
    bob_rough_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "rough", username="bob")]

    assert 0 not in alice_rough_indexes
    assert 0 in bob_rough_indexes

    store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "carol", "mos": 5, "has_defect": False})

    carol_fine_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "fine", username="carol")]
    dora_fine_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "fine", username="dora")]

    assert 0 not in carol_fine_indexes
    assert 0 in dora_fine_indexes


def test_v2_fine_assignment_waits_for_required_rough_votes_and_aggregate_pass():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "fine gates",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2, "min_mos": 4},
            "fine": {"annotator_count": 1, "min_mos": 4},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    assert store.list_stage_items(task["id"], "fine", username="carol") == []

    store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "bob", "mos": 3, "has_defect": False})

    fine_indexes = [item["item_index"] for item in store.list_stage_items(task["id"], "fine", username="carol")]
    assert fine_indexes == [0]


def test_v2_summary_reports_screening_round_progress():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "round progress",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "rough": {"annotator_count": 2},
            "fine": {"annotator_count": 2},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": False})

    summary = store.summary(task["id"])

    assert summary["rough_annotator_count"] == 2
    assert summary["rough_rounds"] == [
        {"round": 1, "completed": 2, "total": 3},
        {"round": 2, "completed": 1, "total": 3},
    ]
    assert summary["rough_completed"] == 1
    assert summary["rough_annotation_completed"] == 3
    assert summary["rough_annotation_target"] == 6


def test_v2_api_exposes_summary_and_stage_endpoints():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        client = annotations_v2_app.app.test_client()
        create_response = client.post(
            "/api/tasks",
            json={
                "name": "api task",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            },
        )
        task = create_response.get_json()["task"]
        rough_response = client.post(
            f"/api/tasks/{task['id']}/items/0/rough",
            json={"username": "alice", "mos": 5, "has_defect": False},
        )
        fine_items_response = client.get(f"/api/tasks/{task['id']}/items?stage=fine")

        assert create_response.status_code == 201
        assert rough_response.status_code == 200
        assert fine_items_response.get_json()["items"][0]["item_index"] == 0
        assert client.get(f"/api/tasks/{task['id']}/summary").get_json()["summary"]["rough_passed"] == 1
    finally:
        annotations_v2_app.store = old_store


def test_v2_delete_task_api_requires_admin_username_and_unregisters_only():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    try:
        client = annotations_v2_app.app.test_client()
        task = annotations_v2_app.store.create_task(
            {"name": "api delete", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)}
        )
        task_data_dir = Path(task["data_dir"])

        forbidden_response = client.delete(f"/api/tasks/{task['id']}", json={"username": "alice"})
        allowed_response = client.delete(f"/api/tasks/{task['id']}", json={"username": "孙本猿"})

        assert forbidden_response.status_code == 403
        assert forbidden_response.get_json()["error"] == "只有孙本猿可以删除任务"
        assert allowed_response.status_code == 200
        assert allowed_response.get_json()["task"]["id"] == task["id"]
        assert annotations_v2_app.store.list_tasks() == []
        assert task_data_dir.exists()
    finally:
        annotations_v2_app.store = old_store
        annotations_v2_app.app.config.update(PROPAGATE_EXCEPTIONS=None)


def test_v2_create_task_reports_missing_jsonl_as_bad_request():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    try:
        client = annotations_v2_app.app.test_client()

        response = client.post(
            "/api/tasks",
            json={
                "name": "missing file",
                "root_dir": str(tmp_path),
                "jsonl_path": str(tmp_path / "missing.jsonl"),
            },
        )

        assert response.status_code == 400
        assert response.get_json()["error"] == f"jsonl 文件不存在：{tmp_path / 'missing.jsonl'}"
    finally:
        annotations_v2_app.store = old_store
        annotations_v2_app.app.config.update(PROPAGATE_EXCEPTIONS=None)


def test_v2_rate_page_route_and_main_page_are_separate():
    from web.annotations_v2 import app as annotations_v2_app

    annotations_v2_app.app.config.update(TESTING=True)
    client = annotations_v2_app.app.test_client()

    main_response = client.get("/")
    rate_response = client.get("/dataset/rate/task-123?stage=rough")

    main_html = main_response.data.decode("utf-8")
    rate_html = rate_response.data.decode("utf-8")
    assert main_response.status_code == 200
    assert rate_response.status_code == 200
    assert 'id="taskList"' in main_html
    assert 'id="stageBody"' not in main_html
    assert 'id="stageBody"' in rate_html
    assert 'data-page="rate"' in rate_html


def test_v2_visualization_page_route_is_separate_from_rate_page():
    from web.annotations_v2 import app as annotations_v2_app

    annotations_v2_app.app.config.update(TESTING=True)
    client = annotations_v2_app.app.test_client()

    visualize_response = client.get("/dataset/visualize/task-123?stage=rough")
    visualize_html = visualize_response.data.decode("utf-8")

    assert visualize_response.status_code == 200
    assert 'data-page="visualize"' in visualize_html
    assert 'data-task-id="task-123"' in visualize_html
    assert 'id="visualizationStageTabs"' in visualize_html
    assert 'id="visualizationBody"' in visualize_html
    assert 'id="visualizationResultPanel"' in visualize_html
    assert 'id="stageForm"' not in visualize_html


def test_v2_frontend_uses_progress_entries_login_gate_and_no_primary_issue_field():
    index_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "index.html").read_text(encoding="utf-8")
    rate_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "rate.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="loginView"' in index_template
    assert 'id="appView"' in index_template
    assert 'class="topbar hidden"' in index_template
    assert 'id="primaryIssueInput"' not in index_template
    assert 'screeningProgressCells("粗筛", summary.rough_rounds, summary.rough_passed, { href: `/dataset/rate/${task.id}?stage=rough` })' in script
    assert 'screeningProgressCells("精筛", summary.fine_rounds, summary.fine_passed, { href: `/dataset/rate/${task.id}?stage=fine` })' in script
    assert 'progressCell("标签纠错", summary.label_completed, summary.sampled, null, { href: `/dataset/rate/${task.id}?stage=label` })' in script
    assert 'progressCell("采样", summary.sampled, summary.fine_passed, null, { href: `/dataset/sample/${task.id}` })' in script
    assert '<a href="${entry.href}" class="progressCell progressEntry">' in script
    assert '<button class="progressCell progressEntry" data-action="${entry.action}"' in script
    assert '<a class="buttonLike" href="/dataset/rate/${task.id}?stage=rough">粗筛</a>' not in script
    assert '<a class="buttonLike" href="/dataset/rate/${task.id}?stage=fine">精筛</a>' not in script
    assert '<button data-action="sample" data-id="${task.id}" type="button">采样</button>' not in script
    assert '<a class="buttonLike" href="/dataset/rate/${task.id}?stage=label">标签纠错</a>' not in script
    assert 'id="samplePanel"' not in index_template
    assert 'id="ratingControls"' in rate_template
    assert 'id="labelPreviewBlock"' not in rate_template
    assert 'id="recordPreview"' not in rate_template
    assert 'primaryIssueField' not in script
    assert 'otherIssueInput' not in script


def test_v2_screening_page_uses_three_column_rating_panel():
    rate_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "rate.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    stage_grid_start = rate_template.index('id="stageBody"')
    src_index = rate_template.index('id="srcImage"', stage_grid_start)
    dst_index = rate_template.index('id="dstImage"', stage_grid_start)
    form_index = rate_template.index('id="ratingControls"', stage_grid_start)

    assert src_index < dst_index < form_index
    assert 'class="panel annotationPanel"' in rate_template
    assert "mosScoreBar" in script
    assert 'name="mosOption"' in script
    assert 'name="defectOption"' in script
    assert 'value="false"' in script
    assert '${hasDefect ? "" : "checked"}' in script
    assert "otherIssueField" not in script
    assert "noteInput" not in script
    assert "grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) minmax(520px, 680px);" in styles


def test_v2_screening_page_exposes_keyboard_shortcuts():
    rate_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "rate.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "下一条（Space）" in rate_template
    assert "handleRateShortcuts" in script
    assert "selectMosScore" in script
    assert "setDefectValue(true)" in script
    assert 'event.code === "Space"' in script
    assert "/^[1-5]$/.test(event.key)" in script
    assert "是（E）" in script
    assert "isTextEditingShortcutTarget" in script
    assert '["radio", "checkbox"].includes(inputType)' in script
    assert "isEditableShortcutTarget" not in script


def test_v2_screening_auto_saves_when_paging_instead_of_save_buttons():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    rough_start = script.index('if (state.stage === "rough")')
    rough_end = script.index('if (state.stage === "fine")', rough_start)
    fine_end = script.index("const currentLabels", rough_end)
    screening_render = script[rough_start:fine_end]

    assert "保存粗筛" not in screening_render
    assert "保存精筛" not in screening_render
    assert "saveCurrentScreeningBeforePageChange" in script
    assert "goToItem(state.index + 1)" in script


def test_v2_frontend_supports_multi_annotator_assignment_and_round_progress():
    index_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="roughAnnotatorCountInput"' in index_template
    assert 'id="fineAnnotatorCountInput"' in index_template
    assert 'annotator_count: Number($("roughAnnotatorCountInput").value || 1)' in script
    assert 'annotator_count: Number($("fineAnnotatorCountInput").value || 1)' in script
    assert "screeningProgressCells" in script
    assert "第 ${round.round} 人" in script
    assert "username=${encodeURIComponent(state.username)}" in script
    assert "goToItem(state.index - 1)" in script
    assert "await saveCurrentScreeningBeforePageChange()" in script
    assert "请选择 MOS 分后再翻页" in script


def test_v2_fine_form_defaults_to_rough_result_for_fast_acceptance():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "fineDefaultRecord(record)" in script
    assert "function fineDefaultRecord(record)" in script
    assert "return record.fine || record.rough || {};" in script
    assert "const current = fineDefaultRecord(record);" in script
    assert "goToItem(state.index + 1)" in script


def test_v2_label_correction_uses_choice_bars_instead_of_text_inputs():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "function labelCorrectionPaths" in script
    assert "function labelOptionsForPath" in script
    assert "function labelChoiceInputs" in script
    assert "label.className = \"labelOption labelChoiceOption\"" in script
    assert "input.type = \"radio\"" in script
    assert "input.dataset.labelPath" in script
    assert "input.dataset.optionValue" in script
    assert "document.querySelectorAll(\".labelChoiceInput:checked\")" in script
    assert "JSON.parse(input.dataset.optionValue)" in script
    assert "labelPathInput" not in script
    assert "labelsJsonInput" not in script
    assert ".labelChoiceGroup" in styles
    assert ".labelChoiceGroup .labelOptions" in styles
    assert ".labelChoiceOption span" in styles
    assert "grid-template-columns: 1fr;" in styles
    assert '.labelOption input[type="radio"]' in styles


def test_v2_frontend_links_and_renders_stage_visualization_pages():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert '`/dataset/visualize/${task.id}?stage=rough`' in script
    assert '`/dataset/visualize/${task.id}?stage=fine`' in script
    assert '`/dataset/visualize/${task.id}?stage=sample`' in script
    assert '`/dataset/visualize/${task.id}?stage=label`' in script
    assert "openVisualizationPage" in script
    assert "reloadVisualizationResults" in script
    assert "renderVisualizationPage" in script
    assert "renderScreeningVisualization" in script
    assert "renderSampleVisualization" in script
    assert "renderLabelVisualization" in script


def test_v2_visualization_frontend_exposes_filter_controls():
    visualize_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "visualize.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="openVisualizationFilterBtn"' in visualize_template
    assert 'id="visualizationFilterOverlay"' in visualize_template
    assert 'id="visualizationFilterPanel"' in visualize_template
    assert 'id="visualizationFilterBody"' in visualize_template
    assert 'class="drawerOverlay hidden"' in visualize_template
    assert 'class="filterDrawer hidden"' in visualize_template
    assert 'class="panel visualizationFilterPanel hidden"' not in visualize_template
    assert "visualizationFilters" in script
    assert "visualizationFilterOptions" in script
    assert "renderVisualizationFilterPanel" in script
    assert "collectVisualizationFilters" in script
    assert "buildVisualizationFilterPayload" in script
    assert 'params.set("filters", JSON.stringify(buildVisualizationFilterPayload()))' in script
    assert '"MOS 分", visualizationFilterOptionsOrDefault(options.mos, [1, 2, 3, 4, 5]), "mos"' in script
    assert '"是否有质量问题"' in script
    assert '"标注者", options.annotators || [], "annotators"' in script
    assert 'renderVisualizationFilterGroup(dimension.name, dimension.options || [], "label", selected, path)' in script
    assert 'input.dataset.visualizationFilterType === "mos"' in script
    assert 'input.dataset.visualizationFilterType === "has_defect"' in script
    assert 'input.dataset.visualizationFilterType === "annotators"' in script
    assert 'input.dataset.visualizationFilterType === "label"' in script
    assert '$("visualizationFilterOverlay").classList.remove("hidden")' in script
    assert '$("visualizationFilterOverlay").classList.add("hidden")' in script


def test_v2_visualization_filter_options_use_v1_horizontal_bars():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'list.className = "labelOptions"' in script
    assert 'label.className = "labelOption"' in script
    assert 'list.className = "filterOptionList"' not in script
    assert 'label.className = "filterOption"' not in script
    assert '.labelOptions {' in styles
    assert '.labelOption input[type="checkbox"],\n.labelOption input[type="radio"] {' in styles
    assert '.labelOption input[type="checkbox"]:checked + span' in styles
    assert '.filterOptionList' not in styles
    assert '.filterOption {' not in styles


def test_v2_visualization_filter_bars_use_readable_fallback_options():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert "visualizationFilterOptionsOrDefault(options.mos, [1, 2, 3, 4, 5])" in script
    assert "visualizationFilterOptionsOrDefault(options.has_defect, [false, true])" in script
    assert 'renderVisualizationFilterGroup("标注者", options.annotators || [], "annotators", state.visualizationFilters.annotators, null, "暂无标注者")' in script
    assert "function visualizationFilterOptionsOrDefault" in script


def test_v2_visualization_filter_bars_are_not_clipped_by_fieldset_layout():
    styles = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "styles.css").read_text(encoding="utf-8")

    assert ".filterGroup {\n  display: block;" in styles
    assert "min-height: auto;" in styles
    assert ".filterGroup legend {\n  display: block;" in styles
    assert ".labelOptions {\n  display: inline-flex;" in styles
    assert "min-height: 32px;" in styles


def test_v2_frontend_exposes_annotation_import_action():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'data-action="import"' in script
    assert "async function importTaskAnnotations" in script
    assert "导入 JSONL 文件路径" in script
    assert 'api(`/api/tasks/${taskId}/import`' in script
    assert 'if (action === "import") importTaskAnnotations(taskId)' in script


def test_v2_frontend_drives_dedicated_sample_page():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")
    sample_template = (PROJECT_ROOT / "web" / "annotations_v2" / "templates" / "sample.html").read_text(encoding="utf-8")

    assert 'data-page="sample"' in sample_template
    assert 'id="selectAllSampleBtn"' in sample_template
    assert 'id="sampleBucketList"' in sample_template
    assert "openSamplePage" in script
    assert "reloadSampleBuckets" in script
    assert "renderSampleBuckets" in script
    assert "collectSampleSelections" in script
    assert "selectAllSampleBuckets" in script
    assert 'api(`/api/tasks/${state.taskId}/sample-buckets`' in script
    assert 'select_all: true' in script
    assert 'selections: collectSampleSelections()' in script
