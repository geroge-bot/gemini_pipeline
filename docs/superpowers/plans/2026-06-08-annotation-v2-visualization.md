# Annotation V2 Stage Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only visualization pages for rough screening, fine screening, sampling, and label correction in `web/annotations_v2`.

**Architecture:** Add a v2-native store method and API route that page through stage-specific visualization rows. Add one Flask template and extend the existing vanilla JS/CSS to render the selected stage with an image pair and read-only result panel. Keep the old `web/annotations` result/statistics/QC systems out of scope.

**Tech Stack:** Python 3, Flask, pytest, vanilla JavaScript, CSS.

---

## File Structure

- Modify `tests/test_annotations_v2_app.py`: add failing coverage for store behavior, API route, route/template separation, and frontend links.
- Modify `web/annotations_v2/app.py`: add relative path helper, visualization row builder, store method, Flask page route, and API endpoint.
- Create `web/annotations_v2/templates/visualize.html`: read-only stage visualization page shell.
- Modify `web/annotations_v2/static/app.js`: add visualization state, task-card links, page loading, navigation, and render helpers.
- Modify `web/annotations_v2/static/styles.css`: add compact read-only visualization layout and metadata styles.

---

### Task 1: Backend Visualization Contract

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Write failing store and API tests**

Add tests that create a three-item task, save rough/fine/sample/label records, and assert stage filtering:

```python
def test_v2_visualization_results_are_stage_specific():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
            {"src_image": "src/c.jpg", "dst_image": "dst/c.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "visualize",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True},
            "fine": {"min_mos": 4, "enable_defect": False},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": True})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "bob", "mos": 3, "has_defect": False})
    store.sample(task["id"], {"target_count": 2, "min_per_bucket": 1})
    store.save_label(task["id"], 0, {"username": "carol", "labels": {"输入图": {"菜品种类": "融合菜"}}})

    rough_total, rough_rows = store.get_visualization_results(task["id"], "rough", offset=0, limit=10)
    fine_total, fine_rows = store.get_visualization_results(task["id"], "fine", offset=0, limit=10)
    sample_total, sample_rows = store.get_visualization_results(task["id"], "sample", offset=0, limit=10)
    label_total, label_rows = store.get_visualization_results(task["id"], "label", offset=0, limit=10)

    assert rough_total == 3
    assert [row["item_index"] for row in rough_rows] == [0, 1, 2]
    assert rough_rows[1]["stage_passed"] is False
    assert rough_rows[1]["stage_annotations"][0]["has_defect"] is True

    assert fine_total == 2
    assert [row["item_index"] for row in fine_rows] == [0, 2]
    assert fine_rows[0]["stage_passed"] is True
    assert fine_rows[1]["stage_passed"] is False

    assert sample_total == 1
    assert sample_rows[0]["sampled"] is True
    assert sample_rows[0]["sample_bucket"] == "输入图/菜品种类=中餐"

    assert label_total == 1
    assert label_rows[0]["corrected_labels"] == {"输入图": {"菜品种类": "融合菜"}}
    assert label_rows[0]["label_username"] == "carol"
    assert label_rows[0]["src_relative_path"] == "src/a.jpg"
    assert label_rows[0]["dst_relative_path"] == "dst/a.jpg"
```

Add API coverage:

```python
def test_v2_visualization_results_api_pages_stage_rows():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        client = annotations_v2_app.app.test_client()
        task = client.post(
            "/api/tasks",
            json={"name": "api visualize", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)},
        ).get_json()["task"]
        client.post(f"/api/tasks/{task['id']}/items/0/rough", json={"username": "alice", "mos": 5, "has_defect": False})

        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=rough&page=0&limit=1")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["stage"] == "rough"
        assert payload["total"] == 1
        assert payload["page"] == 0
        assert payload["limit"] == 1
        assert payload["results"][0]["item_index"] == 0
        assert payload["results"][0]["stage_result"]["mos"] == 5
    finally:
        annotations_v2_app.store = old_store
```

Add bad stage coverage:

```python
def test_v2_visualization_results_reject_unknown_stage():
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    try:
        client = annotations_v2_app.app.test_client()
        task = annotations_v2_app.store.create_task({"root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
        response = client.get(f"/api/tasks/{task['id']}/visualization-results?stage=other")
        assert response.status_code == 400
        assert response.get_json()["error"] == "未知可视化阶段"
    finally:
        annotations_v2_app.store = old_store
        annotations_v2_app.app.config.update(PROPAGATE_EXCEPTIONS=None)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_visualization_results_are_stage_specific tests/test_annotations_v2_app.py::test_v2_visualization_results_api_pages_stage_rows tests/test_annotations_v2_app.py::test_v2_visualization_results_reject_unknown_stage -q
```

