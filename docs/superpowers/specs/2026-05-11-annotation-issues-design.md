# Annotation Issues Design

## Goal

Add task-scoped issue submission, assignment, answering, and Markdown export to `web/annotations`.

## Design

Each annotation task stores issues in `data/tasks/<task_id>/issues.json`. An issue references one annotated result by `item_index`, keeps a snapshot of the current annotation and image paths, and tracks `open` or `closed` status. Creating an issue from the result page assigns it to the original annotator for that result.

The UI adds an issue entry point on task cards, a result-page "Create Issue" action, and a task-level issue view. The issue view shows a GitHub-like list and a detail panel with the referenced annotation, images, discussion, and links back to the result page item.

Answers are plain text that may include image region references inserted by drag-selecting a source or destination image. Region references use normalized image coordinates:

```text
[dst: x=0.123 y=0.245 w=0.320 h=0.180]
```

Markdown export emits all issues with metadata, image paths, annotation snapshots, and answers so a later model can analyze the full issue set.

