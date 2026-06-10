import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.repair_annotations_v2_json import main


def test_repair_annotations_v2_json_dry_run_reports_extra_data_without_writing(tmp_path, capsys):
    path = tmp_path / "records.json"
    original = '{"0": {"rough": {"mos": 5}}}\n{"leftover": true}\n'
    path.write_text(original, encoding="utf-8")

    exit_code = main([str(path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "RECOVERABLE extra-data" in output
    assert path.read_text(encoding="utf-8") == original


def test_repair_annotations_v2_json_apply_repairs_and_backs_up_extra_data(tmp_path):
    path = tmp_path / "items.json"
    backup_dir = tmp_path / "backup"
    original = '[{"item_index": 0}]\n{"partial": "stale"}\n'
    path.write_text(original, encoding="utf-8")

    exit_code = main(["--apply", "--backup-dir", str(backup_dir), str(path)])

    assert exit_code == 0
    assert json.loads(path.read_text(encoding="utf-8")) == [{"item_index": 0}]
    backups = list(backup_dir.rglob("items.json.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_repair_annotations_v2_json_leaves_unrecoverable_file_unchanged(tmp_path, capsys):
    path = tmp_path / "state.json"
    original = '{"tasks": ['
    path.write_text(original, encoding="utf-8")

    exit_code = main(["--apply", str(path)])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "BROKEN unrecoverable" in output
    assert path.read_text(encoding="utf-8") == original