Expected: FAIL because `AnnotationV2Store.get_visualization_results` and `/api/tasks/<task_id>/visualization-results` do not exist.

- [ ] **Step 3: Implement backend contract**

Add `image_relative_path`, `VALID_VISUALIZATION_STAGES`, `AnnotationV2Store._visualization_candidates`, `AnnotationV2Store._visualization_row`, and `AnnotationV2Store.get_visualization_results`.

The store method must:

- Accept `rough`, `fine`, `sample`, and `label`.
- Raise `ValueError("未知可视化阶段")` for any other stage.
- Return `(total, rows)` where `total` is after stage filtering and before paging.
- Use `stage_result` for aggregate rough/fine records.
- Use `stage_annotations` for rough/fine annotation lists.
- Include `src_relative_path`, `dst_relative_path`, `image_urls`, `original_labels`, `record`, `sampled`, `sample_bucket`, `corrected_labels`, `label_username`, and `label_updated_at`.

Add API route:

```python
@app.get("/api/tasks/<task_id>/visualization-results")
def api_visualization_results(task_id: str):
    page = max(0, int(request.args.get("page", 0) or 0))
    limit = max(1, int(request.args.get("limit", 1) or 1))
    stage = request.args.get("stage", "rough")
    total, results = store.get_visualization_results(task_id, stage, offset=page * limit, limit=limit)
    return jsonify({"stage": stage, "results": results, "total": total, "page": page, "limit": limit})
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_visualization_results_are_stage_specific tests/test_annotations_v2_app.py::test_v2_visualization_results_api_pages_stage_rows tests/test_annotations_v2_app.py::test_v2_visualization_results_reject_unknown_stage -q
```

Expected: PASS.

---

### Task 2: Visualization Page Route And Template

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`
- Create: `web/annotations_v2/templates/visualize.html`

- [ ] **Step 1: Write failing route/template test**

Add:

```python
def test_v2_visualization_page_route_is_separate_from_rate_page():
    from web.annotations_v2 import app as annotations_v2_app

    annotations_v2_app.app.config.update(TESTING=True)
    client = annotations_v2_app.app.test_client()

    visualize_response = client.get("/dataset/visualize/task-123?stage=rough")
    visualize_html = visualize_response.data.decode("utf-8")

    assert visualize_response.status_code == 200
    assert 'data-page="visualize"' in visualize_html
    assert 'data-task-id="task-123"' in visualize_html
    assert 'id="visualizationStageTabs"' in visualize_html
    assert 'id="visualizationBody"' in visualize_html
    assert 'id="visualizationResultPanel"' in visualize_html
    assert 'id="stageForm"' not in visualize_html
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_visualization_page_route_is_separate_from_rate_page -q
```

Expected: FAIL with a 404 or missing template route.

- [ ] **Step 3: Implement route and template**

Add Flask route:

```python
@app.get("/dataset/visualize/<task_id>")
def visualize_page(task_id: str):
    return render_template("visualize.html", task_id=task_id)
```

Create `visualize.html` with:

- `body data-page="visualize" data-task-id="{{ task_id }}"`
- Login view matching existing v2 templates
- Topbar with return link
- Work header with `visualizationTitle`, `visualizationProgress`, `visualizationStageTabs`, `visualizationPrevBtn`, `visualizationJumpInput`, `visualizationJumpBtn`, and `visualizationNextBtn`
- `visualizationBody` containing source figure, target figure, and `visualizationResultPanel`
- The existing `app.js` script include with a new cache-busting suffix

- [ ] **Step 4: Run test to verify GREEN**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_visualization_page_route_is_separate_from_rate_page -q
```

Expected: PASS.

---

