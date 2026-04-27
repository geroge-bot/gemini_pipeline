import json
import shutil
from pathlib import Path

from scripts.export_pairs_jsonl import build_pairs_jsonl


def test_build_pairs_jsonl_exports_relative_pair_paths() -> None:
    scratch = Path(__file__).resolve().parent / "_scratch_export_pairs"
    if scratch.exists():
        shutil.rmtree(scratch)

    try:
        input_dir = scratch / r"整理后"
        original_dir = input_dir / r"原始图片" / r"202601-咖啡厅" / r"A Billion Coffee艾彼咖啡"
        generated_dir = input_dir / r"生成图片" / r"202601-咖啡厅" / r"A Billion Coffee艾彼咖啡"
        output_jsonl = scratch / "pairs.jsonl"

        original_dir.mkdir(parents=True, exist_ok=True)
        generated_dir.mkdir(parents=True, exist_ok=True)

        (original_dir / r"ABC肉食主义拼盘_037_13362.png").write_bytes(b"orig")
        (generated_dir / r"ABC肉食主义拼盘_037_13362_p1_方案_8484.jpg").write_bytes(b"gen")
        (generated_dir / r"ABC肉食主义拼盘_037_13362_p1_方案_8484.json").write_text("{}", encoding="utf-8")

        (generated_dir / r"orphan_001_11111_p1_方案_9999.jpg").write_bytes(b"orphan")

        stats = build_pairs_jsonl(input_dir, output_jsonl)

        assert stats["pairs"] == 1
        assert stats["missing_originals"] == 1

        lines = output_jsonl.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record == {
            "src_image": r"原始图片\202601-咖啡厅\A Billion Coffee艾彼咖啡\ABC肉食主义拼盘_037_13362.png",
            "dst_image": r"生成图片\202601-咖啡厅\A Billion Coffee艾彼咖啡\ABC肉食主义拼盘_037_13362_p1_方案_8484.jpg",
        }
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
