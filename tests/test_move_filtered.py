import json
import shutil
from pathlib import Path

from scripts.move_filtered import copy_keep_generated_pair, parse_generated_image_name


def write_critique_log(path: Path, level: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "--- Plan 1 ---\n"
            "[结果]：Yes\n"
            "[错误编号]：1\n"
            f"[错误等级]：{level}\n"
            "[原因]：demo\n"
        ),
        encoding="utf-8",
    )


def test_copy_keep_generated_pair_filters_and_updates_json() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_move_filtered"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        input_dir = scratch / r"过滤后数据"
        critique_log_dir = scratch / r"过滤后质检" / r"critique_log"
        target_dir = scratch / "target"

        rel_dir = Path(r"202601-咖啡厅2") / r"333Cafe美领馆店"
        source_rel_dir = input_dir / rel_dir

        (source_rel_dir / "a_001_11111.png").parent.mkdir(parents=True, exist_ok=True)
        (source_rel_dir / "a_001_11111.png").write_bytes(b"orig-a")
        (source_rel_dir / "a_001_11111_p1_2222.jpg").write_bytes(b"gen-a")
        (source_rel_dir / "a_001_11111_p1_2222.json").write_text(
            json.dumps({"foo": "bar"}, ensure_ascii=False),
            encoding="utf-8",
        )
        write_critique_log(
            critique_log_dir / rel_dir / "a_001_11111_p1_critique_log_2222.txt",
            r"无",
        )

        (source_rel_dir / "b_002_22222.png").write_bytes(b"orig-b")
        (source_rel_dir / "b_002_22222_p1_3333.jpg").write_bytes(b"gen-b")
        write_critique_log(
            critique_log_dir / rel_dir / "b_002_22222_p1_critique_log_3333.txt",
            r"严重",
        )

        (source_rel_dir / "c_003_33333.png").write_bytes(b"orig-c")
        (source_rel_dir / "c_003_33333_p1_4444.jpg").write_bytes(b"gen-c")
        write_critique_log(
            critique_log_dir / rel_dir / "c_003_33333_p1_critique_log_4444.txt",
            r"无",
        )

        stats = copy_keep_generated_pair(input_dir, critique_log_dir, target_dir)

        assert stats["copied_generated"] == 2
        assert stats["copied_original"] == 2
        assert stats["skipped_by_validation"] == 1

        accepted_original = target_dir / r"原始图片" / rel_dir / "a_001_11111.png"
        rejected_original = target_dir / r"原始图片" / rel_dir / "b_002_22222.png"
        accepted_generated = target_dir / r"生成图片" / rel_dir / "a_001_11111_p1_2222.jpg"
        rejected_generated = target_dir / r"生成图片" / rel_dir / "b_002_22222_p1_3333.jpg"
        created_generated = target_dir / r"生成图片" / rel_dir / "c_003_33333_p1_4444.jpg"

        assert accepted_original.exists()
        assert not rejected_original.exists()
        assert accepted_generated.exists()
        assert not rejected_generated.exists()
        assert created_generated.exists()

        accepted_json = target_dir / r"生成图片" / rel_dir / "a_001_11111_p1_2222.json"
        created_json = target_dir / r"生成图片" / rel_dir / "c_003_33333_p1_4444.json"

        accepted_json_data = json.loads(accepted_json.read_text(encoding="utf-8"))
        created_json_data = json.loads(created_json.read_text(encoding="utf-8"))

        assert accepted_json_data["foo"] == "bar"
        assert accepted_json_data["validation"]["level"] == r"无错误"
        assert created_json_data == {
            "validation": {
                "has_error": True,
                "error_ids": "1",
                "level": r"无错误",
                "reason": "demo",
                "raw_output": "[结果]：Yes\n[错误编号]：1\n[错误等级]：无\n[原因]：demo",
            }
        }
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def test_parse_generated_image_name_supports_labelled_generated_files() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_move_filtered_parse"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        image_path = scratch / r"A Billion Coffee艾彼咖啡" / r"ABC肉食主义拼盘_037_13362_p1_方案_8484.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"x")

        parsed = parse_generated_image_name(image_path)

        assert parsed == {
            "seed": "8484",
            "plan_num": 1,
            "original_stem": r"ABC肉食主义拼盘_037_13362",
            "log_stem": r"ABC肉食主义拼盘_037_13362_p1",
        }
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
