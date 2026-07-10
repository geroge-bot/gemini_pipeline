import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_migrate_state_rewrites_macos_and_windows_roots():
    from scripts.migrate_annotations_v2_paths import migrate_state

    state = {
        "tasks": [
            {
                "id": "task-1",
                "root_dir": "/Users/george/data/run",
                "jsonl_path": "/Users/george/data/run/pairs.jsonl",
                "data_dir": "D:\\annotations\\tasks\\task-1",
                "label_dir": "",
            }
        ]
    }

    migrated, changes = migrate_state(
        state,
        [
            ("/Users/george/data", "/srv/data"),
            ("D:\\annotations", "/srv/annotations"),
        ],
    )

    task = migrated["tasks"][0]
    assert task["root_dir"] == "/srv/data/run"
    assert task["jsonl_path"] == "/srv/data/run/pairs.jsonl"
    assert task["data_dir"] == "/srv/annotations/tasks/task-1"
    assert len(changes) == 3
    assert state["tasks"][0]["root_dir"] == "/Users/george/data/run"


def test_migrate_cli_is_dry_run_by_default_and_backs_up_on_apply(tmp_path, capsys):
    from scripts.migrate_annotations_v2_paths import main

    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-1",
                        "root_dir": "/old/images",
                        "jsonl_path": "/old/images/pairs.jsonl",
                        "data_dir": "/old/tasks/task-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["--state-path", str(state_path), "--map", "/old=/new"]) == 0
    assert json.loads(state_path.read_text(encoding="utf-8"))["tasks"][0]["root_dir"] == "/old/images"
    assert main(["--state-path", str(state_path), "--map", "/old=/new", "--apply"]) == 0

    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated["tasks"][0]["root_dir"] == "/new/images"
    assert len(list(tmp_path.glob("state.json.bak-*"))) == 1
    assert "change_count" in capsys.readouterr().out
