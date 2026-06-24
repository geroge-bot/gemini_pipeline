# Annotations V2 Storage

This document describes the durable storage layout used by `web/annotations_v2`.

## Layout

Each task has an independent data directory:

```text
data/tasks/<task_id>/
  items.json
  records/
    <item_index>.json
```

`state.json` stores task metadata and points each task at its `data_dir`.

## `items.json`

`items.json` is the imported item table. It is written when a task is created and is not rewritten during normal annotation saves.

Each row stores:

- `item_index`
- `src_image`
- `dst_image`
- imported `labels`
- optional generation prompt fields

## `records/<item_index>.json`

Each file under `records/` stores the workflow state for one item. Normal rough, fine, and label saves write only the current item's record file.

Typical fields are:

- `rough_annotations` and aggregated `rough`
- `fine_annotations` and aggregated `fine`
- `sampled`
- `sample_bucket`
- `label`
- temporary `label_claim`

Bulk operations such as sampling and JSONL import may update multiple item record files.

## Legacy Compatibility

Older tasks may still have `records.json`, a single object keyed by `item_index`. The store reads this file as a legacy baseline and then overlays any newer `records/<item_index>.json` files.

New saves are written to `records/<item_index>.json`; they do not rewrite `records.json`. This allows existing tasks to move to per-item storage gradually as users continue annotating.

## Concurrency

Per-item saves lock the target item record file. Saves to different items can proceed without rewriting a shared task-wide JSON file.

Task-wide operations still read all records and write the affected item files. They should remain administrative or lower-frequency operations compared with normal annotation saves.
