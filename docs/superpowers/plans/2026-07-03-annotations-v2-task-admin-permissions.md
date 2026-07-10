# Annotations V2 Task Admin Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide task creation controls from non-admin users and reject non-admin task creation requests in `annotations_v2`.

**Architecture:** Reuse the existing `孙本猿` task deletion admin identity as the task-management admin identity. The frontend uses one shared helper to decide whether task-management controls are visible, and the backend uses one shared guard for destructive or creation task-management APIs.

**Tech Stack:** Flask, vanilla JavaScript, pytest, `node --check`.

---

### Task 1: Backend Create Permission

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/app.py`

- [ ] **Step 1: Write failing API tests**

Add tests proving `POST /api/tasks` rejects `alice` with 403 and accepts `孙本猿` with 201 when the same existing task payload is supplied with `username`.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_create_task_api_requires_admin_username -q`
Expected: FAIL because the endpoint currently ignores `username` and allows non-admin creation.

- [ ] **Step 3: Implement backend guard**

Rename the task delete admin concept to task admin, keep the existing username value, and call the guard from both `api_create_task` and `api_delete_task`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_create_task_api_requires_admin_username tests/test_annotations_v2_app.py::test_v2_delete_task_api_requires_admin_username_and_unregisters_only -q`
Expected: PASS.

### Task 2: Frontend Create Form Visibility

**Files:**
- Modify: `tests/test_annotations_v2_app.py`
- Modify: `web/annotations_v2/static/app.js`

- [ ] **Step 1: Write failing frontend assertions**

Add assertions that the script defines a shared `canManageTasks()` helper, uses it for deletion, and calls an `updateTaskManagementVisibility()` function that toggles `createTaskForm`.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_hides_task_creation_for_non_admin_users -q`
Expected: FAIL because the frontend only has deletion-specific permission logic.

- [ ] **Step 3: Implement frontend visibility**

Add `canManageTasks()`, `updateTaskManagementVisibility()`, call it during session updates and rendering, and include `username` in the task creation payload.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_frontend_hides_task_creation_for_non_admin_users -q`
Expected: PASS.

### Task 3: Final Verification

**Files:**
- Test: `tests/test_annotations_v2_app.py`
- Test: `web/annotations_v2/static/app.js`

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_annotations_v2_app.py::test_v2_create_task_api_requires_admin_username tests/test_annotations_v2_app.py::test_v2_delete_task_api_requires_admin_username_and_unregisters_only tests/test_annotations_v2_app.py::test_v2_frontend_hides_task_creation_for_non_admin_users -q`
Expected: PASS.

- [ ] **Step 2: Check JavaScript syntax**

Run: `node --check web/annotations_v2/static/app.js`
Expected: no output and exit code 0.
