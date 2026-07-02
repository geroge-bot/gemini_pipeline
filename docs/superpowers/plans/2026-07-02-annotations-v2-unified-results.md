# Annotations V2 Unified Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge all annotations_v2 result display pages into one item-centric result page, support inline label dropdown editing like annotations v1, and preserve editor attribution/history.

**Architecture:** Keep the existing v2 workflow and records storage intact, including gzip-compressed sharded `records/<item_index>.json.gz` files with legacy `records/<item_index>.json` and `records.json` fallback. Add a unified result API that returns one paginated item with all stage data, then replace the stage-tab visualization UI with one result workbench. Label editing writes a current `record.label` snapshot plus append-only `record.label_revisions` history for traceability.

**Tech Stack:** Flask, vanilla JavaScript, JSON file storage, pytest, Node `--check`.

---

## File Structure

- Modify `web/annotations_v2/app.py`
  - Add unified result row construction, unified filter matching/options, and inline label revision save API.
  - Keep old visualization APIs working for compatibility.
- Modify `web/annotations_v2/static/app.js`
  - Replace stage-specific result view state and render paths with unified result state.
  - Add dropdown label editors and save calls.
- Modify `web/annotations_v2/templates/visualize.html`
  - Remove stage tabs, keep pager/filter drawer, add a compact unified result panel host.
- Modify `web/annotations_v2/static/styles.css`
  - Add styles for inline result label editors, status sections, and revision history.
- Modify `tests/test_annotations_v2_app.py`
  - Add backend coverage for unified results, filtering, and label revision history.
- Optionally modify `web/annotations_v2/IMPORT_FORMAT.md` or `web/annotations_v2/STORAGE.md`
  - Document `label_revisions` only if implementation changes persisted record format in a way operators need to know.

## Remote Access Efficiency Requirements

- The unified result API must remain server-side paginated: default UI request uses `page` and `limit=1`.
- The API must build/filter candidates server-side and only serialize the current page.
- `include_filter_options=0` must skip expensive filter option aggregation during image preloading.
- Frontend preloading must request at most the next three result pages and only image URLs, reusing the existing preview-cache image endpoint.
- The result row must not inline image bytes or full task item arrays.
- Label edits must update one gzip-aware item record shard via `_update_record`, not rewrite all records.
- Implementation must use the existing gzip-aware `read_json_file`, `read_gzip_json_file`, `write_json_file`, `write_gzip_json_file`, `_read_record`, `_read_records`, and `_write_record` helpers. Do not manually read or write `records/<item_index>.json.gz` in new production code.

---

### Task 1: Backend Tests for Unified Result Rows

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify later: `web/annotations_v2/app.py`

- [ ] **Step 1: Add failing tests**

Append these tests near the existing v2 store tests:

```python
def test_v2_unified_results_return_all_stage_data(tmp_path):
    from web.annotations_v2.app import AnnotationV2Store

    jsonl_path = tmp_path / "pairs.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "unified",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "annotator_count": 1, "require_no_defect": True},
            "fine": {"min_mos": 4, "annotator_count": 1},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 0, {"username": "bob", "mos": 4, "has_defect": False})
    store.sample(task["id"], {"select_all": True})
    store.save_label(task["id"], 0, {"username": "carol", "labels": {"输入图": {"菜品种类": "西餐"}}})

    total, rows = store.get_unified_results(task["id"], offset=0, limit=1)

    assert total == 2
    assert rows[0]["item_index"] == 0
    assert rows[0]["rough"]["username"] == "alice"
    assert rows[0]["fine"]["username"] == "bob"
    assert rows[0]["sampled"] is True
    assert rows[0]["label"]["username"] == "carol"
    assert rows[0]["effective_labels"] == {"输入图": {"菜品种类": "西餐"}}
    assert rows[0]["original_labels"] == {"输入图": {"菜品种类": "中餐"}}
    assert rows[0]["status"]["rough_passed"] is True
    assert rows[0]["status"]["fine_passed"] is True


def test_v2_unified_results_can_filter_by_status_and_labels(tmp_path):
    from web.annotations_v2.app import AnnotationV2Store

    jsonl_path = tmp_path / "pairs.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}},
            {"src_image": "src/b.jpg", "dst_image": "dst/b.jpg", "labels": {"输入图": {"菜品种类": "西餐"}}},
        ],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "filters",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "annotator_count": 1, "require_no_defect": True},
            "fine": {"min_mos": 4, "annotator_count": 1},
        }
    )
    store.save_rough(task["id"], 0, {"username": "alice", "mos": 5, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 2, "has_defect": False})

    total, rows = store.get_unified_results(
        task["id"],
        filters={
            "statuses": ["rough_passed"],
            "labels": [{"path": ["输入图", "菜品种类"], "values": ["中餐"]}],
        },
    )

    assert total == 1
    assert rows[0]["item_index"] == 0
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_unified_results_return_all_stage_data tests/test_annotations_v2_app.py::test_v2_unified_results_can_filter_by_status_and_labels -q
```

