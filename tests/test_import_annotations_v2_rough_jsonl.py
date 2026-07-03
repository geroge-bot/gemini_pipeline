import gzip
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_workspace_tmp():
    path = Path("annotations_test_tmp") / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_v2_task(tmp_path):
    from web.annotations_v2.app import AnnotationV2Store

    source_jsonl = tmp_path / "task.jsonl"
    write_jsonl(
        source_jsonl,
        [
            {"src_image": "ori/a.jpg", "dst_image": "gen/a.jpg"},
            {"src_image": "ori/b.jpg", "dst_image": "gen/b.jpg"},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "rough target",
            "root_dir": str(tmp_path),
            "jsonl_path": str(source_jsonl),
            "rough": {"annotator_count": 2, "min_mos": 4, "require_no_defect": True},
        }
    )
    return store, task


def test_import_rough_jsonl_dry_run_matches_without_writing_records():
    from scripts.import_annotations_v2_rough_jsonl import import_rough_jsonl

    tmp_path = make_workspace_tmp()
    store, task = make_v2_task(tmp_path)
    import_jsonl = tmp_path / "import.jsonl"
    write_jsonl(
        import_jsonl,
        [
            {"原图": "ori/a.jpg", "生成图": "gen/a.jpg", "MOS评分": "3", "是否有质量问题": False, "评分人": "alice"},
            {"原图": "ori/missing.jpg", "生成图": "gen/missing.jpg", "MOS评分": "5", "是否有质量问题": False, "评分人": "bob"},
        ],
    )

    result = import_rough_jsonl(str(import_jsonl), task["id"], state_path=tmp_path / "state.json", apply=False)

    assert result["dry_run"] is True
    assert result["rows_seen"] == 2
    assert result["matched"] == 1
    assert result["unmatched"] == 1
    assert result["imported"] == 0
    assert store._read_records(store._require_task(task["id"])) == {}


def test_import_rough_jsonl_apply_writes_v2_rough_record_and_refreshes_summary():
    from scripts.import_annotations_v2_rough_jsonl import import_rough_jsonl

    tmp_path = make_workspace_tmp()
    store, task = make_v2_task(tmp_path)
    import_jsonl = tmp_path / "import.jsonl"
    write_jsonl(
        import_jsonl,
        [
            {"原图": "ori/a.jpg", "生成图": "gen/a.jpg", "MOS评分": "3", "是否有质量问题": True, "评分人": "alice"},
        ],
    )

    result = import_rough_jsonl(str(import_jsonl), "rough target", state_path=tmp_path / "state.json", apply=True)

    assert result["dry_run"] is False
    assert result["matched"] == 1
    assert result["imported"] == 1
    assert result["summary"]["rough_annotation_completed"] == 1
    assert result["summary"]["rough_rounds"][0] == {"round": 1, "completed": 1, "total": 2}
    records_dir = Path(task["data_dir"]) / "records"
    with gzip.open(records_dir / "0.json.gz", "rt", encoding="utf-8") as handle:
        record = json.load(handle)
    assert record["rough"]["username"] == "alice"
    assert record["rough"]["mos"] == 3
    assert record["rough"]["has_defect"] is True
    assert record["rough_annotations"][0]["username"] == "alice"
    assert record["rough_annotations"][0]["mos"] == 3
    summary = json.loads((Path(task["data_dir"]) / "summary.json").read_text(encoding="utf-8"))
    assert summary["stale"] is False
    assert summary["rough_annotation_completed"] == 1


def test_import_rough_jsonl_matches_task_name_after_trimming_whitespace():
    from scripts.import_annotations_v2_rough_jsonl import import_rough_jsonl

    tmp_path = make_workspace_tmp()
    store, task = make_v2_task(tmp_path)
    import_jsonl = tmp_path / "import.jsonl"
    write_jsonl(
        import_jsonl,
        [
            {"原图": "ori/a.jpg", "生成图": "gen/a.jpg", "MOS评分": "3", "是否有质量问题": False, "评分人": "alice"},
        ],
    )

    result = import_rough_jsonl(str(import_jsonl), " rough target ", state_path=tmp_path / "state.json", apply=True)

    assert result["matched"] == 1
    assert result["imported"] == 1
    record = store._read_record(store._require_task(task["id"]), 0)
    assert record["rough"]["username"] == "alice"


