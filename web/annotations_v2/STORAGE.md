# Annotations V2 Storage

This document describes the durable storage layout used by `web/annotations_v2`.

## Layout

Each task has an independent data directory:

```text
data/tasks/<task_id>/
  items.json
  issues.json
  summary.json
  records-cache.sqlite3
  records/
    <item_index>.json.gz
  preview_cache/jobs/
    <job_id>.json
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

## `records-cache.sqlite3`

The gzip shards remain the durable compatibility format. `records-cache.sqlite3` is a disposable WAL-mode read-through cache of the merged legacy/sharded record view. Its metadata stores the source filesystem signature; a mismatch causes the cache to be rebuilt from the JSON/gzip files. This avoids reopening thousands of gzip files after every process restart while preserving compatibility with existing tasks and scripts.

Set `ANNOTATIONS_V2_SQLITE_RECORD_CACHE=0` to disable this cache. Keep it on local storage; do not place it on NFS.

## `issues.json`

`issues.json` stores task-scoped review issues created from the unified result page. It is separate from per-item records so listing and exporting issues does not scan every `records/<item_index>.json.gz` shard.

Each issue stores:

- `id`: unique issue id.
- `status`: `open` or `closed`.
- `title` and `body`: reviewer question.
- `item_index`: referenced task item.
- `created_by`, `assigned_to`, and `assigned_stage`.
- `created_at`, `updated_at`, `closed_at`, and `closed_by`.
- `answers`: discussion entries with `author`, `body`, and `created_at`.
- `snapshot`: the unified result row captured when the issue was created, including image paths, rough/fine records, label state, and workflow status.

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

All read-modify-write operations use stable sidecar lock files plus OS-level advisory locks. The locks are shared by threads and by WSGI worker processes on the same server.

Every mutation for one task first acquires the task mutation lock. Per-item saves then lock the target record. Bulk operations, label claims, imports, sampling, and summary refresh therefore cannot write stale snapshots over normal annotation saves.

Bulk operations write only item indexes whose content actually changed. Label claim creation normally writes one gzip shard rather than rewriting every record in the task.

Preview-cache job state is persisted under `preview_cache/jobs/`, so polling can be served by a different WSGI worker from the worker that started the background job.

These locks are intended for multiple processes on one server with local storage. For multiple application servers or storage whose advisory-lock semantics are uncertain, use a transactional database rather than relying on shared JSON files.