Expected: FAIL with `AttributeError: 'AnnotationV2Store' object has no attribute 'get_unified_results'`.

### Task 2: Implement Unified Backend Result Read Path

**Files:**
- Modify: `web/annotations_v2/app.py`
- Test: `tests/test_annotations_v2_app.py`

- [ ] **Step 1: Add unified result helpers to `AnnotationV2Store`**

Add methods near the existing visualization helpers. These methods must operate on records returned by `_read_records` and `_read_record`, so they automatically support compressed `.json.gz` shards and legacy fallbacks:

```python
    def _effective_labels(self, item: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        labels = deepcopy(item.get("labels", {}))
        label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
        corrected = label_record.get("labels") if isinstance(label_record.get("labels"), dict) else {}
        nested_overlay(labels, corrected)
        return sanitize_labels(labels)

    def _unified_status(self, task: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        rough_complete = self._stage_complete(task, record, "rough")
        fine_complete = self._stage_complete(task, record, "fine")
        rough_passed = rough_complete and self._rough_passes(task, record.get("rough"))
        fine_passed = fine_complete and self._fine_passes(task, record.get("fine"))
        return {
            "rough_completed": rough_complete,
            "rough_passed": rough_passed,
            "fine_completed": fine_complete,
            "fine_passed": fine_passed,
            "sampled": bool(record.get("sampled")),
            "label_completed": isinstance(record.get("label"), dict),
        }

    def _unified_result_row(self, task: dict[str, Any], item: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
        revisions = record.get("label_revisions") if isinstance(record.get("label_revisions"), list) else []
        return {
            "item_index": item["item_index"],
            "src_image": item["src_image"],
            "dst_image": item["dst_image"],
            "src_relative_path": image_relative_path(task.get("root_dir"), item["src_image"]),
            "dst_relative_path": image_relative_path(task.get("root_dir"), item["dst_image"]),
            "generation_prompt": item.get("generation_prompt", ""),
            "generation_prompt_json_path": item.get("generation_prompt_json_path", ""),
            "image_urls": {
                "src": f"/api/tasks/{task['id']}/images/{item['item_index']}/src",
                "dst": f"/api/tasks/{task['id']}/images/{item['item_index']}/dst",
            },
            "original_labels": deepcopy(item.get("labels", {})),
            "effective_labels": self._effective_labels(item, record),
            "rough": deepcopy(record.get("rough")) if isinstance(record.get("rough"), dict) else None,
            "rough_annotations": self._screen_annotations(record, "rough"),
            "fine": deepcopy(record.get("fine")) if isinstance(record.get("fine"), dict) else None,
            "fine_annotations": self._screen_annotations(record, "fine"),
            "sampled": bool(record.get("sampled")),
            "sample_bucket": record.get("sample_bucket"),
            "label": deepcopy(label_record) if label_record else None,
            "label_revisions": [deepcopy(entry) for entry in revisions if isinstance(entry, dict)],
            "status": self._unified_status(task, record),
        }
```

- [ ] **Step 2: Add unified filtering methods**

Add these methods after `_unified_result_row`:

