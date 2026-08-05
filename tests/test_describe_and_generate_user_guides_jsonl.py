import json
import threading
import time
from pathlib import Path

from scripts.describe_and_generate_user_guides_jsonl import (
    describe_and_generate_user_guides_jsonl,
)


def _guide(name: str) -> list[dict]:
    return [
        {
            "场景描述": f"场景{name}",
            "摆盘描述": "把主菜移到画面中央。",
            "整体引导": "把主菜移到中央，放低手机拍摄，突出食物质感。",
            "调用标签": ["微调级", "通俗级"],
            "整体引导重写": "主菜居中摆放，降低手机靠近拍，表现丰富质感。",
        }
    ]


def _write_pairs(data_root: Path, count: int) -> Path:
    records = []
    for index in range(count):
        original = data_root / "src" / f"food_{index}.jpg"
        generated = data_root / "dst" / f"food_{index}.jpg"
        original.parent.mkdir(parents=True, exist_ok=True)
        generated.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"original")
        generated.write_bytes(b"generated")
        records.append(
            {
                "src_image": f"src/food_{index}.jpg",
                "dst_image": f"dst/food_{index}.jpg",
            }
        )

    jsonl_path = data_root / "pairs.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    return jsonl_path


def test_generates_description_before_user_guide_and_preserves_output_path(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    output_dir = tmp_path / "labels"
    jsonl_path = _write_pairs(data_root, 1)
    calls = []

    def describe_func(original_path: Path, generated_path: Path) -> dict:
        calls.append((generated_path.name, "description"))
        return {"question": "怎么拍好看？", "answer": "调整构图。"}

    def user_guide_func(original_path: Path, generated_path: Path) -> list[dict]:
        calls.append((generated_path.name, "user_guide"))
        return _guide(generated_path.stem)

    stats = describe_and_generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        output_dir=output_dir,
        input_root=data_root,
        describe_func=describe_func,
        user_guide_func=user_guide_func,
    )

    assert stats == {"processed": 1, "skipped": 0, "failed": 0}
    assert calls == [("food_0.jpg", "description"), ("food_0.jpg", "user_guide")]

    output_path = output_dir / "dst" / "food_0.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["description"]["question"] == "怎么拍好看？"
    assert payload["user_guide"] == _guide("food_0")
    assert payload["original_image_path"] == str(data_root / "src" / "food_0.jpg")
    assert payload["generated_image_path"] == str(data_root / "dst" / "food_0.jpg")


def test_processes_pairs_concurrently_but_keeps_each_pair_in_order(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_dir = tmp_path / "labels"
    jsonl_path = _write_pairs(data_root, 3)
    state_lock = threading.Lock()
    active_descriptions = 0
    max_active_descriptions = 0
    stages: dict[str, list[str]] = {}

    def describe_func(original_path: Path, generated_path: Path) -> dict:
        nonlocal active_descriptions, max_active_descriptions
        with state_lock:
            stages.setdefault(generated_path.name, []).append("description")
            active_descriptions += 1
            max_active_descriptions = max(max_active_descriptions, active_descriptions)
        time.sleep(0.1)
        with state_lock:
            active_descriptions -= 1
        return {"question": generated_path.name}

    def user_guide_func(original_path: Path, generated_path: Path) -> list[dict]:
        with state_lock:
            stages.setdefault(generated_path.name, []).append("user_guide")
        return _guide(generated_path.stem)

    stats = describe_and_generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        output_dir=output_dir,
        input_root=data_root,
        describe_func=describe_func,
        user_guide_func=user_guide_func,
        max_workers=2,
    )

    assert stats == {"processed": 3, "skipped": 0, "failed": 0}
    assert max_active_descriptions >= 2
    assert all(stage_order == ["description", "user_guide"] for stage_order in stages.values())


def test_checkpoints_description_and_resumes_only_missing_user_guide(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_dir = tmp_path / "labels"
    jsonl_path = _write_pairs(data_root, 1)
    description_calls = 0

    def describe_func(original_path: Path, generated_path: Path) -> dict:
        nonlocal description_calls
        description_calls += 1
        return {"question": "已生成的问题"}

    def failing_user_guide(original_path: Path, generated_path: Path) -> list[dict]:
        raise RuntimeError("temporary guide failure")

    first_stats = describe_and_generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        output_dir=output_dir,
        input_root=data_root,
        describe_func=describe_func,
        user_guide_func=failing_user_guide,
        max_workers=1,
    )

    output_path = output_dir / "dst" / "food_0.json"
    checkpoint = json.loads(output_path.read_text(encoding="utf-8"))
    assert first_stats == {"processed": 0, "skipped": 0, "failed": 1}
    assert checkpoint["description"]["question"] == "已生成的问题"
    assert "user_guide" not in checkpoint

    second_stats = describe_and_generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        output_dir=output_dir,
        input_root=data_root,
        describe_func=describe_func,
        user_guide_func=lambda original, generated: _guide(generated.stem),
        max_workers=1,
    )

    assert second_stats == {"processed": 1, "skipped": 0, "failed": 0}
    assert description_calls == 1
    resumed = json.loads(output_path.read_text(encoding="utf-8"))
    assert resumed["user_guide"] == _guide("food_0")


def test_skips_complete_existing_result_unless_overwrite_is_enabled(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_dir = tmp_path / "labels"
    jsonl_path = _write_pairs(data_root, 1)
    output_path = output_dir / "dst" / "food_0.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text(
        json.dumps(
            {
                "description": {"question": "已有问题"},
                "user_guide": _guide("existing"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    calls = []

    def describe_func(original_path: Path, generated_path: Path) -> dict:
        calls.append("description")
        return {"question": "新问题"}

    def user_guide_func(original_path: Path, generated_path: Path) -> list[dict]:
        calls.append("user_guide")
        return _guide("new")

    skipped_stats = describe_and_generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        output_dir=output_dir,
        input_root=data_root,
        describe_func=describe_func,
        user_guide_func=user_guide_func,
    )
    assert skipped_stats == {"processed": 0, "skipped": 1, "failed": 0}
    assert calls == []

    overwritten_stats = describe_and_generate_user_guides_jsonl(
        jsonl_path=jsonl_path,
        output_dir=output_dir,
        input_root=data_root,
        describe_func=describe_func,
        user_guide_func=user_guide_func,
        overwrite=True,
    )
    assert overwritten_stats == {"processed": 1, "skipped": 0, "failed": 0}
    assert calls == ["description", "user_guide"]
    overwritten = json.loads(output_path.read_text(encoding="utf-8"))
    assert overwritten["description"]["question"] == "新问题"
    assert overwritten["user_guide"] == _guide("new")
