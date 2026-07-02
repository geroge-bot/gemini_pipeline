# Annotation V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent `web/annotations_v2` Flask website that supports import, rough screening, fine screening, distribution sampling, label correction, export, and verification.

**Architecture:** Create a new Flask package instead of modifying `web/annotations`. Put durable behavior in `AnnotationV2Store`, keep routes thin, and use a vanilla JavaScript workbench for the browser UI.

**Tech Stack:** Python 3, Flask, PIL/Pillow, pytest, vanilla HTML/CSS/JavaScript.

---

## File Structure

- Create `web/annotations_v2/__init__.py`: package marker.
- Create `web/annotations_v2/app.py`: store, domain rules, Flask routes, image serving, and JSONL export.
- Create `web/annotations_v2/templates/index.html`: single-page workbench shell.
- Create `web/annotations_v2/static/app.js`: task creation, task list, stage queues, save actions, sampling, export.
- Create `web/annotations_v2/static/styles.css`: compact workbench UI.
- Create `tests/test_annotations_v2_app.py`: API and store coverage for the v2 workflow.
- Use `doc/design/annotation_v2/design_report.md` as the behavioral source of truth.

## Task 1: Add failing tests for the v2 workflow

**Files:**
- Create: `tests/test_annotations_v2_app.py`

- [ ] **Step 1: Write tests for task creation, stage gates, sampling, label correction, and export**

```python
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


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_v2_task_creation_loads_jsonl_and_label_files():
    from web.annotations_v2.app import AnnotationV2Store

    tmp_path = make_workspace_tmp()
    jsonl_path = tmp_path / "data.jsonl"
    label_dir = tmp_path / "labels"
    write_jsonl(jsonl_path, [{"src_image": "src/a.jpg", "dst_image": "dst/a.jpg"}])
    write_json(label_dir / "src" / "a.json", {"labels": {"菜品种类": "中餐"}})
    write_json(label_dir / "dst" / "a.json", {"美学评分": 4})

    store = AnnotationV2Store(tmp_path / "state.json")
    task = store.create_task(
        {
            "name": "v2 food",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "label_dir": str(label_dir),
            "selected_label_paths": [["输入图", "菜品种类"]],
        }
    )

    items = store.list_stage_items(task["id"], "rough")
    assert task["name"] == "v2 food"
    assert items[0]["labels"] == {"输入图": {"菜品种类": "中餐"}, "输出图": {"美学评分": 4}}
    assert store.summary(task["id"])["total"] == 1


def test_v2_stage_gates_sampling_label_correction_and_export():
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
            "name": "v2 food",
            "root_dir": str(tmp_path),
            "jsonl_path": str(jsonl_path),
            "selected_label_paths": [["输入图", "菜品种类"]],
            "rough": {"min_mos": 4, "require_no_defect": True, "issue_options": ["主体问题"]},
            "fine": {"min_mos": 4, "enable_defect": True},
        }
    )

    store.save_rough(task["id"], 0, {"username": "alice", "mos": 4, "has_defect": False})
    store.save_rough(task["id"], 1, {"username": "alice", "mos": 5, "has_defect": True, "issues": ["主体问题"]})
    store.save_rough(task["id"], 2, {"username": "alice", "mos": 5, "has_defect": False})

    fine_items = store.list_stage_items(task["id"], "fine")
    assert [item["item_index"] for item in fine_items] == [0, 2]

    store.save_fine(task["id"], 0, {"username": "bob", "mos": 5, "has_defect": False})
    store.save_fine(task["id"], 2, {"username": "bob", "mos": 3, "has_defect": False})

    sample = store.sample(task["id"], {"target_count": 2, "min_per_bucket": 1})
    assert sample["sampled_count"] == 1
    assert sample["buckets"] == [{"bucket": "输入图/菜品种类=中餐", "candidate_count": 1, "sampled_count": 1}]
    assert [item["item_index"] for item in store.list_stage_items(task["id"], "label")] == [0]

    corrected = {"输入图": {"菜品种类": "融合菜"}}
    store.save_label(task["id"], 0, {"username": "carol", "labels": corrected})

    summary = store.summary(task["id"])
    assert summary["rough_passed"] == 2
    assert summary["fine_passed"] == 1
    assert summary["sampled"] == 1
    assert summary["label_completed"] == 1

    rows = [json.loads(line) for line in store.export_jsonl(task["id"]).splitlines()]
    assert rows[0]["rough"]["mos"] == 4
    assert rows[0]["fine"]["mos"] == 5
    assert rows[0]["sampled"] is True
    assert rows[0]["corrected_labels"] == corrected
    assert rows[1]["rough"]["has_defect"] is True


def test_v2_api_exposes_summary_and_stage_endpoints():
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
        create_response = client.post(
            "/api/tasks",
            json={
                "name": "api task",
                "root_dir": str(tmp_path),
                "jsonl_path": str(jsonl_path),
                "selected_label_paths": [["输入图", "菜品种类"]],
            },
        )
        task = create_response.get_json()["task"]
        rough_response = client.post(
            f"/api/tasks/{task['id']}/items/0/rough",
            json={"username": "alice", "mos": 5, "has_defect": False},
        )
        fine_items_response = client.get(f"/api/tasks/{task['id']}/items?stage=fine")

        assert create_response.status_code == 201
        assert rough_response.status_code == 200
        assert fine_items_response.get_json()["items"][0]["item_index"] == 0
        assert client.get(f"/api/tasks/{task['id']}/summary").get_json()["summary"]["rough_passed"] == 1
    finally:
        annotations_v2_app.store = old_store
```

