import json
import shutil
from pathlib import Path

from scripts.label_pairs_jsonl import (
    get_output_json_path,
    label_pair_with_module,
    label_pairs_jsonl,
)


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


def test_label_pairs_jsonl_writes_labels_to_preserved_path_and_updates_existing_json() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_label_pairs"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        data_root = scratch / "data"
        original = data_root / "原始图片" / "shop" / "food.png"
        generated = data_root / "生成图片" / "shop" / "food_p1_方案_8484.jpg"
        output_dir = scratch / "labels"
        jsonl_path = data_root / "data.jsonl"
        out_json = output_dir / "生成图片" / "shop" / "food_p1_方案_8484.json"

        original.parent.mkdir(parents=True)
        generated.parent.mkdir(parents=True)
        out_json.parent.mkdir(parents=True)
        original.write_bytes(b"orig")
        generated.write_bytes(b"gen")
        out_json.write_text(
            json.dumps({"existing": True, "labels": {"old": "value"}}, ensure_ascii=False),
            encoding="utf-8",
        )
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

        def fake_labeler(original_path: Path, generated_path: Path) -> dict:
            return {
                "input_image": {"camera_angle": 45},
                "output_image": {"aesthetic_score": 4},
                "seen": [str(original_path), str(generated_path)],
            }

        stats = label_pairs_jsonl(
            jsonl_path=jsonl_path,
            output_dir=output_dir,
            input_root=data_root,
            label_func=fake_labeler,
        )

        assert stats == {"processed": 1, "skipped": 0, "failed": 0}
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["existing"] is True
        assert payload["src_image"] == r"原始图片\shop\food.png"
        assert payload["dst_image"] == r"生成图片\shop\food_p1_方案_8484.jpg"
        assert payload["original_image_path"] == str(original)
        assert payload["generated_image_path"] == str(generated)
        assert payload["labels"]["output_image"]["aesthetic_score"] == 4
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def test_label_pair_with_module_uses_two_image_labeler_private_pair_method() -> None:
    calls = {}

    class FakeClient:
        pass

    class FakeModule:
        def __init__(self, model):
            calls["model_override"] = model

        def _label_pair(self, client, model, original_image_path, generated_image_path):
            calls["client"] = client
            calls["model"] = model
            calls["original_image_path"] = original_image_path
            calls["generated_image_path"] = generated_image_path
            return {"ok": True}

    client = FakeClient()

    result = label_pair_with_module(
        "orig.jpg",
        "gen.jpg",
        client=client,
        model="vision-model",
        module_factory=FakeModule,
    )

    assert result == {"ok": True}
    assert calls == {
        "model_override": "vision-model",
        "client": client,
        "model": "vision-model",
        "original_image_path": "orig.jpg",
        "generated_image_path": "gen.jpg",
    }
