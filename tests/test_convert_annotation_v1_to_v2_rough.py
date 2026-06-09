import json
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_workspace_tmp():
    path = Path("annotations_test_tmp") / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path):
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_convert_v1_annotations_to_v2_rough_matches_paths_and_defaults_no_quality_issue():
    from scripts.convert_annotation_v1_to_v2_rough import convert_annotation_v1_to_v2_rough
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    v1_path = tmp_path / "v1_filtered.jsonl"
    v2_path = tmp_path / "v2_unfiltered.jsonl"
    output_path = tmp_path / "v2_with_rough.jsonl"
    write_jsonl(
        v1_path,
        [
            {
                "src_image": str(tmp_path / "old" / "原始图片" / "shop" / "a.jpg"),
                "dst_image": str(tmp_path / "old" / "生成图片" / "shop" / "a_p1.jpg"),
                "mos": 4,
                "username": "alice",
                "tags": {},
                "updated_at": 123.0,
            }
        ],
    )
    write_jsonl(
        v2_path,
        [
            {"item_index": 0, "src_image": "原始图片/shop/a.jpg", "dst_image": "生成图片/shop/a_p1.jpg"},
            {"item_index": 1, "src_image": "原始图片/shop/b.jpg", "dst_image": "生成图片/shop/b_p1.jpg"},
        ],
    )

    stats = convert_annotation_v1_to_v2_rough(v1_path, v2_path, output_path)

    assert stats == {
        "v1_rows": 1,
        "v2_rows": 2,
        "matched": 1,
        "unmatched_v1": 0,
        "skipped_existing_rough": 0,
        "duplicate_v1_pairs": 0,
        "updated_v2_rows": 1,
    }
    rows = read_jsonl(output_path)
    assert rows[0]["rough_annotations"] == [
        {
            "username": "alice",
            "mos": 4,
            "has_defect": False,
            "primary_issue": "",
            "issues": [],
            "other_issue": "",
            "note": "",
            "updated_at": 123.0,
        }
    ]
    assert "rough_annotations" not in rows[1]

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(v2_path)})
    result = store.import_annotations_jsonl(task["id"], output_path)
    assert result["imported_count"] == 1
    assert store.summary(task["id"])["rough_completed"] == 1


def test_convert_v1_annotations_to_v2_rough_maps_quality_score_to_defect_issue():
    from scripts.convert_annotation_v1_to_v2_rough import convert_annotation_v1_to_v2_rough

    tmp_path = make_workspace_tmp()
    v1_path = tmp_path / "v1_filtered.jsonl"
    v2_path = tmp_path / "v2_unfiltered.jsonl"
    output_path = tmp_path / "v2_with_rough.jsonl"
    write_jsonl(
        v1_path,
        [
            {
                "src_image": "原始图片/a.jpg",
                "dst_image": "生成图片/a.jpg",
                "mos": "3",
                "annotator": "bob",
                "tags": {
                    "质量维度评分": 2,
                    "质量问题": ["主体问题", "文字问题"],
                    "备注": "需要复核",
                },
            }
        ],
    )
    write_jsonl(v2_path, [{"src_image": "原始图片/a.jpg", "dst_image": "生成图片/a.jpg"}])

    stats = convert_annotation_v1_to_v2_rough(
        v1_path,
        v2_path,
        output_path,
        quality_pass_min=4,
    )

    assert stats["matched"] == 1
    rough = read_jsonl(output_path)[0]["rough_annotations"][0]
    assert rough["username"] == "bob"
    assert rough["mos"] == 3
    assert rough["has_defect"] is True
    assert rough["primary_issue"] == "主体问题"
    assert rough["issues"] == ["主体问题", "文字问题"]
    assert rough["note"] == "需要复核"


def test_convert_optional_secondary_review_json_updates_v2_fine_annotations():
    from scripts.convert_annotation_v1_to_v2_rough import convert_annotation_v1_to_v2_rough
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    v1_path = tmp_path / "v1_filtered.jsonl"
    v2_path = tmp_path / "v2_unfiltered.jsonl"
    secondary_path = tmp_path / "secondary.json"
    output_path = tmp_path / "v2_with_rough_and_fine.jsonl"
    write_jsonl(
        v1_path,
        [
            {
                "src_image": "原始图片/a.jpg",
                "dst_image": "生成图片/20250702--图4_p1_方案_1_84383.jpg",
                "mos": 5,
                "username": "rough_a",
                "tags": {"质量维度评分": 5},
            },
            {
                "src_image": "原始图片/b.jpg",
                "dst_image": "生成图片/20250628-bhc炸鸡我终于来了！！！！-图1_p1_方案_1_68970.jpg",
                "mos": 4,
                "username": "rough_b",
                "tags": {"质量维度评分": 5},
            },
        ],
    )
    write_jsonl(
        v2_path,
        [
            {
                "src_image": "原始图片/a.jpg",
                "dst_image": "生成图片/20250702--图4_p1_方案_1_84383.jpg",
            },
            {
                "src_image": "原始图片/b.jpg",
                "dst_image": "生成图片/20250628-bhc炸鸡我终于来了！！！！-图1_p1_方案_1_68970.jpg",
            },
        ],
    )
    secondary_path.write_text(
        json.dumps(
            {
                "20250702--图4_p1_方案_1_84383.jpg": {
                    "status": "confirm",
                    "reviewer": "l00950843",
                    "review_time": "2026-06-01 18:21:48",
                    "data": {
                        "file": "/root/lyh/haco/food/data/high_mos_images/20250702--图4_p1_方案_1_84383.jpg"
                    },
                },
                "20250628-bhc炸鸡我终于来了！！！！-图1_p1_方案_1_68970.jpg": {
                    "status": "reject",
                    "reviewer": "w60124604",
                    "review_time": "2026-06-03 10:38:28",
                    "data": {
                        "file": "/root/lyh/haco/food/data/high_mos_images/20250628-bhc炸鸡我终于来了！！！！-图1_p1_方案_1_68970.jpg"
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    stats = convert_annotation_v1_to_v2_rough(
        v1_path,
        v2_path,
        output_path,
        secondary_review_json=secondary_path,
    )

    assert stats["secondary_rows"] == 2
    assert stats["secondary_matched"] == 2
    assert stats["secondary_unmatched"] == 0
    rows = read_jsonl(output_path)
    assert rows[0]["fine_annotations"] == [
        {
            "username": "l00950843",
            "mos": 5,
            "has_defect": False,
            "primary_issue": "",
            "issues": [],
            "other_issue": "",
            "note": "",
            "updated_at": "2026-06-01 18:21:48",
        }
    ]
    assert rows[1]["fine_annotations"][0]["username"] == "w60124604"
    assert rows[1]["fine_annotations"][0]["mos"] == 3
    assert rows[1]["fine_annotations"][0]["has_defect"] is False

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(v2_path)})
    result = store.import_annotations_jsonl(task["id"], output_path)
    assert result["imported_count"] == 2
    assert store.summary(task["id"])["fine_completed"] == 2
