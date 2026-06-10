# Annotation V2 Startup and Deployment

`web/annotations_v2` is the second-generation annotation site for rough screening, fine screening, distribution sampling, label correction, result visualization, and JSONL import/export.

## Local Startup

From the repository root:

```bash
python -m web.annotations_v2.app
```

Default local URL:

```text
http://127.0.0.1:5065
```

To change the port:

```bash
export ANNOTATIONS_V2_PORT=5066
python -m web.annotations_v2.app
```

The built-in Flask entry point binds to `127.0.0.1`. For LAN or production access, put Nginx in front of the app and keep the Python process bound to localhost.

## Runtime Environment

These environment variables control runtime paths:

```bash
export ANNOTATIONS_V2_STATE_PATH=/srv/annotations_v2/state/state.json
export ANNOTATIONS_V2_DATA_DIR=/srv/annotations_v2/data/tasks
export ANNOTATIONS_V2_PREVIEW_CACHE_DIR=/srv/annotations_v2/preview-cache
export ANNOTATIONS_V2_PORT=5065
```

Meanings:

- `ANNOTATIONS_V2_STATE_PATH`: task registry JSON file. Defaults to `web/annotations_v2/data/state.json`.
- `ANNOTATIONS_V2_DATA_DIR`: task item and record storage directory. Defaults to a `tasks` directory next to the state file.
- `ANNOTATIONS_V2_PREVIEW_CACHE_DIR`: generated image preview cache root. Use a local SSD/NVMe path on Ubuntu.
- `ANNOTATIONS_V2_PORT`: local Flask port. Defaults to `5065`.

Create production directories before startup:

```bash
sudo mkdir -p /srv/annotations_v2/state /srv/annotations_v2/data/tasks /srv/annotations_v2/preview-cache
sudo chown -R ubuntu:www-data /srv/annotations_v2
sudo chmod -R 755 /srv/annotations_v2
```

Replace `ubuntu` with the Linux user that runs the Python app.

## Production App Process

Run the app behind Nginx. One simple Gunicorn command is:

```bash
export ANNOTATIONS_V2_STATE_PATH=/srv/annotations_v2/state/state.json
export ANNOTATIONS_V2_DATA_DIR=/srv/annotations_v2/data/tasks
export ANNOTATIONS_V2_PREVIEW_CACHE_DIR=/srv/annotations_v2/preview-cache
gunicorn 'web.annotations_v2.app:app' \
  --bind 127.0.0.1:5065 \
  --workers 4 \
  --threads 8 \
  --timeout 120
```

The app should stay private on `127.0.0.1`; Nginx serves LAN users on port `80` or `443`.

## Nginx Config Generation

Generate an Nginx server config from the current preview cache environment:

```bash
export ANNOTATIONS_V2_PREVIEW_CACHE_DIR=/srv/annotations_v2/preview-cache
python scripts/configure_annotations_v2_nginx.py \
  --server-name 192.168.1.50 \
  --flask-upstream http://127.0.0.1:5065 \
  --output /tmp/annotations_v2.nginx.conf
```

For domain access, replace `192.168.1.50` with the domain name. For a single internal default site, `--server-name _` is also valid.

Install and reload:

```bash
sudo cp /tmp/annotations_v2.nginx.conf /etc/nginx/sites-available/annotations_v2
sudo ln -sf /etc/nginx/sites-available/annotations_v2 /etc/nginx/sites-enabled/annotations_v2
sudo nginx -t
sudo systemctl reload nginx
```

The generated config maps:

```text
/annotation-assets/ -> $ANNOTATIONS_V2_PREVIEW_CACHE_DIR
```

This lets Nginx serve warmed thumbnail and preview images directly. Flask still handles pages, JSON APIs, saves, import, export, and original-image fallback.

## Preview Cache Workflow

After creating or importing a task, click `缓存图片` on the task card. The cache job creates:

```text
$ANNOTATIONS_V2_PREVIEW_CACHE_DIR/<task_id>/
  manifest.json
  <cache_key>.thumb.jpg
  <cache_key>.preview.jpg
```

Normal image payloads then prefer static URLs like:

```text
/annotation-assets/<task_id>/<cache_key>.preview.jpg
```

If a manifest entry is missing, the frontend falls back to:

```text
/api/tasks/<task_id>/images/<item_index>/<src|dst>
```

## Deployment Verification

Check app health through Nginx:

```bash
curl -I http://192.168.1.50/
```

After warming one task, verify a generated image URL:

```bash
curl -I http://192.168.1.50/annotation-assets/<task_id>/<file>.preview.jpg
```

Expected headers include:

```text
HTTP/1.1 200 OK
Cache-Control: public, max-age=31536000, immutable
```

In the browser network panel, warmed rating and visualization images should request `/annotation-assets/...` rather than `/api/tasks/.../images/...`.

## Troubleshooting

- `/annotation-assets/...` returns `404`: confirm `ANNOTATIONS_V2_PREVIEW_CACHE_DIR` and the Nginx `alias` point to the same directory, then run the `缓存图片` job.
- Static image requests return `403`: confirm the Nginx worker user can read the preview cache directory and files.
- Pages load but saves fail: confirm Nginx proxies `/` to the same Flask/Gunicorn port configured by `--bind`.
- Images still load slowly after warming: confirm browser requests hit `/annotation-assets/...`; if they still hit `/api/tasks/.../images/...`, the task likely has no manifest yet.

## Checks

Before deploying code changes related to Annotation V2, run:

```bash
python -m pytest tests/test_annotations_v2_app.py tests/test_configure_annotations_v2_nginx.py -q
node --check web/annotations_v2/static/app.js
```
