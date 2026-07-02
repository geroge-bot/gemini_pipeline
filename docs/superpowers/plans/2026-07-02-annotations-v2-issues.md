# Annotations V2 Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add v1-style task-scoped issue creation, discussion, status changes, and Markdown export to annotations v2 result display.

**Architecture:** Store issues in each v2 task data directory as `issues.json`, separate from per-item gzip records, so task-level issue lists do not scan all records. Create issue snapshots from the existing unified result row to preserve the exact result context while keeping links back to the live item.

**Tech Stack:** Flask, vanilla JavaScript, JSON file storage, pytest.

---

## File Structure

- Modify `web/annotations_v2/app.py`: issue file helpers, store methods, Flask issue routes.
- Modify `web/annotations_v2/templates/visualize.html`: result-page issue button, issue modal, task issue view.
- Modify `web/annotations_v2/static/app.js`: issue list/detail rendering, create/answer/status/export flows, bbox insertion.
- Modify `web/annotations_v2/static/styles.css`: issue list/detail/modal and selection overlay styles.
- Modify `tests/test_annotations_v2_app.py`: backend issue contract, API behavior, frontend marker tests.
- Modify `web/annotations_v2/STORAGE.md`: document `issues.json`.

## Task 1: Backend Issue Contract

- [ ] Add failing tests for issue creation, assignee selection from unified result data, snapshot shape, answer/status changes, Markdown export, and API routes.
- [ ] Run targeted issue tests and confirm they fail because v2 issue methods/routes do not exist.
- [ ] Implement task-level `issues.json` helpers and issue store methods in `AnnotationV2Store`.
- [ ] Add Flask routes under `/api/tasks/<task_id>/issues`.
- [ ] Run targeted backend tests and confirm they pass.

## Task 2: Frontend Issue UI

- [ ] Add failing marker tests for result-page issue action, issue modal, task issue view, answer form, Markdown export, and bbox helpers.
- [ ] Update the visualize template with the result action, issue view, and modal markup.
- [ ] Add JS state and functions for opening issues, rendering list/detail, creating issues, answering, closing/reopening, jumping to the result item, exporting Markdown, and bbox insertion.
- [ ] Add CSS for the issue layout and image selection overlay.
- [ ] Run targeted frontend marker tests and confirm they pass.

## Task 3: Documentation and Verification

- [ ] Document `issues.json` in `web/annotations_v2/STORAGE.md`.
- [ ] Run `python -m pytest tests/test_annotations_v2_app.py -q`.
- [ ] Run `node --check web/annotations_v2/static/app.js`.