def test_import_rough_jsonl_missing_task_error_lists_available_tasks():
    from scripts.import_annotations_v2_rough_jsonl import import_rough_jsonl

    tmp_path = make_workspace_tmp()
    make_v2_task(tmp_path)
    import_jsonl = tmp_path / "import.jsonl"
    write_jsonl(import_jsonl, [])

    with pytest.raises(ValueError) as exc_info:
        import_rough_jsonl(str(import_jsonl), "missing task", state_path=tmp_path / "state.json", apply=False)

    message = str(exc_info.value)
    assert "missing task" in message
    assert str(tmp_path / "state.json") in message
    assert "rough target" in message


def test_import_rough_jsonl_uses_state_path_environment_by_default(monkeypatch):
    from scripts.import_annotations_v2_rough_jsonl import import_rough_jsonl, list_tasks

    tmp_path = make_workspace_tmp()
    store, task = make_v2_task(tmp_path)
    import_jsonl = tmp_path / "import.jsonl"
    write_jsonl(
        import_jsonl,
        [
            {"原图": "ori/a.jpg", "生成图": "gen/a.jpg", "MOS评分": "4", "是否有质量问题": False, "评分人": "env-user"},
        ],
    )
    monkeypatch.setenv("ANNOTATIONS_V2_STATE_PATH", str(tmp_path / "state.json"))

    tasks = list_tasks()
    result = import_rough_jsonl(str(import_jsonl), task["id"], apply=True)

    assert [item["id"] for item in tasks] == [task["id"]]
    assert result["matched"] == 1
    assert result["imported"] == 1
    record = store._read_record(store._require_task(task["id"]), 0)
    assert record["rough"]["username"] == "env-user"


def test_import_rough_jsonl_normalizes_absolute_paths_and_string_booleans():
    from scripts.import_annotations_v2_rough_jsonl import import_rough_jsonl

    tmp_path = make_workspace_tmp()
    store, task = make_v2_task(tmp_path)
    import_jsonl = tmp_path / "import.jsonl"
    write_jsonl(
        import_jsonl,
        [
            {
                "原图": str(tmp_path / "ori" / "b.jpg"),
                "生成图": str(tmp_path / "gen" / "b.jpg"),
                "MOS评分": "5",
                "是否有质量问题": "false",
                "评分人": "bob",
            },
        ],
    )

    result = import_rough_jsonl(str(import_jsonl), task["id"], state_path=tmp_path / "state.json", apply=True)

    assert result["matched"] == 1
    assert result["imported"] == 1
    record = store._read_record(store._require_task(task["id"]), 1)
    assert record["rough"]["username"] == "bob"
    assert record["rough"]["mos"] == 5
    assert record["rough"]["has_defect"] is False


def test_import_rough_jsonl_reports_invalid_rows_and_capacity_skips():
    from scripts.import_annotations_v2_rough_jsonl import import_rough_jsonl

    tmp_path = make_workspace_tmp()
    store, task = make_v2_task(tmp_path)
    # Fill the only allowed rough slot for item 0 in a task configured below.
    with store._state_lock:
        state = store._read_state()
        stored = store._find_task(state, task["id"])
        stored["rough"]["annotator_count"] = 1
        store._write_state(state)
    store.save_rough(task["id"], 0, {"username": "existing", "mos": 4, "has_defect": False})
    import_jsonl = tmp_path / "import.jsonl"
    write_jsonl(
        import_jsonl,
        [
            {"原图": "ori/a.jpg", "生成图": "gen/a.jpg", "MOS评分": "5", "是否有质量问题": False, "评分人": "new-user"},
            {"原图": "ori/b.jpg", "生成图": "gen/b.jpg", "MOS评分": "bad", "是否有质量问题": False, "评分人": "alice"},
        ],
    )

    result = import_rough_jsonl(str(import_jsonl), task["id"], state_path=tmp_path / "state.json", apply=True)

    assert result["rows_seen"] == 2
    assert result["matched"] == 1
    assert result["imported"] == 0
    assert result["capacity_skipped"] == 1
    assert result["invalid_rows"] == 1
    assert {error["line"] for error in result["errors"]} == {1, 2}