```python
    def _unified_matches_filters(
        self,
        task: dict[str, Any],
        item: dict[str, Any],
        record: dict[str, Any],
        filters: dict[str, Any] | None,
    ) -> bool:
        if not filters:
            return True

        status_values = normalize_filter_values(filters.get("statuses"))
        if status_values:
            status = self._unified_status(task, record)
            if not any(status.get(value) for value in status_values):
                return False

        mos_values = normalize_filter_values(filters.get("mos"))
        if mos_values:
            mos_candidates = []
            for stage in ("rough", "fine"):
                stage_record = record.get(stage)
                if isinstance(stage_record, dict) and stage_record.get("mos") is not None:
                    mos_candidates.append(str(stage_record.get("mos")))
            if not set(mos_candidates).intersection(mos_values):
                return False

        defect_values = normalize_filter_values(filters.get("has_defect"))
        if defect_values:
            defect_candidates = []
            for stage in ("rough", "fine"):
                stage_record = record.get(stage)
                if isinstance(stage_record, dict) and stage_record.get("has_defect") is not None:
                    defect_candidates.append(str(bool(stage_record.get("has_defect"))))
            if not set(defect_candidates).intersection(defect_values):
                return False

        annotators = normalize_filter_values(filters.get("annotators"))
        if annotators:
            names = set()
            for stage in ("rough", "fine"):
                for annotation in self._screen_annotations(record, stage):
                    if annotation.get("username"):
                        names.add(str(annotation["username"]))
            label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
            if label_record.get("username"):
                names.add(str(label_record["username"]))
            for revision in record.get("label_revisions") or []:
                if isinstance(revision, dict) and revision.get("username"):
                    names.add(str(revision["username"]))
            if not names.intersection(annotators):
                return False

        effective_labels = self._effective_labels(item, record)
        for label_filter in filters.get("labels") or []:
            if not isinstance(label_filter, dict):
                continue
            path = label_filter.get("path")
            if not isinstance(path, list) or not path:
                continue
            selected_values = normalize_filter_values(label_filter.get("values"))
            if not selected_values:
                continue
            current_value = nested_get(effective_labels, [str(part) for part in path])
            current_values = normalize_filter_values(current_value if isinstance(current_value, list) else [current_value])
            if not current_values.intersection(selected_values):
                return False
        return True
```

- [ ] **Step 3: Add `get_unified_results`**

Add this public method:

```python
    def get_unified_results(
        self,
        task_id: str,
        offset: int = 0,
        limit: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        task = self._require_task(task_id)
        items = self._read_items(task)
        records = self._read_records(task)
        candidates = [
            item
            for item in items
            if self._unified_matches_filters(task, item, records.get(str(item["item_index"]), {}), filters)
        ]
        total = len(candidates)
        start = max(0, int(offset or 0))
        stop = total if limit is None else min(total, start + max(0, int(limit)))
        rows = [
            self._unified_result_row(task, item, records.get(str(item["item_index"]), {}))
            for item in candidates[start:stop]
        ]
        return total, rows
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_unified_results_return_all_stage_data tests/test_annotations_v2_app.py::test_v2_unified_results_can_filter_by_status_and_labels -q
```

Expected: PASS.

### Task 3: Backend Label Revision Save API

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_v2_unified_label_edit_records_revision_history(tmp_path):
    from web.annotations_v2.app import AnnotationV2Store

    jsonl_path = tmp_path / "pairs.jsonl"
    write_jsonl(
        jsonl_path,
        [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}}],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "label-edit",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )

    first = store.save_result_labels(
        task["id"],
        0,
        {"username": "alice", "labels": {"输入图": {"菜品种类": "西餐"}}},
    )
    second = store.save_result_labels(
        task["id"],
        0,
        {"username": "bob", "labels": {"输入图": {"菜品种类": "甜品"}}},
    )

    assert first["username"] == "alice"
    assert second["username"] == "bob"
    assert second["labels"] == {"输入图": {"菜品种类": "甜品"}}
    assert len(second["label_revisions"]) == 2
    assert second["label_revisions"][0]["username"] == "alice"
    assert second["label_revisions"][0]["before"] == {"输入图": {"菜品种类": "中餐"}}
    assert second["label_revisions"][0]["after"] == {"输入图": {"菜品种类": "西餐"}}
    assert second["label_revisions"][1]["username"] == "bob"
    assert second["label_revisions"][1]["before"] == {"输入图": {"菜品种类": "西餐"}}
    assert second["label_revisions"][1]["after"] == {"输入图": {"菜品种类": "甜品"}}
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_unified_label_edit_records_revision_history -q
```

Expected: FAIL with missing `save_result_labels`.

- [ ] **Step 3: Implement `save_result_labels`**

Add method near `save_label`:

```python
    def save_result_labels(self, task_id: str, item_index: int, payload: dict[str, Any]) -> dict[str, Any]:
        task = self._require_task(task_id)
        item = self._read_item(task, int(item_index))

        def mutate(item_record: dict[str, Any]) -> dict[str, Any]:
            username = str(payload.get("username") or "").strip()
            if not username:
                raise ValueError("username is required")
            labels = payload.get("labels")
            if not isinstance(labels, dict):
                raise ValueError("labels must be an object")
            labels = selected_sanitized_labels(labels, task.get("selected_label_paths"))
            if not labels:
                raise ValueError("labels must include at least one selected label")

            before = self._effective_labels(item, item_record)
            now = utc_now()
            revision = {
                "id": str(uuid.uuid4()),
                "username": username,
                "updated_at": now,
                "before": before,
                "after": deepcopy(labels),
                "source": "unified_results",
            }
            revisions = item_record.setdefault("label_revisions", [])
            if not isinstance(revisions, list):
                revisions = []
                item_record["label_revisions"] = revisions
            revisions.append(revision)
            item_record["label"] = {
                "username": username,
                "labels": labels,
                "updated_at": now,
            }
            item_record.pop("label_claim", None)
            return {
                **deepcopy(item_record["label"]),
                "label_revisions": [deepcopy(entry) for entry in revisions if isinstance(entry, dict)],
            }

        return self._update_record(task, int(item_index), mutate)
