# Annotation V2 Image Acceleration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve warmed `annotations_v2` preview images through stable static URLs and make the browser load thumbs before full previews.

**Architecture:** Extend the existing preview cache instead of replacing it. Warming creates `thumb` and `preview` variants plus a per-task manifest; API payloads prefer manifest-backed `/annotation-assets/...` URLs and fall back to the existing Flask image endpoint. The frontend renders a thumb immediately, decodes the preview off-screen, and only marks current images high priority.

**Tech Stack:** Flask, Pillow, vanilla JavaScript, pytest, Node syntax check, Nginx static alias in deployment.

---

### Task 1: Manifest and Variant Generation

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Write failing backend test for two variants and manifest**

Add this test near the existing preview cache tests:

```python
def test_v2_preview_cache_job_writes_static_manifest_with_thumb_and_preview_variants():
    from PIL import Image
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    preview_cache_root = tmp_path / "preview-cache"
    make_test_image(tmp_path / "src" / "large.jpg", (1600, 900))
    make_test_image(tmp_path / "dst" / "large.jpg", (1200, 1000))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/large.jpg", "dst_image": "dst/large.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json", preview_cache_dir=preview_cache_root)
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})

    result = store.warm_preview_cache(task["id"])

    manifest_path = preview_cache_root / task["id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    src_entry = manifest["items"]["0"]["src"]
    dst_entry = manifest["items"]["0"]["dst"]
    assert result["total"] == 2
    assert result["generated_count"] == 4
    assert result["failed_count"] == 0
    assert src_entry["variants"]["thumb"]["url"].startswith(f"/annotation-assets/{task['id']}/")
    assert src_entry["variants"]["preview"]["url"].startswith(f"/annotation-assets/{task['id']}/")
    assert dst_entry["variants"]["thumb"]["url"].startswith(f"/annotation-assets/{task['id']}/")
    assert max(Image.open(preview_cache_root / task["id"] / src_entry["variants"]["thumb"]["path"]).size) == 512
    assert max(Image.open(preview_cache_root / task["id"] / src_entry["variants"]["preview"]["path"]).size) == 1024
```

- [ ] **Step 2: Verify the test fails**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_preview_cache_job_writes_static_manifest_with_thumb_and_preview_variants -q
```

Expected: FAIL because `manifest.json` and `thumb` variants do not exist.

- [ ] **Step 3: Implement variant helpers and manifest writing**

In `web/annotations_v2/app.py`, add constants and helpers near the existing preview cache helpers:

```python
IMAGE_THUMB_MAX_EDGE = 512
STATIC_ASSET_URL_PREFIX = "/annotation-assets"

def preview_manifest_path(cache_dir: Path) -> Path:
    return cache_dir / "manifest.json"

def preview_variant_filename(cache_key: str, variant: str, image_format: str) -> str:
    suffix = ".jpg" if image_format == "JPEG" else f".{image_format.lower()}"
    return f"{cache_key}.{variant}{suffix}"

def resized_image_variant(path: Path, cache_dir: Path, variant: str, max_edge: int) -> dict[str, Any]:
    mimetype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    cache_key = preview_cache_key(path, max_edge)
    with Image.open(path) as image:
        image.load()
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        image_format = (image.format or path.suffix.lstrip(".") or "JPEG").upper()
        if image_format == "JPG":
            image_format = "JPEG"
        if image_format == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        filename = preview_variant_filename(cache_key, variant, image_format)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / filename
        if not cache_path.exists():
            tmp_path = cache_path.with_name(f"{cache_path.name}.{threading.get_ident()}.tmp")
            image.save(tmp_path, format=image_format, quality=88, optimize=True)
            os.replace(tmp_path, cache_path)
        width, height = Image.open(cache_path).size
        return {
            "path": filename,
            "url": "",
            "width": width,
            "height": height,
            "size": cache_path.stat().st_size,
            "mimetype": Image.MIME.get(image_format, mimetype),
        }
```

Update `warm_preview_cache` so each source image job generates `thumb` and `preview`, builds `manifest["items"][str(item_index)][kind]`, and writes `manifest.json` atomically after all futures finish.

- [ ] **Step 4: Verify Task 1 passes**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_preview_cache_job_writes_static_manifest_with_thumb_and_preview_variants -q
```

Expected: PASS.

### Task 2: Manifest-backed API URLs with Fallback

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Write failing API payload test**

Add this test near the preview cache tests:

