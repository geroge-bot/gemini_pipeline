import json
from pathlib import Path

from scripts import generate_user_guides_jsonl as script


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def _guide(label: str) -> dict:
    return {
        "场景描述": f"场景{label}",
        "整体引导": f"整体引导{label}",
        "摆盘描述": f"摆盘描述{label}",
    }


def test_generate_user_guides_jsonl_writes_checkpoints(tmp_path: Path, monkeypatch) -> None:
    jsonl_path = tmp_path / "pairs.jsonl"
    output_path = tmp_path / "pairs_out.jsonl"
    records = [
        {"input_image": "a.jpg", "output_image": "a_out.jpg"},
        {"input_image": "b.jpg", "output_image": "b_out.jpg"},
    ]
    _write_jsonl(jsonl_path, records)

    checkpoint_counts = []
    real_write_checkpoint = script.write_jsonl_checkpoint

    def guide_func(original_path: Path, generated_path: Path) -> dict:
        return _guide(generated_path.stem)

    def spy_write_checkpoint(**kwargs):
        checkpoint_counts.append(
            sum("用户指引" in record for _line_no, record in kwargs["records"])
        )
        return real_write_checkpoint(**kwargs)

    monkeypatch.setattr(script, "write_jsonl_checkpoint", spy_write_checkpoint)

    stats = script.generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        output_path=output_path,
        guide_func=guide_func,
        max_workers=1,
        checkpoint_interval=1,
        backup=False,
    )

    assert stats == {"processed": 2, "skipped": 0, "failed": 0}
    assert checkpoint_counts == [1, 2, 2]
    written = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert written[0]["用户指引"] == _guide("a_out")
    assert written[1]["用户指引"] == _guide("b_out")


def test_generate_user_guides_jsonl_resume_skips_success_and_retries_errors(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "pairs.jsonl"
    records = [
        {
            "input_image": "done.jpg",
            "output_image": "done_out.jpg",
            "用户指引": _guide("done"),
        },
        {
            "input_image": "retry.jpg",
            "output_image": "retry_out.jpg",
            "用户指引": {"error": "previous failure"},
        },
        {"input_image": "new.jpg", "output_image": "new_out.jpg"},
    ]
    _write_jsonl(jsonl_path, records)

    calls = []

    def guide_func(original_path: Path, generated_path: Path) -> dict:
        calls.append(generated_path.name)
        return _guide(generated_path.stem)

    stats = script.generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        guide_func=guide_func,
        max_workers=1,
        checkpoint_interval=1,
        backup=False,
    )

    assert stats == {"processed": 2, "skipped": 1, "failed": 0}
    assert calls == ["retry_out.jpg", "new_out.jpg"]
    written = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert written[0]["用户指引"] == _guide("done")
    assert written[1]["用户指引"] == _guide("retry_out")
    assert written[2]["用户指引"] == _guide("new_out")