```

This method intentionally writes through `_update_record`, which writes the gzip shard using the current store implementation.

- [ ] **Step 4: Add route**

Add near existing label save route:

```python
@app.post("/api/tasks/<task_id>/results/<int:item_index>/labels")
def api_save_result_labels(task_id: str, item_index: int):
    return jsonify({"record": store.save_result_labels(task_id, item_index, request.get_json(force=True) or {})})
```

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_unified_label_edit_records_revision_history -q
```

Expected: PASS.

### Task 4: Unified Result API Routes and Filter Options

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Add API route test**

Append:

```python
def test_v2_api_exposes_unified_results_and_label_edit(tmp_path):
    from web.annotations_v2 import app as annotations_v2_app
    from web.annotations_v2.app import AnnotationV2Store

    old_store = annotations_v2_app.store
    annotations_v2_app.store = AnnotationV2Store(tmp_path / "state.json")
    annotations_v2_app.app.config.update(TESTING=True)
    try:
        client = annotations_v2_app.app.test_client()
        jsonl_path = tmp_path / "pairs.jsonl"
        write_jsonl(
            jsonl_path,
            [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}}],
        )
        task = client.post(
            "/api/tasks",
            json={
                "name": "api-unified",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            },
        ).get_json()["task"]

        edit_response = client.post(
            f"/api/tasks/{task['id']}/results/0/labels",
            json={"username": "alice", "labels": {"输入图": {"菜品种类": "西餐"}}},
        )
        assert edit_response.status_code == 200
        assert edit_response.get_json()["record"]["username"] == "alice"

        response = client.get(f"/api/tasks/{task['id']}/results?page=0&limit=1")
        assert response.status_code == 200
        data = response.get_json()
        assert data["total"] == 1
        assert data["results"][0]["effective_labels"] == {"输入图": {"菜品种类": "西餐"}}
        assert data["filter_options"]["statuses"]
    finally:
        annotations_v2_app.store = old_store
```

- [ ] **Step 2: Implement filter options**

Add method near `get_unified_results`:

```python
    def get_unified_filter_options(self, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        items = self._read_items(task)
        records = self._read_records(task)
        mos_values = set()
        defect_values = set()
        annotators = set()
        label_values: dict[str, dict[str, Any]] = {}

        for item in items:
            record = records.get(str(item["item_index"]), {})
            for stage in ("rough", "fine"):
                stage_record = record.get(stage)
                if isinstance(stage_record, dict):
                    if stage_record.get("mos") is not None:
                        mos_values.add(int(stage_record["mos"]))
                    if stage_record.get("has_defect") is not None:
                        defect_values.add(bool(stage_record["has_defect"]))
                for annotation in self._screen_annotations(record, stage):
                    if annotation.get("username"):
                        annotators.add(str(annotation["username"]))
            label_record = record.get("label") if isinstance(record.get("label"), dict) else {}
            if label_record.get("username"):
                annotators.add(str(label_record["username"]))
            for revision in record.get("label_revisions") or []:
                if isinstance(revision, dict) and revision.get("username"):
                    annotators.add(str(revision["username"]))

            effective_labels = self._effective_labels(item, record)
            for label_path in flatten_label_paths(effective_labels):
                key = json.dumps(label_path, ensure_ascii=False)
                entry = label_values.setdefault(key, {"path": label_path, "values": set()})
                for value in stat_values(nested_get(effective_labels, label_path)):
                    entry["values"].add(value)

        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in label_values.values():
            path = entry["path"]
            if len(path) < 2:
                continue
            group_name = str(path[0])
            dimension_name = "/".join(str(part) for part in path[1:])
            groups.setdefault(group_name, []).append({"name": dimension_name, "options": sorted(entry["values"])})

        return {
            "statuses": [
                {"value": "rough_completed", "label": "粗筛完成"},
                {"value": "rough_passed", "label": "粗筛通过"},
                {"value": "fine_completed", "label": "精筛完成"},
                {"value": "fine_passed", "label": "精筛通过"},
                {"value": "sampled", "label": "已采样"},
                {"value": "label_completed", "label": "已编辑标签"},
            ],
            "mos": sorted(mos_values),
            "has_defect": sorted(defect_values),
            "annotators": sorted(annotators),
            "label_options": [
                {"name": group_name, "dimensions": sorted(dimensions, key=lambda item: item["name"])}
                for group_name, dimensions in sorted(groups.items())
            ],
        }
```

