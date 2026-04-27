import json
import shutil
from pathlib import Path

from scripts.describe_and_label_pairs_jsonl import describe_and_label_pairs_jsonl


def test_describe_and_label_pairs_jsonl_writes_description_and_labels_to_preserved_path() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_describe_and_label_pairs"
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
            json.dumps({"existing": True}, ensure_ascii=False),
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

        call_order = []

        def fake_describer(original_path: Path, generated_path: Path) -> dict:
            call_order.append("description")
            return {
                "persona": "摄影小白",
                "question": "怎么拍得好看些？",
                "answer": "画面更有食欲。",
                "conversation_history": [],
                "seen": [str(original_path), str(generated_path)],
            }

        def fake_labeler(original_path: Path, generated_path: Path) -> dict:
            call_order.append("labels")
            return {
                "input_image": {"camera_angle": 45},
                "output_image": {"aesthetic_score": 4},
            }

        stats = describe_and_label_pairs_jsonl(
            jsonl_path=jsonl_path,
            output_dir=output_dir,
            input_root=data_root,
            describe_func=fake_describer,
            label_func=fake_labeler,
        )

        assert stats == {"processed": 1, "skipped": 0, "failed": 0}
        assert call_order == ["description", "labels"]

        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["existing"] is True
        assert payload["src_image"] == r"原始图片\shop\food.png"
        assert payload["dst_image"] == r"生成图片\shop\food_p1_方案_8484.jpg"
        assert payload["original_image_path"] == str(original)
        assert payload["generated_image_path"] == str(generated)
        assert payload["description"]["question"] == "怎么拍得好看些？"
        assert payload["labels"]["output_image"]["aesthetic_score"] == 4
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def test_describe_and_label_pairs_jsonl_reuses_existing_fields_and_only_generates_missing() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_describe_and_label_pairs_cache"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        data_root = scratch / "data"
        output_dir = scratch / "labels"
        jsonl_path = data_root / "data.jsonl"

        records = [
            {
                "src_image": r"原始图片\shop\both.png",
                "dst_image": r"生成图片\shop\both_p1.jpg",
            },
            {
                "src_image": r"原始图片\shop\only_description.png",
                "dst_image": r"生成图片\shop\only_description_p1.jpg",
            },
            {
                "src_image": r"原始图片\shop\only_labels.png",
                "dst_image": r"生成图片\shop\only_labels_p1.jpg",
            },
        ]

        for record in records:
            original = data_root / record["src_image"]
            generated = data_root / record["dst_image"]
            original.parent.mkdir(parents=True, exist_ok=True)
            generated.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(b"orig")
            generated.write_bytes(b"gen")

        existing_payloads = {
            "both_p1": {
                "description": {"question": "已有问题"},
                "labels": {"output_image": {"aesthetic_score": 5}},
            },
            "only_description_p1": {
                "description": {"question": "已有问题"},
            },
            "only_labels_p1": {
                "labels": {"output_image": {"aesthetic_score": 5}},
            },
        }
        for stem, payload in existing_payloads.items():
            out_json = output_dir / "生成图片" / "shop" / f"{stem}.json"
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        jsonl_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )

        calls = {"description": 0, "labels": 0}

        def fake_describer(original_path: Path, generated_path: Path) -> dict:
            calls["description"] += 1
            return {"question": f"新问题:{generated_path.name}"}

        def fake_labeler(original_path: Path, generated_path: Path) -> dict:
            calls["labels"] += 1
            return {"output_image": {"aesthetic_score": 3, "name": generated_path.name}}

        stats = describe_and_label_pairs_jsonl(
            jsonl_path=jsonl_path,
            output_dir=output_dir,
            input_root=data_root,
            describe_func=fake_describer,
            label_func=fake_labeler,
        )

        assert stats == {"processed": 2, "skipped": 1, "failed": 0}
        assert calls == {"description": 1, "labels": 1}

        both = json.loads(
            (output_dir / "生成图片" / "shop" / "both_p1.json").read_text(encoding="utf-8")
        )
        assert both["description"]["question"] == "已有问题"
        assert both["labels"]["output_image"]["aesthetic_score"] == 5

        only_description = json.loads(
            (output_dir / "生成图片" / "shop" / "only_description_p1.json").read_text(
                encoding="utf-8"
            )
        )
        assert only_description["description"]["question"] == "已有问题"
        assert only_description["labels"]["output_image"]["name"] == "only_description_p1.jpg"

        only_labels = json.loads(
            (output_dir / "生成图片" / "shop" / "only_labels_p1.json").read_text(
                encoding="utf-8"
            )
        )
        assert only_labels["description"]["question"] == "新问题:only_labels_p1.jpg"
        assert only_labels["labels"]["output_image"]["aesthetic_score"] == 5
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
