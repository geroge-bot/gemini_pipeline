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


def test_transfer_annotations_matches_by_image_pair_and_merges_target_labels():
    from scripts.transfer_annotations import transfer_annotations
    from web.annotations.app import AnnotationStore

    tmp_path = make_workspace_tmp()
    source_jsonl = tmp_path / "source.jsonl"
    target_jsonl = tmp_path / "target.jsonl"
    target_labels = tmp_path / "labels"
    rows = [
        {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
        {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
    ]
    write_jsonl(source_jsonl, rows)
    write_jsonl(target_jsonl, list(reversed(rows)))
    write_json(target_labels / "src" / "a.json", {"labels": {"菜品种类": "中餐"}})
    write_json(target_labels / "dst" / "a.json", {"labels": {"美学评分": 5, "互动": "无互动"}})

    store = AnnotationStore(tmp_path / "state.json")
    source_task = store.create_task("202604-美食数据-空标注", str(tmp_path), str(source_jsonl), chunk_size=2)
    target_task = store.create_task(
        "202604-美食数据",
        str(tmp_path),
        str(target_jsonl),
        chunk_size=2,
        annotation_dir=str(target_labels),
    )
    source_subtask = store.assign_subtask(source_task["id"], "alice")
    store.save_annotation(
        source_task["id"],
        source_subtask["id"],
        item_index=0,
        username="alice",
        mos=4,
        tags={"输出图": {"人工标签": "保留"}},
    )

    stats = transfer_annotations(
        state_path=tmp_path / "state.json",
        source_task="202604-美食数据-空标注",
        target_task="202604-美食数据",
        apply=True,
    )

    refreshed = AnnotationStore(tmp_path / "state.json").get_task(target_task["id"])
    annotations = refreshed["annotations"]
    assert stats["transferred"] == 1
    assert stats["unmatched"] == 0
    assert stats["skipped_existing"] == 0
    assert set(annotations.keys()) == {"1"}
    assert annotations["1"]["item_index"] == 1
    assert annotations["1"]["mos"] == 4
    assert annotations["1"]["username"] == "alice"
    assert annotations["1"]["tags"] == {
        "输入图": {"菜品种类": "中餐"},
        "输出图": {"美学评分": 5, "互动": "无互动", "人工标签": "保留"},
    }
    assert refreshed["annotation_count"] == 1
    target_summary = next(task for task in AnnotationStore(tmp_path / "state.json").list_tasks() if task["id"] == target_task["id"])
    assert target_summary["assigned_count"] == 1


def test_transfer_annotations_skips_existing_target_annotations_by_default():
    from scripts.transfer_annotations import transfer_annotations
    from web.annotations.app import AnnotationStore

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationStore(tmp_path / "state.json")
    source_task = store.create_task("source", str(tmp_path), str(jsonl_path), chunk_size=1)
    target_task = store.create_task("target", str(tmp_path), str(jsonl_path), chunk_size=1)
    source_subtask = store.assign_subtask(source_task["id"], "alice")
    target_subtask = store.assign_subtask(target_task["id"], "bob")
    store.save_annotation(source_task["id"], source_subtask["id"], 0, "alice", 5, {"new": True})
    store.save_annotation(target_task["id"], target_subtask["id"], 0, "bob", 2, {"old": True})

    stats = transfer_annotations(
        state_path=tmp_path / "state.json",
        source_task="source",
        target_task="target",
        apply=True,
    )

    refreshed = AnnotationStore(tmp_path / "state.json").get_task(target_task["id"])
    assert stats["transferred"] == 0
    assert stats["skipped_existing"] == 1
    assert refreshed["annotations"]["0"]["mos"] == 2
    assert refreshed["annotations"]["0"]["tags"] == {"old": True}


def test_transfer_annotations_matches_paths_with_different_workspace_prefixes():
    from scripts.transfer_annotations import transfer_annotations
    from web.annotations.app import AnnotationStore

    tmp_path = make_workspace_tmp()
    source_root = tmp_path / "data_tmp"
    target_root = tmp_path / "target"
    source_jsonl = tmp_path / "source.jsonl"
    target_jsonl = tmp_path / "target.jsonl"
    write_jsonl(
        source_jsonl,
        [
            {
                "src_image": str(source_root / "原始图片" / "shop" / "dish.jpg"),
                "dst_image": str(source_root / "生成图片" / "shop" / "dish_p1.jpg"),
            }
        ],
    )
    write_jsonl(
        target_jsonl,
        [
            {
                "src_image": "原始图片/shop/dish.jpg",
                "dst_image": "生成图片/shop/dish_p1.jpg",
            }
        ],
    )

    store = AnnotationStore(tmp_path / "state.json")
    source_task = store.create_task("source", str(source_root), str(source_jsonl), chunk_size=1)
    target_task = store.create_task("target", str(target_root), str(target_jsonl), chunk_size=1)
    source_subtask = store.assign_subtask(source_task["id"], "alice")
    store.save_annotation(source_task["id"], source_subtask["id"], 0, "alice", 5, {"输出图": {"人工": "是"}})

    stats = transfer_annotations(tmp_path / "state.json", "source", "target", apply=True)

    refreshed = AnnotationStore(tmp_path / "state.json").get_task(target_task["id"])
    assert stats["transferred"] == 1
    assert stats["unmatched"] == 0
    assert refreshed["annotations"]["0"]["mos"] == 5


def test_transfer_annotations_reads_source_and_target_from_separate_state_files():
    from scripts.transfer_annotations import transfer_annotations
    from web.annotations.app import AnnotationStore

    tmp_path = make_workspace_tmp()
    source_data = tmp_path / "source_annotations_data"
    target_data = tmp_path / "target_annotations_data"
    source_jsonl = tmp_path / "source.jsonl"
    target_jsonl = tmp_path / "target.jsonl"
    rows = [{"src_image": "原始图片/a.jpg", "dst_image": "生成图片/a.jpg"}]
    write_jsonl(source_jsonl, rows)
    write_jsonl(target_jsonl, rows)

    source_store = AnnotationStore(source_data / "state.json")
    target_store = AnnotationStore(target_data / "state.json")
    source_task = source_store.create_task("source", str(tmp_path), str(source_jsonl), chunk_size=1)
    target_task = target_store.create_task("target", str(tmp_path), str(target_jsonl), chunk_size=1)
    source_subtask = source_store.assign_subtask(source_task["id"], "alice")
    source_store.save_annotation(source_task["id"], source_subtask["id"], 0, "alice", 4, {"输出图": {"人工": "源"}})

    stats = transfer_annotations(
        state_path=target_data / "state.json",
        source_state_path=source_data / "state.json",
        source_task="source",
        target_task="target",
        apply=True,
    )

    refreshed_source = AnnotationStore(source_data / "state.json").get_task(source_task["id"])
    refreshed_target = AnnotationStore(target_data / "state.json").get_task(target_task["id"])
    assert stats["transferred"] == 1
    assert stats["unmatched"] == 0
    assert refreshed_source["annotation_count"] == 1
    assert refreshed_target["annotation_count"] == 1
    assert refreshed_target["annotations"]["0"]["mos"] == 4