- [ ] **Step 3: Add `/api/tasks/<task_id>/results` route**

Add route:

```python
@app.get("/api/tasks/<task_id>/results")
def api_unified_results(task_id: str):
    page = clean_non_negative_int(request.args.get("page"), 0)
    limit = clean_positive_int(request.args.get("limit"), 1)
    filters = parse_filters(request.args.get("filters"))
    include_filter_options = clean_bool(request.args.get("include_filter_options", True))
    total, results = store.get_unified_results(task_id, offset=page * limit, limit=limit, filters=filters)
    payload = {
        "total": total,
        "page": page,
        "limit": limit,
        "results": results,
    }
    if include_filter_options:
        payload["filter_options"] = store.get_unified_filter_options(task_id)
    return jsonify(payload)
```

- [ ] **Step 4: Run route test**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_api_exposes_unified_results_and_label_edit -q
```

Expected: PASS.

### Task 5: Frontend State and Unified Fetch Path

**Files:**
- Modify: `web/annotations_v2/static/app.js`
- Modify: `web/annotations_v2/templates/visualize.html`

- [ ] **Step 1: Update task card links**

In `renderTasks`, replace the four result links:

```html
<a class="buttonLike ghost" href="${`/dataset/visualize/${task.id}`}">结果展示</a>
```

Remove the rough/fine/sample/label result links from the task actions.

- [ ] **Step 2: Remove stage tabs from template**

In `web/annotations_v2/templates/visualize.html`, delete:

```html
<nav id="visualizationStageTabs" class="stageTabs" aria-label="可视化阶段"></nav>
```

Keep the pager and filter button.

- [ ] **Step 3: Change visualization state defaults**

In `state`, replace stage-specific result fields with:

```javascript
  visualizationResults: [],
  visualizationPage: 0,
  visualizationTotal: 0,
  visualizationFilters: { statuses: [], mos: [], has_defect: [], annotators: [], labels: {} },
  visualizationFilterOptions: { statuses: [], mos: [], has_defect: [], annotators: [], label_options: [] },
