from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_annotations_v3_package_has_been_removed():
    assert not (PROJECT_ROOT / "web" / "annotations_v3").exists()