- [ ] **Step 2: Run tests and verify they fail because the new package does not exist**

Run: `python -m pytest tests/test_annotations_v2_app.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'web.annotations_v2'`.

## Task 2: Implement the v2 backend

**Files:**
- Create: `web/annotations_v2/__init__.py`
- Create: `web/annotations_v2/app.py`

- [ ] **Step 1: Add package marker**

```python
"""Annotation V2 web application."""
```

- [ ] **Step 2: Implement `AnnotationV2Store` and routes**

Implement the following concrete behavior in `web/annotations_v2/app.py`:

- `load_jsonl(path)` validates non-empty JSONL rows with `src_image` and `dst_image`.
- `read_image_labels(root_dir, label_dir, image_path)` reads same-name JSON labels.
- `create_task(payload)` writes task metadata, `items.json`, and creates a `records/` directory for compressed per-item record files.
- `list_stage_items(task_id, stage)` returns rough, fine, or label queues based on design rules.
- `save_rough`, `save_fine`, and `save_label` validate gates and persist records.
- `sample(task_id, payload)` selects from fine-passed candidates by selected label path buckets.
- `summary(task_id)` returns total and stage counts.
- `export_jsonl(task_id)` emits one row per item with original labels and all stage fields.
- Flask routes expose the API listed in the design report.

- [ ] **Step 3: Run v2 tests and verify backend passes**

Run: `python -m pytest tests/test_annotations_v2_app.py -q`

Expected: PASS.

## Task 3: Implement the v2 frontend workbench

**Files:**
- Create: `web/annotations_v2/templates/index.html`
- Create: `web/annotations_v2/static/app.js`
- Create: `web/annotations_v2/static/styles.css`

- [ ] **Step 1: Create the HTML shell**

The page must contain:

- Header with username input and refresh action.
- Task creation form.
- Task list section.
- Stage workbench with image panels, metadata panel, and stage-specific form.
- Sampling panel.
- Toast element.

- [ ] **Step 2: Implement JavaScript client**

The script must:

- Load tasks on startup.
- Create tasks with configured rough/fine rules and selected label paths.
- Open rough, fine, and label stages.
- Render current item, stage records, images, labels, and forms.
- Save rough, fine, and label records.
- Run sampling and display bucket distribution.
- Download JSONL through the backend endpoint.

- [ ] **Step 3: Implement CSS**

The CSS must create a dense workbench with stable grids, readable forms, 8px-or-less border radii, and responsive behavior.

- [ ] **Step 4: Check JavaScript syntax**

Run: `node --check web/annotations_v2/static/app.js`

Expected: exit 0.

## Task 4: Verify end to end

**Files:**
- Read/verify all created files.

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest tests/test_annotations_v2_app.py -q`

Expected: PASS.

- [ ] **Step 2: Run JavaScript syntax check**

Run: `node --check web/annotations_v2/static/app.js`

Expected: exit 0.

- [ ] **Step 3: Start the v2 app**

Run: `ANNOTATIONS_V2_PORT=5065 python -m web.annotations_v2.app`

Expected: server listens at `http://127.0.0.1:5065`.

- [ ] **Step 4: Browser smoke test**

Open `http://127.0.0.1:5065`, confirm:

- Page title is `数据标注流程 V2`。
- Task creation form is visible.
- No browser console errors are emitted on initial load.

- [ ] **Step 5: Stop the local server**

Terminate the background server session after browser verification.
