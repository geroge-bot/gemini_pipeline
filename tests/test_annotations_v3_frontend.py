import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def write_source_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_dataset(tmp_path, monkeypatch):
    from web.annotations_v3 import datasets, storage

    data_dir = tmp_path / "v3-data"
    root_dir = tmp_path / "images"
    source_jsonl = tmp_path / "items.jsonl"
    write_source_jsonl(
        source_jsonl,
        [{"item_id": "sha256:item-0", "src_image": "ori/0.png", "dst_image": "gen/0.png"}],
    )
    monkeypatch.setenv(storage.DATA_DIR_ENV, str(data_dir))
    return datasets.create_dataset(
        {
            "dataset_id": "dataset-1",
            "name": "food batch",
            "source_jsonl": str(source_jsonl),
            "root_dir": str(root_dir),
            "order": {"mode": "natural"},
        }
    )


def test_page_routes(tmp_path, monkeypatch):
    from web.annotations_v3.app import create_app

    dataset_doc = make_dataset(tmp_path, monkeypatch)
    client = create_app().test_client()

    index = client.get("/")
    assert index.status_code == 200
    assert b'data-page="datasets"' in index.data
    assert b'id="loginView"' in index.data
    assert b'id="appView"' in index.data
    assert b'id="datasetList"' in index.data

    detail = client.get(f"/datasets/{dataset_doc['dataset_id']}")
    assert detail.status_code == 200
    assert "food batch".encode("utf-8") in detail.data

    rate = client.get(f"/datasets/{dataset_doc['dataset_id']}/rate?stage=rough")
    assert rate.status_code == 200
    assert b'data-stage="rough"' in rate.data
    assert b'id="loginView"' in rate.data
    assert b'id="appView"' in rate.data
    assert b'class="topbar hidden"' in rate.data
    assert b'href="/"' in rate.data
    assert b'id="workbench"' in rate.data
    assert b'id="stageBody"' in rate.data
    assert b'id="stageForm"' in rate.data

    missing = client.get("/datasets/missing")
    assert missing.status_code == 404


def test_core_static_modules():
    from web.annotations_v3.app import create_app

    client = create_app().test_client()
    expected = {
        "/static/core/apiClient.js": ["requestJson", "claimAssignment", "saveAnnotationPatch"],
        "/static/core/datasetContext.js": ["readDatasetContext", "setUsername"],
        "/static/core/imageAssetService.js": ["createImageAssetService", "chooseAsset"],
        "/static/core/preloadScheduler.js": ["createPreloadScheduler", "scheduleRatePreloads"],
        "/static/core/annotationSchema.js": ["fieldKey", "getNested", "setNested"],
        "/static/core/annotationDraftStore.js": ["createDraftStore"],
        "/static/core/annotationPayload.js": ["buildPatchPayload"],
    }

    for path, exports in expected.items():
        response = client.get(path)
        assert response.status_code == 200, path
        text = response.data.decode("utf-8")
        for name in exports:
            assert name in text


def test_rate_static_components():
    from web.annotations_v3.app import create_app

    client = create_app().test_client()
    expected = {
        "/static/components/imagePairView.js": ["renderImagePair"],
        "/static/components/annotationFieldRenderer.js": ["renderField"],
        "/static/components/readonlyAnnotationView.js": ["renderReadonlyAnnotation"],
        "/static/ratePage.js": ["claimAssignment", "saveCurrentItem", "renderCurrentItem"],
    }

    for path, names in expected.items():
        response = client.get(path)
        assert response.status_code == 200, path
        text = response.data.decode("utf-8")
        for name in names:
            assert name in text