```

Remove `visualizationStage`.

- [ ] **Step 4: Update fetch URL**

Change `reloadVisualizationResults` to call unified results:

```javascript
async function reloadVisualizationResults(options = {}) {
  const params = new URLSearchParams({
    page: String(state.visualizationPage),
    limit: "1",
  });
  if (options.includeFilterOptions === false) {
    params.set("include_filter_options", "0");
  }
  if (hasActiveVisualizationFilters()) {
    params.set("filters", JSON.stringify(buildVisualizationFilterPayload()));
  }
  const data = await api(`/api/tasks/${state.taskId}/results?${params.toString()}`);
  state.visualizationResults = data.results || [];
  state.visualizationTotal = Number(data.total || 0);
  state.visualizationFilterOptions = data.filter_options || state.visualizationFilterOptions;
  if (state.visualizationPage >= state.visualizationTotal) {
    state.visualizationPage = Math.max(0, state.visualizationTotal - 1);
    if (state.visualizationTotal > 0) {
      await reloadVisualizationResults(options);
    }
  }
}
```

- [ ] **Step 5: Update preload to skip filter options**

Change `preloadVisualizationPageImages` to:

```javascript
async function preloadVisualizationPageImages(page) {
  const params = new URLSearchParams({
    page: String(page),
    limit: "1",
    include_filter_options: "0",
  });
  if (hasActiveVisualizationFilters()) {
    params.set("filters", JSON.stringify(buildVisualizationFilterPayload()));
  }
  const data = await api(`/api/tasks/${state.taskId}/results?${params.toString()}`);
  const item = (data.results || [])[0];
  if (!item) return;
  preloadImage(item.image_urls?.src || `/api/tasks/${state.taskId}/images/${item.item_index}/src`);
  preloadImage(item.image_urls?.dst || `/api/tasks/${state.taskId}/images/${item.item_index}/dst`);
}
```

- [ ] **Step 6: Remove stage switching calls**

Delete `renderVisualizationStageTabs`, `switchVisualizationStage`, `normalizeVisualizationStage`, and `visualizationStageTitle`.

Update `openVisualizationPage`:

```javascript
async function openVisualizationPage() {
  state.visualizationPage = 0;
  state.activeTask = taskById(state.taskId) || { id: state.taskId, name: state.taskId };
  await reloadVisualizationResults();
  renderVisualizationPage();
}
```

- [ ] **Step 7: Run syntax check**

Run:

```bash
node --check web/annotations_v2/static/app.js
```

Expected: no output and exit code 0.

### Task 6: Unified Result Rendering and Inline Label Editors

**Files:**
- Modify: `web/annotations_v2/static/app.js`
- Modify: `web/annotations_v2/static/styles.css`

- [ ] **Step 1: Update result page title**

In `renderVisualizationPage`, set:

```javascript
$("visualizationTitle").textContent = `${state.activeTask?.name || state.taskId} · 结果展示`;
```

Remove branches that call stage-specific renderers and replace with:

```javascript
renderUnifiedResultPanel(item);
```

- [ ] **Step 2: Add unified panel renderer**

Add:

```javascript
function renderUnifiedResultPanel(item) {
  $("visualizationResultPanel").innerHTML = `
    <h2>结果</h2>
    <div class="badgeRow">
      ${statusBadge("粗筛", item.status?.rough_passed, item.status?.rough_completed)}
      ${statusBadge("精筛", item.status?.fine_passed, item.status?.fine_completed)}
      ${resultBadge(item.sampled ? "已采样" : "未采样", item.sampled ? "pass" : "")}
      ${resultBadge(item.label ? "已编辑标签" : "未编辑标签", item.label ? "pass" : "")}
    </div>
    ${renderScreeningRecord("粗筛聚合", item.rough)}
    ${renderScreeningRecord("精筛聚合", item.fine)}
    <section class="resultBlock">
      <h3>采样</h3>
      <div class="tagRows">
        ${resultRow("状态", item.sampled ? "已采样" : "未采样")}
        ${resultRow("采样桶", item.sample_bucket || "未分组")}
      </div>
    </section>
    ${renderEditableLabels(item)}
    ${renderLabelRevisionHistory(item)}
  `;
}

function statusBadge(label, passed, completed) {
  if (passed) return resultBadge(`${label}通过`, "pass");
  if (completed) return resultBadge(`${label}未通过`, "fail");
  return resultBadge(`${label}未完成`);
}
```

- [ ] **Step 3: Add editable labels renderer**

Add:

```javascript
function resultLabelPaths(item) {
  const selected = state.activeTask?.selected_label_paths || [];
  if (selected.length) return selected;
  return flattenLabelRows(item.effective_labels || {}).map((row) => row.parts || row.path.split("/"));
}

function renderEditableLabels(item) {
  const labels = item.effective_labels || item.original_labels || {};
  const paths = resultLabelPaths(item);
  return `
    <section class="resultBlock">
      <h3>标签</h3>
      <div class="tagRows editableLabelRows">
        ${paths.map((path) => renderEditableLabelRow(path, getNested(labels, path), item)).join("") || '<div class="metaText">暂无标签</div>'}
      </div>
    </section>
  `;
}

function renderEditableLabelRow(path, value, item) {
  const labelMeta = item.label || {};
  return `
    <div class="tagRow resultEditableLabelRow" data-result-label-path="${escapeHtml(JSON.stringify(path))}">
      <div class="tagKey">${escapeHtml(path.join("/"))}</div>
      <button class="tagValue resultLabelValue" type="button">${escapeHtml(value ?? "未选择")}</button>
      <div class="tagMeta">${escapeHtml(labelMeta.username || "")}${labelMeta.updated_at ? ` · ${escapeHtml(formatTimestamp(labelMeta.updated_at))}` : ""}</div>
    </div>
  `;
}
```

- [ ] **Step 4: Add editor logic**

Add:

```javascript
function currentVisualizationItem() {
  return state.visualizationResults[0] || null;
}

function findResultLabelDimension(path) {
  const [groupName, ...dimensionParts] = path;
  const dimensionName = dimensionParts.join("/");
  const group = (state.activeTask?.label_option_groups || []).find((entry) => entry.name === groupName);
  return (group?.dimensions || []).find((entry) => entry.name === dimensionName) || null;
}