```python
def test_v2_stage_items_prefer_static_manifest_image_urls_and_keep_original_fallback():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    preview_cache_root = tmp_path / "preview-cache"
    make_test_image(tmp_path / "src" / "large.jpg", (1600, 900))
    make_test_image(tmp_path / "dst" / "large.jpg", (1200, 1000))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/large.jpg", "dst_image": "dst/large.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json", preview_cache_dir=preview_cache_root)
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        annotations_v2_app.store.warm_preview_cache(task["id"])
        client = annotations_v2_app.app.test_client()

        response = client.get(f"/api/tasks/{task['id']}/stage-items?stage=rough&username=alice")

        item = response.get_json()["items"][0]
        assert response.status_code == 200
        assert item["image_urls"]["src"].startswith(f"/annotation-assets/{task['id']}/")
        assert item["image_urls"]["dst"].startswith(f"/annotation-assets/{task['id']}/")
        assert item["image_urls"]["src_thumb"].startswith(f"/annotation-assets/{task['id']}/")
        assert item["image_urls"]["dst_thumb"].startswith(f"/annotation-assets/{task['id']}/")
        assert item["image_urls"]["src_original"].endswith("/src?original=1")
        assert item["image_urls"]["dst_original"].endswith("/dst?original=1")
    finally:
        annotations_v2_app.store = old_store
```

Add a focused fallback test:

```python
def test_v2_stage_items_fall_back_to_flask_image_urls_without_manifest():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    make_test_image(tmp_path / "src" / "a.jpg", (800, 600))
    make_test_image(tmp_path / "dst" / "a.jpg", (800, 600))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    item = store.get_stage_items(task["id"], "rough", "alice")["items"][0]

    assert item["image_urls"]["src"] == f"/api/tasks/{task['id']}/images/0/src"
    assert item["image_urls"]["dst"] == f"/api/tasks/{task['id']}/images/0/dst"
    assert item["image_urls"]["src_thumb"] == item["image_urls"]["src"]
    assert item["image_urls"]["dst_thumb"] == item["image_urls"]["dst"]
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_stage_items_prefer_static_manifest_image_urls_and_keep_original_fallback tests/test_annotations_v2_app.py::test_v2_stage_items_fall_back_to_flask_image_urls_without_manifest -q
```

Expected: first FAIL because payloads do not read the manifest; second FAIL because thumb and original keys are absent.

- [ ] **Step 3: Implement manifest reading and image URL payload helper**

In `AnnotationV2Store`, add:

```python
def _read_preview_manifest(self, task_id: str) -> dict[str, Any]:
    return read_json_file(preview_manifest_path(self.preview_cache_dir(task_id)), {"items": {}})

def _image_urls(self, task: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
    task_id = str(task["id"])
    item_index = int(item["item_index"])
    fallback_src = f"/api/tasks/{task_id}/images/{item_index}/src"
    fallback_dst = f"/api/tasks/{task_id}/images/{item_index}/dst"
    manifest = self._read_preview_manifest(task_id)
    entry = manifest.get("items", {}).get(str(item_index), {})
    src = entry.get("src", {}).get("variants", {})
    dst = entry.get("dst", {}).get("variants", {})
    return {
        "src": src.get("preview", {}).get("url") or fallback_src,
        "dst": dst.get("preview", {}).get("url") or fallback_dst,
        "src_thumb": src.get("thumb", {}).get("url") or src.get("preview", {}).get("url") or fallback_src,
        "dst_thumb": dst.get("thumb", {}).get("url") or dst.get("preview", {}).get("url") or fallback_dst,
        "src_original": f"{fallback_src}?original=1",
        "dst_original": f"{fallback_dst}?original=1",
    }
```

Use `_image_urls()` in `_item_payload()` and `_visualization_row()`.

- [ ] **Step 4: Verify Task 2 passes**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_stage_items_prefer_static_manifest_image_urls_and_keep_original_fallback tests/test_annotations_v2_app.py::test_v2_stage_items_fall_back_to_flask_image_urls_without_manifest -q
```

Expected: PASS.

### Task 3: Cache Status Summary

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Write failing task payload cache status test**

Add:

```python
def test_v2_task_payload_reports_static_preview_cache_status():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    preview_cache_root = tmp_path / "preview-cache"
    make_test_image(tmp_path / "src" / "a.jpg", (1200, 800))
    make_test_image(tmp_path / "dst" / "a.jpg", (1200, 800))
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    store = AnnotationV2Store(tmp_path / "state.json", preview_cache_dir=preview_cache_root)
    task = store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    before = store.list_tasks()[0]["preview_cache"]
    store.warm_preview_cache(task["id"])
    after = store.list_tasks()[0]["preview_cache"]

    assert before["total_images"] == 2
    assert before["generated_images"] == 0
    assert before["cache_ready"] is False
    assert after["total_images"] == 2
    assert after["generated_images"] == 2
    assert after["failed_images"] == 0
    assert after["missing_images"] == 0
    assert after["cache_ready"] is True
```

- [ ] **Step 2: Verify test fails**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_task_payload_reports_static_preview_cache_status -q
```

Expected: FAIL because `preview_cache` is not in task payloads.

