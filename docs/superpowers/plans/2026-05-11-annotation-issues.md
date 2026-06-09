# Annotation Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build task-scoped issue creation, assignment, answering, closing, and Markdown export for the annotations app.

**Architecture:** Extend `AnnotationStore` with `issues.json` helpers and issue methods. Add Flask endpoints for issue CRUD-style actions. Add one front-end issue view plus result-page issue creation and region-selection helpers.

**Tech Stack:** Flask, JSON file storage, vanilla JavaScript, CSS, pytest.

---

### Task 1: Back-End Issue Contract

**Files:**
- Modify: `tests/test_annotations_app.py`
- Modify: `web/annotations/app.py`

- [ ] Write failing tests for issue creation, assignment, answer storage, state changes, and Markdown export.
- [ ] Run `python -m pytest tests/test_annotations_app.py -k issue -q` and verify the tests fail because issue methods/endpoints are missing.
- [ ] Add `AnnotationStore` helpers for reading/writing `issues.json`.
- [ ] Add `create_issue`, `list_issues`, `add_issue_answer`, `close_issue`, `reopen_issue`, and `export_issues_markdown`.
- [ ] Add Flask routes under `/api/tasks/<task_id>/issues`.
- [ ] Run the issue tests and then the full annotations test file.

### Task 2: Front-End Issue UI

**Files:**
- Modify: `web/annotations/templates/index.html`
- Modify: `web/annotations/static/app.js`
- Modify: `web/annotations/static/styles.css`
- Modify: `tests/test_annotations_app.py`

- [ ] Add front-end marker tests for task-card Issue entry, result-page create action, issue view, answer form, and bbox insertion code.
- [ ] Run the marker tests and verify they fail.
- [ ] Add the issue view and modal markup.
- [ ] Add state, API calls, rendering, navigation, create modal, answer submission, close/reopen actions, result-link navigation, and Markdown export trigger.
- [ ] Add CSS for issue list, detail panel, modal, and image selection overlays.
- [ ] Run `node --check web/annotations/static/app.js` and the front-end marker tests.

### Task 3: Final Verification

**Files:**
- Verify: `tests/test_annotations_app.py`
- Verify: `web/annotations/static/app.js`

- [ ] Run `python -m pytest tests/test_annotations_app.py`.
- [ ] Run `node --check web/annotations/static/app.js`.
- [ ] Review `git diff -- web/annotations tests docs`.