function beginVisualizationLabelEdit(row) {
  const item = currentVisualizationItem();
  if (!item) return;
  const path = JSON.parse(row.dataset.resultLabelPath || "[]");
  const button = row.querySelector(".resultLabelValue");
  if (!button) return;
  const currentValue = getNested(item.effective_labels || {}, path);
  const dimension = findResultLabelDimension(path);
  const editor = renderResultSelectEditor(dimension?.options || [], currentValue);
  button.replaceWith(editor);
  editor.focus();
  let saved = false;
  const save = () => {
    if (saved) return;
    saved = true;
    const nextLabels = mergeLabelObjects(item.effective_labels || {}, {});
    setNested(nextLabels, path, editor.value);
    saveVisualizationLabels(item, nextLabels).catch((error) => showToast(error.message));
  };
  editor.addEventListener("change", save);
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Enter") save();
    if (event.key === "Escape") renderVisualizationPage();
  });
  editor.addEventListener("blur", save, { once: true });
}

async function saveVisualizationLabels(item, labels) {
  const data = await api(`/api/tasks/${state.taskId}/results/${item.item_index}/labels`, {
    method: "POST",
    body: JSON.stringify({ username: state.username, labels }),
  });
  item.label = {
    username: data.record.username,
    labels: data.record.labels,
    updated_at: data.record.updated_at,
  };
  item.effective_labels = data.record.labels;
  item.label_revisions = data.record.label_revisions || [];
  renderVisualizationPage();
  showToast("标签已保存");
}
```

- [ ] **Step 5: Render revision history**

Add:

```javascript
function renderLabelRevisionHistory(item) {
  const revisions = item.label_revisions || [];
  return `
    <details class="resultBlock revisionHistory">
      <summary>编辑历史 ${revisions.length}</summary>
      <div class="revisionList">
        ${revisions.map(renderLabelRevision).join("") || '<div class="metaText">暂无编辑历史</div>'}
      </div>
    </details>
  `;
}

function renderLabelRevision(revision) {
  return `
    <article class="revisionItem">
      <strong>${escapeHtml(revision.username || "未知用户")}</strong>
      <span>${escapeHtml(formatTimestamp(revision.updated_at))}</span>
      ${renderLabelRows("修改前", revision.before)}
      ${renderLabelRows("修改后", revision.after)}
    </article>
  `;
}
```

- [ ] **Step 6: Bind label edit click**

In `bindEvents`, add:

```javascript
$("visualizationResultPanel")?.addEventListener("click", (event) => {
  const row = event.target.closest("[data-result-label-path]");
  if (!row || event.target.closest(".qcInlineEditor")) return;
  beginVisualizationLabelEdit(row);
});
```

- [ ] **Step 7: Add CSS**

Add to `web/annotations_v2/static/styles.css`:

```css
.resultEditableLabelRow {
  grid-template-columns: minmax(120px, 1fr) minmax(120px, 1.2fr) minmax(120px, auto);
}

.resultLabelValue {
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  padding: 0;
  cursor: pointer;
}

.tagMeta {
  color: var(--muted);
  font-size: 12px;
  text-align: right;
}

.qcInlineEditor {
  width: 100%;
  min-height: 32px;
}

.revisionHistory summary {
  cursor: pointer;
  font-weight: 700;
}

.revisionList {
  display: grid;
  gap: 12px;
  margin-top: 12px;
}

.revisionItem {
  border-top: 1px solid var(--border);
  padding-top: 12px;
}
```

- [ ] **Step 8: Run syntax check**

Run:

```bash
node --check web/annotations_v2/static/app.js
```

Expected: PASS.

### Task 7: Unified Filters Frontend

**Files:**
- Modify: `web/annotations_v2/static/app.js`

- [ ] **Step 1: Update empty filters**

Change:

```javascript
function emptyVisualizationFilters() {
  return { statuses: [], mos: [], has_defect: [], annotators: [], labels: {} };
}
```

- [ ] **Step 2: Include statuses in payload**

Change `hasActiveVisualizationFilters` and `buildVisualizationFilterPayload` so `statuses` is included:

```javascript
function hasActiveVisualizationFilters() {
  const payload = buildVisualizationFilterPayload();
  return Boolean(
    payload.statuses.length ||
    payload.mos.length ||
    payload.has_defect.length ||
    payload.annotators.length ||
    payload.labels.some((filter) => filter.values.length)
  );
}