### Task 3: Frontend Links And Visualization Renderer

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/static/app.js`

- [ ] **Step 1: Write failing frontend string test**

Add:

```python
def test_v2_frontend_links_and_renders_stage_visualization_pages():
    script = (PROJECT_ROOT / "web" / "annotations_v2" / "static" / "app.js").read_text(encoding="utf-8")

    assert '`/dataset/visualize/${task.id}?stage=rough`' in script
    assert '`/dataset/visualize/${task.id}?stage=fine`' in script
    assert '`/dataset/visualize/${task.id}?stage=sample`' in script
    assert '`/dataset/visualize/${task.id}?stage=label`' in script
    assert "openVisualizationPage" in script
    assert "reloadVisualizationResults" in script
    assert "renderVisualizationPage" in script
    assert "renderScreeningVisualization" in script
    assert "renderSampleVisualization" in script
    assert "renderLabelVisualization" in script
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_links_and_renders_stage_visualization_pages -q
```

Expected: FAIL because the links and renderer functions do not exist.

- [ ] **Step 3: Implement frontend links and renderer**

Update `state` with:

```javascript
visualizationStage: "rough",
visualizationResults: [],
visualizationPage: 0,
visualizationTotal: 0,
```

In task cards, add links:

```javascript
<a class="buttonLike ghost" href="/dataset/visualize/${task.id}?stage=rough">粗筛结果</a>
<a class="buttonLike ghost" href="/dataset/visualize/${task.id}?stage=fine">精筛结果</a>
<a class="buttonLike ghost" href="/dataset/visualize/${task.id}?stage=sample">采样结果</a>
<a class="buttonLike ghost" href="/dataset/visualize/${task.id}?stage=label">标签结果</a>
```

Add visualization functions:

- `openVisualizationPage()`: parse URL stage, load tasks, set `activeTask`, and call reload/render.
- `reloadVisualizationResults()`: call `/api/tasks/${state.taskId}/visualization-results?stage=${state.visualizationStage}&page=${state.visualizationPage}&limit=1`.
- `renderVisualizationPage()`: render title, progress, images, paths, and delegate result panel by stage.
- `renderScreeningVisualization(item)`: render aggregate, pass badge, and all annotations.
- `renderSampleVisualization(item)`: render sampled badge, bucket, rough/fine summaries.
- `renderLabelVisualization(item)`: render original labels and corrected labels.

Update `enterApp()` so `state.page === "visualize"` initializes the visualization page instead of the home/rate workbench.

Add click handlers for previous, next, jump, and stage tabs.

- [ ] **Step 4: Run frontend test and syntax check**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_links_and_renders_stage_visualization_pages -q
node --check web/annotations_v2/static/app.js
```

Expected: both commands PASS.

---

### Task 4: Visualization Styles

**Files:**
- Modify: `web/annotations_v2/static/styles.css`

- [ ] **Step 1: Add layout and read-only styles**

Add CSS classes:

- `.visualizationGrid`
- `.visualizationGrid figure`
- `.visualizationGrid img`
- `.visualizationPanel`
- `.stageTabs`
- `.stageTab`
- `.resultBadge`
- `.resultBlock`
- `.resultList`
- `.tagRows`
- `.tagRow`
- `.tagKey`
- `.tagValue`
- `.imagePath`

Keep the layout compact and consistent with existing `stageGrid`.

- [ ] **Step 2: Run frontend syntax check**

Run:

```bash
node --check web/annotations_v2/static/app.js
```

Expected: PASS. CSS has no project linter, so verify by reviewing selectors and using the browser smoke check in Task 5.

---

### Task 5: Full Verification And Commit

**Files:**
- All modified files from Tasks 1-4

- [ ] **Step 1: Run targeted backend/frontend checks**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py -q
node --check web/annotations_v2/static/app.js
```

Expected: PASS.

- [ ] **Step 2: Optional browser smoke check if server starts cleanly**

Run:

```bash
ANNOTATIONS_V2_PORT=5065 python -m web.annotations_v2.app
```

Open:

```text
http://127.0.0.1:5065/
```

Check that the task card has four visualization links and that `/dataset/visualize/<task_id>?stage=rough` renders without overlapping controls.

- [ ] **Step 3: Review diff**

Run:

```bash
git diff -- web/annotations_v2/app.py web/annotations_v2/templates/visualize.html web/annotations_v2/static/app.js web/annotations_v2/static/styles.css tests/test_annotations_v2_app.py
```

Expected: diff only contains v2 visualization API, page, frontend, style, and tests.

- [ ] **Step 4: Commit implementation**

Run:

```bash
git add tests/test_annotations_v2_app.py web/annotations_v2/app.py web/annotations_v2/templates/visualize.html web/annotations_v2/static/app.js web/annotations_v2/static/styles.css docs/superpowers/plans/2026-06-08-annotation-v2-visualization.md
git commit -m "feat: add annotation v2 visualization pages"
```

Expected: commit succeeds and does not stage unrelated working tree changes.

