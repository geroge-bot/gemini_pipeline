import json
import shutil
import threading
import time
from pathlib import Path

from scripts.describe_pairs_jsonl import (
    DEFAULT_MAX_WORKERS,
    describe_pairs_jsonl,
    get_output_json_path,
)
from pipeline.modules.description import (
    ANSWER_PROMPTS,
    DEFAULT_ANSWER_PROMPT_KEY,
    DescriptionModule,
    _build_answer_prompt,
)
from pipeline.modules.prompt_backup import food_prompt_0722


def test_food_0722_is_default_and_legacy_prompt_is_retained() -> None:
    messages = _build_answer_prompt("persona", "question", "a", "b")

    assert DEFAULT_ANSWER_PROMPT_KEY == "food_0722"
    assert set(ANSWER_PROMPTS) == {"food_legacy", "food_0722"}
    assert messages[0]["content"] == food_prompt_0722.strip()
    assert DescriptionModule(answer_prompt_key="food_legacy").answer_prompt_key == "food_legacy"


def test_get_output_json_path_preserves_generated_relative_path() -> None:
    output_dir = Path("labels")
    generated_relative = Path(
        r"生成图片\202601-咖啡厅\A Billion Coffee艾彼咖啡"
        r"\ABC肉食主义拼盘_037_13362_p1_方案_8484.jpg"
    )

    assert get_output_json_path(output_dir, generated_relative) == (
        output_dir
        / r"生成图片\202601-咖啡厅\A Billion Coffee艾彼咖啡"
        / "ABC肉食主义拼盘_037_13362_p1_方案_8484.json"
    )


def test_describe_pairs_jsonl_writes_description_to_preserved_path() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_describe_pairs"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        data_root = scratch / "data"
        original = data_root / "原始图片" / "shop" / "food.png"
        generated = data_root / "生成图片" / "shop" / "food_p1_方案_8484.jpg"
        output_dir = scratch / "labels"
        jsonl_path = data_root / "data.jsonl"

        original.parent.mkdir(parents=True)
        generated.parent.mkdir(parents=True)
        original.write_bytes(b"orig")
        generated.write_bytes(b"gen")
        jsonl_path.write_text(
            json.dumps(
                {
                    "src_image": r"原始图片\shop\food.png",
                    "dst_image": r"生成图片\shop\food_p1_方案_8484.jpg",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        def fake_describer(original_path: Path, generated_path: Path) -> dict:
            return {
                "persona": "",
                "question": "怎么拍好看？",
                "answer": "{}",
                "conversation_history": [],
                "seen": [str(original_path), str(generated_path)],
            }

        stats = describe_pairs_jsonl(
            jsonl_path=jsonl_path,
            output_dir=output_dir,
            input_root=data_root,
            describe_func=fake_describer,
        )

        out_json = output_dir / "生成图片" / "shop" / "food_p1_方案_8484.json"
        assert stats == {"processed": 1, "skipped": 0, "failed": 0}
        assert out_json.exists()

        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["src_image"] == r"原始图片\shop\food.png"
        assert payload["dst_image"] == r"生成图片\shop\food_p1_方案_8484.jpg"
        assert payload["description"]["question"] == "怎么拍好看？"
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def test_describe_pairs_jsonl_uses_parallel_workers() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_describe_pairs_parallel"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        data_root = scratch / "data"
        output_dir = scratch / "labels"
        jsonl_path = data_root / "data.jsonl"
        original_dir = data_root / "原始图片" / "shop"
        generated_dir = data_root / "生成图片" / "shop"

        original_dir.mkdir(parents=True)
        generated_dir.mkdir(parents=True)

        records = []
        for idx in range(3):
            original_name = f"food_{idx}.png"
            generated_name = f"food_{idx}_p1_方案_8484.jpg"
            (original_dir / original_name).write_bytes(b"orig")
            (generated_dir / generated_name).write_bytes(b"gen")
            records.append(
                {
                    "src_image": fr"原始图片\shop\{original_name}",
                    "dst_image": fr"生成图片\shop\{generated_name}",
                }
            )

        jsonl_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )

        state_lock = threading.Lock()
        current_running = 0
        max_running = 0

        def fake_describer(original_path: Path, generated_path: Path) -> dict:
            nonlocal current_running, max_running
            with state_lock:
                current_running += 1
                max_running = max(max_running, current_running)
            time.sleep(0.15)
            with state_lock:
                current_running -= 1
            return {
                "persona": "",
                "question": "怎么拍好看？",
                "answer": "{}",
                "conversation_history": [],
                "seen": [str(original_path), str(generated_path)],
            }

        stats = describe_pairs_jsonl(
            jsonl_path=jsonl_path,
            output_dir=output_dir,
            input_root=data_root,
            describe_func=fake_describer,
            max_workers=2,
        )

        assert stats == {"processed": 3, "skipped": 0, "failed": 0}
        assert max_running >= 2
        assert DEFAULT_MAX_WORKERS == 50
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def test_describe_pairs_jsonl_randomly_samples_reproducibly() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_describe_pairs_sample"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        data_root = scratch / "data"
        output_dir = scratch / "labels"
        jsonl_path = data_root / "data.jsonl"
        data_root.mkdir(parents=True)
        records = [
            {"src_image": f"src/{idx}.jpg", "dst_image": f"dst/{idx}.jpg"}
            for idx in range(10)
        ]
        jsonl_path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )

        seen: list[str] = []

        def fake_describer(original_path: Path, generated_path: Path) -> dict:
            seen.append(generated_path.name)
            return {"answer": "{}"}

        stats = describe_pairs_jsonl(
            jsonl_path=jsonl_path,
            output_dir=output_dir,
            input_root=data_root,
            describe_func=fake_describer,
            sample_size=3,
            random_seed=7,
            max_workers=1,
        )

        assert stats == {"processed": 3, "skipped": 0, "failed": 0}
        assert seen == ["5.jpg", "2.jpg", "6.jpg"]
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
