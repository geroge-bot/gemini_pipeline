# Annotations V2 Remote Deployment

## Storage

Keep `state.json`, task data, record shards, and preview cache on local SSD/NVMe. Do not put SQLite or thousands of gzip shards on NFS unless its advisory-lock and latency behavior has been verified.

Configure explicit durable locations:

```bash
export ANNOTATIONS_V2_STATE_PATH=/srv/annotations/state.json
export ANNOTATIONS_V2_DATA_DIR=/srv/annotations/tasks
export ANNOTATIONS_V2_PREVIEW_CACHE_DIR=/var/cache/annotations-v2/previews
export ANNOTATIONS_V2_SQLITE_RECORD_CACHE=1
```

Existing tasks store absolute paths. Copy/mount the data at its new locations first, then preview metadata changes before applying them:

```bash
python scripts/migrate_annotations_v2_paths.py \
  --state-path /srv/annotations/state.json \
  --map '/Users/george/data=/srv/datasets' \
  --data-root /srv/annotations/tasks
```

Add `--apply` only after checking the printed changes. The command creates a timestamped backup before writing.

## Authentication

The browser username remains useful for local trusted use. Remote deployment should put the app behind an authenticating reverse proxy that removes any client-supplied identity header and injects its own trusted header:

```bash
export ANNOTATIONS_V2_AUTH_USER_HEADER=X-Remote-User
export ANNOTATIONS_V2_AUTH_REQUIRED=1
```

When configured, the server rejects missing identities and overrides usernames supplied in JSON or query parameters with the trusted header value.

## WSGI and logging

The file transaction locks and persisted preview jobs support multiple worker processes on one server. Start conservatively and measure before increasing concurrency:

```bash
export ANNOTATIONS_V2_LOG_LEVEL=INFO
export ANNOTATIONS_V2_PREVIEW_CACHE_WORKERS=4
gunicorn --workers 2 --threads 4 --bind 127.0.0.1:5065 web.annotations_v2.app:app
```

Use Nginx or another reverse proxy for TLS, authentication, request limits, and static-file delivery. Prewarm previews before annotation sessions. Cache-hit preview responses use a configurable short browser cache:

```bash
export ANNOTATIONS_V2_IMAGE_CACHE_MAX_AGE=300
```

For multiple application servers, move task state, annotations, claims, issues, and jobs to PostgreSQL or another transactional shared database.
