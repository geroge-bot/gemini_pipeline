# Annotation V2 Task Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an edit dialog for existing annotation v2 tasks so issue types and label correction paths can be changed after task creation.

**Architecture:** The task configuration remains in `web/annotations_v2/data/state.json`. A new store method and PATCH route update only `rough.issue_options` and `selected_label_paths`; existing stage-item APIs already read those fields at request time, so refreshed pages use the edited values.

**Tech Stack:** Flask, vanilla JavaScript, pytest, existing annotation v2 templates and styles.

---

### Task 1: Backend Store and API

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Write failing store and API tests**

Add tests that create a task, update issue options and label paths, verify persistence, call PATCH, and confirm label-stage drafts use the edited path.

- [ ] **Step 2: Run backend tests to verify failure**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_update_task_properties_persists_issue_options_and_label_paths tests/test_annotations_v2_app.py::test_v2_update_task_api_refreshes_stage_configuration -q`

Expected: FAIL because `update_task` and `PATCH /api/tasks/<task_id>` do not exist.

- [ ] **Step 3: Implement minimal backend**

Add `AnnotationV2Store.update_task`. Normalize `issue_options` with `normalize_issue_options` and `selected_label_paths` with `normalize_label_paths`. Add the Flask PATCH route.

- [ ] **Step 4: Run backend tests**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_update_task_properties_persists_issue_options_and_label_paths tests/test_annotations_v2_app.py::test_v2_update_task_api_refreshes_stage_configuration -q`

Expected: PASS.

### Task 2: Frontend Edit Dialog

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/templates/index.html`
- Modify: `web/annotations_v2/static/app.js`
- Modify: `web/annotations_v2/static/styles.css`

- [ ] **Step 1: Write failing frontend static test**

Add a test that asserts the task card has `data-action="edit"`, the page has edit dialog fields, the script preloads current task values, and save uses PATCH.

- [ ] **Step 2: Run frontend test to verify failure**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_exposes_task_edit_dialog_for_issue_and_label_paths -q`

Expected: FAIL because the edit dialog is not present.

- [ ] **Step 3: Implement minimal frontend**

Add modal markup, task-card edit button, helpers to serialize and parse paths, event bindings, PATCH call, and dialog styles.

- [ ] **Step 4: Run frontend test and syntax check**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_exposes_task_edit_dialog_for_issue_and_label_paths -q`

Expected: PASS.

Run: `node --check web/annotations_v2/static/app.js`

Expected: exit 0.

### Task 3: Full Verification

**Files:**
- Verify: `tests/test_annotations_v2_app.py`
- Verify: `web/annotations_v2/static/app.js`

- [ ] **Step 1: Run full focused test file**

Run: `python -m pytest tests/test_annotations_v2_app.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run JavaScript syntax check**

Run: `node --check web/annotations_v2/static/app.js`

Expected: exit 0.
