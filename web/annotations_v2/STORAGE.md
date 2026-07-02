# Annotations V2 Storage

This document describes the durable storage layout used by `web/annotations_v2`.

## Layout

Each task has an independent data directory:

```text
data/tasks/<task_id>/
  items.json
  records/
    <item_index>.json.gz
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

## `records/<item_index>.json.gz`

Each file under `records/` stores the workflow state for one item. Normal rough, fine, and label saves write only the current item's gzip-compressed record file.

Typical fields are:

- `rough_annotations` and aggregated `rough`
- `fine_annotations` and aggregated `fine`
- `sampled`
- `sample_bucket`
- `label`
- `label_revisions`
- temporary `label_claim`

Bulk operations such as sampling and JSONL import may update multiple item record files.

### Label Revision History

Unified result-page label edits update the current `label` snapshot and append an entry to `label_revisions`.

Each revision contains:

- `id`: unique revision id.
- `username`: editor username from the active session.
- `updated_at`: Unix timestamp.
- `before`: effective labels before the edit.
- `after`: saved labels after the edit.
- `source`: currently `unified_results`.

## Legacy Compatibility

Older tasks may still have `records.json`, a single object keyed by `item_index`, or uncompressed `records/<item_index>.json` shards. The store reads `records.json` as a legacy baseline, overlays uncompressed shards, then overlays compressed `records/<item_index>.json.gz` shards.

New saves are written to `records/<item_index>.json.gz`; they do not rewrite `records.json`. If an older uncompressed shard exists for the same item, a successful compressed save removes that stale `.json` shard. This allows existing tasks to move to compressed per-item storage gradually as users continue annotating.

## Concurrency

Per-item saves lock the target item record file. Saves to different items can proceed without rewriting a shared task-wide JSON file.

Task-wide operations still read all records and write the affected item files. They should remain administrative or lower-frequency operations compared with normal annotation saves.
