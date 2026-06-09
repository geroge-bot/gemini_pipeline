# Annotation V2 Task Editing Design

## Goal

Allow users to edit selected properties of an existing `annotations_v2` task after creation. The editable properties are the rough-screening issue types and the label paths used for label correction.

## Scope

The feature edits task configuration only. It does not rewrite existing rough, fine, sampling, or label records. After saving, subsequent API responses and annotation pages must use the updated configuration:

- Rough and fine annotation forms render issue checkboxes from the updated `rough.issue_options`.
- Label correction uses the updated `selected_label_paths` to build label drafts and choice groups.
- Task list payloads return the updated task metadata.

## Backend Design

Add `AnnotationV2Store.update_task(task_id, payload)`.

The method reads the task from `state.json`, normalizes the provided fields with the existing helpers, writes the updated state, and returns the standard task payload. It accepts:

- `rough.issue_options` or top-level `issue_options`.
- `selected_label_paths`.

Existing task fields such as paths, thresholds, annotator counts, item files, and records remain unchanged.

Add `PATCH /api/tasks/<task_id>` and return `{ "task": ... }`.

## Frontend Design

Add an edit button to each task card. Clicking it opens a modal dialog on the home page. The dialog preloads:

- Problem types as newline-separated text from `task.rough.issue_options`.
- Label correction paths as newline-separated `group/dimension` paths from `task.selected_label_paths`.

Saving sends a PATCH request, closes the dialog, refreshes tasks, and shows a toast. Cancel, close, or clicking the overlay dismisses the dialog without saving.

## Testing

Add tests in `tests/test_annotations_v2_app.py` for:

- Store-level updates normalize and persist both fields.
- PATCH API updates the task and later stage-item payloads reflect the edited label paths.
- Frontend static behavior exposes the edit button, modal, prefill helpers, and PATCH request.

Run:

```bash
python -m pytest tests/test_annotations_v2_app.py -q
node --check web/annotations_v2/static/app.js
```
