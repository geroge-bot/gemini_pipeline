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


def test_batch_migrates_v1_task_annotations_labels_and_qc_history_to_v2_gzip_records():
    from scripts.migrate_annotations_v1_tasks_to_v2 import migrate_v1_tasks_to_v2
    from web.annotations.app import AnnotationStore
    from web.annotations.label_options import LABEL_OPTION_GROUPS
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "pairs.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg"},
        ],
    )
    group_name = LABEL_OPTION_GROUPS[0]["name"]
    dimension_name = LABEL_OPTION_GROUPS[0]["dimensions"][0]["name"]

    v1_store = AnnotationStore(tmp_path / "v1_state.json")
    v1_task = v1_store.create_task("v1 food", str(tmp_path), str(jsonl_path), chunk_size=1)
    subtask = v1_store.assign_subtask(v1_task["id"], "alice")
    item_index = subtask["item_indexes"][0]
    v1_store.save_annotation(task_id=v1_task["id"], subtask_id=subtask["id"], item_index=item_index, username="alice", mos=5, tags={group_name: {dimension_name: 10}})
    v1_store.save_quality_check(task_id=v1_task["id"], item_index=item_index, username="bob", mos=4, tags={group_name: {dimension_name: 45}})

    stats = migrate_v1_tasks_to_v2(
        v1_state_path=tmp_path / "v1_state.json",
        v2_state_path=tmp_path / "v2_state.json",
        task_refs=["all"],
        apply=True,
    )

    assert stats["tasks_seen"] == 1
    assert stats["tasks_migrated"] == 1
    assert stats["annotations_seen"] == 1
    assert stats["annotations_migrated"] == 1
    assert stats["unmatched_annotations"] == 0
    assert stats["dry_run"] == 0

    v2_store = AnnotationV2Store(tmp_path / "v2_state.json")
    [v2_task] = v2_store.list_tasks()
    assert v2_task["name"] == "v1 food"
    assert v2_task["source_v1_task_id"] == v1_task["id"]
    assert (Path(v2_task["data_dir"]) / "records" / f"{item_index}.json.gz").exists()

    total, rows = v2_store.get_unified_results(v2_task["id"], offset=0, limit=2)
    migrated_row = next(row for row in rows if row["item_index"] == item_index)

    assert total == 2
    assert migrated_row["rough"]["username"] == "alice"
    assert migrated_row["fine"]["username"] == "alice"
    assert migrated_row["sampled"] is True
    assert migrated_row["label"]["username"] == "bob"
    assert migrated_row["label"]["labels"] == {group_name: {dimension_name: 45}}
    assert [revision["username"] for revision in migrated_row["label_revisions"]] == ["alice", "bob"]
    assert migrated_row["label_revisions"][0]["source"] == "annotations_v1_annotation"
    assert migrated_row["label_revisions"][1]["source"] == "annotations_v1_qc"
    assert migrated_row["effective_labels"] == {group_name: {dimension_name: 45}}


def test_batch_migration_dry_run_does_not_create_v2_tasks_and_apply_skips_existing_source():
    from scripts.migrate_annotations_v1_tasks_to_v2 import migrate_v1_tasks_to_v2
    from web.annotations.app import AnnotationStore
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "pairs.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    v1_store = AnnotationStore(tmp_path / "v1_state.json")
    v1_task = v1_store.create_task("v1 dry run", str(tmp_path), str(jsonl_path), chunk_size=1)

    dry_stats = migrate_v1_tasks_to_v2(
        v1_state_path=tmp_path / "v1_state.json",
        v2_state_path=tmp_path / "v2_state.json",
        task_refs=[v1_task["id"]],
        apply=False,
    )

    assert dry_stats["tasks_seen"] == 1
    assert dry_stats["tasks_migrated"] == 0
    assert dry_stats["dry_run"] == 1
    assert AnnotationV2Store(tmp_path / "v2_state.json").list_tasks() == []

    first_apply = migrate_v1_tasks_to_v2(
        v1_state_path=tmp_path / "v1_state.json",
        v2_state_path=tmp_path / "v2_state.json",
        task_refs=[v1_task["id"]],
        apply=True,
    )
    second_apply = migrate_v1_tasks_to_v2(
        v1_state_path=tmp_path / "v1_state.json",
        v2_state_path=tmp_path / "v2_state.json",
        task_refs=[v1_task["id"]],
        apply=True,
    )

    assert first_apply["tasks_migrated"] == 1
    assert second_apply["tasks_migrated"] == 0
    assert second_apply["tasks_skipped_existing"] == 1
    assert len(AnnotationV2Store(tmp_path / "v2_state.json").list_tasks()) == 1