- [ ] **Step 3: Implement cache summary**

Add `AnnotationV2Store._preview_cache_summary(task)` and include it in `_task_payload()`:

```python
def _preview_cache_summary(self, task: dict[str, Any]) -> dict[str, Any]:
    total_images = int(task.get("item_count") or 0) * 2
    manifest = self._read_preview_manifest(str(task["id"]))
    generated_images = 0
    missing_images = 0
    failed_images = 0
    for item_entry in manifest.get("items", {}).values():
        for kind in ("src", "dst"):
            variants = item_entry.get(kind, {}).get("variants", {})
            if variants.get("thumb") and variants.get("preview"):
                generated_images += 1
            else:
                missing_images += 1
    failed_images = len(manifest.get("failures", []))
    if total_images and generated_images < total_images:
        missing_images += total_images - generated_images - missing_images
    return {
        "total_images": total_images,
        "generated_images": generated_images,
        "failed_images": failed_images,
        "missing_images": max(0, missing_images),
        "cache_ready": total_images > 0 and generated_images == total_images and failed_images == 0,
        "updated_at": manifest.get("updated_at"),
    }
```

- [ ] **Step 4: Verify Task 3 passes**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_task_payload_reports_static_preview_cache_status -q
```

Expected: PASS.

### Task 4: Frontend Two-phase Loading and Low-priority Preload

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/static/app.js`

- [ ] **Step 1: Update failing frontend static test**

Replace `test_v2_frontend_preloads_next_three_preview_pages` expectations with:

```python
assert "const PRELOAD_FORWARD_PAGES = 3;" in script
assert "const PRELOAD_CONCURRENCY = 4;" in script
assert "const preloadQueue = [];" in script
assert "let activePreloadCount = 0;" in script
assert "function preparePreviewImage(image, previewSrc, thumbSrc, originalSrc)" in script
assert "image.fetchPriority = \"high\";" in script
assert "preview.decode()" in script
assert 'preparePreviewImage($("srcImage"), item.image_urls.src, item.image_urls.src_thumb, item.image_urls.src_original);' in script
assert 'preparePreviewImage($("dstImage"), item.image_urls.dst, item.image_urls.dst_thumb, item.image_urls.dst_original);' in script
assert "enqueuePreloadImage(" in script
assert "function runPreloadQueue()" in script
assert "if (activePreloadCount >= PRELOAD_CONCURRENCY)" in script
assert "preload.fetchPriority = \"low\";" in script
```

- [ ] **Step 2: Verify test fails**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_preloads_next_three_preview_pages -q
```

Expected: FAIL because the frontend still uses single-source eager loading and unbounded preload images.

- [ ] **Step 3: Implement frontend loading**

Update `app.js`:

```javascript
const PRELOAD_CONCURRENCY = 4;
const preloadQueue = [];
let activePreloadCount = 0;
```

Replace `preparePreviewImage`:

```javascript
function preparePreviewImage(image, previewSrc, thumbSrc, originalSrc) {
  const visibleSrc = thumbSrc || previewSrc;
  image.loading = "eager";
  image.decoding = "async";
  image.fetchPriority = "high";
  image.dataset.originalSrc = originalSrc || `${previewSrc}?original=1`;
  if (visibleSrc && image.src !== visibleSrc) {
    image.src = visibleSrc;
  }
  if (!previewSrc || previewSrc === visibleSrc) return;
  const loadToken = `${Date.now()}-${Math.random()}`;
  image.dataset.previewLoadToken = loadToken;
  const preview = new Image();
  preview.decoding = "async";
  preview.src = previewSrc;
  const swap = () => {
    if (image.dataset.previewLoadToken === loadToken && image.src !== previewSrc) {
      image.src = previewSrc;
    }
  };
  if (preview.decode) {
    preview.decode().then(swap).catch(swap);
  } else {
    preview.onload = swap;
  }
}
```

Update current image call sites to pass preview, thumb, and original URLs. Replace `preloadImage` with an enqueue/run queue pair that limits concurrent low-priority preloads.

- [ ] **Step 4: Verify Task 4 passes**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_preloads_next_three_preview_pages -q
node --check web/annotations_v2/static/app.js
```

Expected: PASS and Node syntax check succeeds.

### Task 5: Full Verification

**Files:**
- Verify: `tests/test_annotations_v2_app.py`
- Verify: `web/annotations_v2/static/app.js`

- [ ] **Step 1: Run focused backend and frontend tests**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py -q
node --check web/annotations_v2/static/app.js
```

Expected: all tests pass.

- [ ] **Step 2: Inspect git diff**

Run:

```bash
git diff -- web/annotations_v2/app.py web/annotations_v2/static/app.js tests/test_annotations_v2_app.py
```

Expected: diff only contains preview manifest, static image URL, cache status, frontend image loading, and matching tests.