function buildVisualizationFilterPayload() {
  return {
    statuses: state.visualizationFilters.statuses || [],
    mos: state.visualizationFilters.mos || [],
    has_defect: state.visualizationFilters.has_defect || [],
    annotators: state.visualizationFilters.annotators || [],
    labels: Object.entries(state.visualizationFilters.labels || {}).map(([key, entry]) => {
      const normalized = normalizeVisualizationLabelFilter(entry);
      return { path: JSON.parse(key), values: normalized.values };
    }),
  };
}
```

- [ ] **Step 3: Render status filters**

In `renderVisualizationFilterPanel`, add before MOS:

```javascript
body.appendChild(renderVisualizationFilterGroup("状态", options.statuses || [], "statuses", state.visualizationFilters.statuses));
```

- [ ] **Step 4: Collect status filters**

In `collectVisualizationFilters`, add:

```javascript
if (input.dataset.visualizationFilterType === "statuses") {
  filters.statuses.push(value);
  return;
}
```

- [ ] **Step 5: Run syntax check**

Run:

```bash
node --check web/annotations_v2/static/app.js
```

Expected: PASS.

### Task 8: Export Compatibility and Storage Documentation

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`
- Modify: `web/annotations_v2/STORAGE.md`

- [ ] **Step 1: Add export test for revisions**

Append:

```python
def test_v2_export_includes_label_revisions(tmp_path):
    from web.annotations_v2.app import AnnotationV2Store

    jsonl_path = tmp_path / "pairs.jsonl"
    write_jsonl(
        jsonl_path,
        [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg", "labels": {"输入图": {"菜品种类": "中餐"}}}],
    )
    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task({"name": "export-revisions", "root_dir": str(tmp_path), "jsonl_path": str(jsonl_path)})
    store.save_result_labels(task["id"], 0, {"username": "alice", "labels": {"输入图": {"菜品种类": "西餐"}}})

    rows = [json.loads(line) for line in store.export_jsonl(task["id"]).splitlines()]

    assert rows[0]["corrected_labels"] == {"输入图": {"菜品种类": "西餐"}}
    assert rows[0]["label_username"] == "alice"
    assert rows[0]["label_revisions"][0]["username"] == "alice"
```

- [ ] **Step 2: Update export row**

In `export_jsonl`, add:

```python
"label_revisions": deepcopy(record.get("label_revisions", [])) if isinstance(record.get("label_revisions"), list) else [],
```

Keep existing `corrected_labels`, `label_username`, and `label_updated_at`.

- [ ] **Step 3: Document record format**

Append to `web/annotations_v2/STORAGE.md`:

```markdown
### Label Revision History

Unified result-page label edits update the current `record.label` snapshot and append an entry to `record.label_revisions`.

Each revision contains:

- `id`: unique revision id.
- `username`: editor username from the active session.
- `updated_at`: Unix timestamp.
- `before`: effective labels before the edit.
- `after`: saved labels after the edit.
- `source`: currently `unified_results`.
```

- [ ] **Step 4: Run export test**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py::test_v2_export_includes_label_revisions -q
```

Expected: PASS.

### Task 9: Full Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run backend tests**

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend syntax check**

Run:

```bash
node --check web/annotations_v2/static/app.js
```

Expected: PASS.

- [ ] **Step 3: Start local app for manual smoke**

Run:

```bash
ANNOTATIONS_V2_PORT=5065 python -m web.annotations_v2.app
```

Expected: server starts on `http://127.0.0.1:5065`.

- [ ] **Step 4: Manual smoke checklist**

Open `http://127.0.0.1:5065`, log in, open a task result page, and verify:

- The task card has one result entry.
- The result page has no stage tabs.
- One page loads one item and next/previous pagination works.
- Filters work for status, MOS, annotator, and labels.
- Editing a label dropdown saves and updates the displayed editor/time.
- Editing the same label as another user appends another revision.
- Images load via `/api/tasks/<task_id>/images/<item_index>/<src|dst>` and are not inlined in JSON.

- [ ] **Step 5: Optional compatibility check**

Open an old URL like:

```text
/dataset/visualize/<task_id>?stage=rough
```

Expected: it renders the unified result page and ignores `stage`.

## Self-Review

- Spec coverage: unified result page, no stage distinction in UI, dropdown label editing, editor attribution, revision history, and remote efficiency are covered.
- Placeholder scan: no placeholder steps are present.
- Type consistency: backend uses `effective_labels`, `label_revisions`, `statuses`, and `save_result_labels` consistently across tests, APIs, and frontend plan.
