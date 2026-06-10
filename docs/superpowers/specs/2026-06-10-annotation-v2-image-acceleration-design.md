# Annotation V2 Image Acceleration Design

## Goal

Improve image loading speed for `web/annotations_v2` when deployed on a remote Ubuntu server where source images are stored on the server's local disk.

The target experience is that annotators can move through image pairs without visible blank waits after preview caches have been warmed. Flask should handle task state and JSON APIs, while large image bytes should be served by Nginx from static preview files.

## Current State

`annotations_v2` already has three useful acceleration pieces:

- The image endpoint returns resized previews by default and original files only when `original=1` is requested.
- Resized previews are cached on disk using a content-derived cache key.
- The frontend preloads nearby image pairs for rating and visualization pages.

The remaining bottleneck is the delivery path. Every image still goes through `/api/tasks/<task_id>/images/<item_index>/<kind>`, which means Flask resolves the task and item, checks the file path, may touch the preview cache, and streams the image response. On a remote deployment this consumes Python worker capacity and adds avoidable latency for every image request.

## Scope

This design covers:

- Static preview asset generation for annotation images.
- A manifest-backed URL model for image variants.
- Nginx direct serving of preview assets.
- Frontend two-phase image loading using thumbnail and preview variants.
- Cache status visibility and fallback behavior.

This design does not cover:

- CDN or object storage.
- Authentication changes beyond an optional Nginx `X-Accel-Redirect` path.
- Changes to the imported JSONL format.
- Changes to annotation records or export format.

## Proposed Architecture

Add a static preview asset layer between task items and browser image requests.

For each task item image, generate two derived variants:

- `thumb`: 512 px max edge for immediate placeholder display.
- `preview`: 1024 px max edge for normal annotation work.

Original images remain in their existing source directories and are loaded only when explicitly requested. The normal rating and visualization pages should use the generated static variants.

Generated assets live under the configured preview cache root:

```text
<ANNOTATIONS_V2_PREVIEW_CACHE_DIR>/<task_id>/
  manifest.json
  <cache_key>.thumb.jpg
  <cache_key>.preview.jpg
```

The cache root should be on local SSD/NVMe storage on the Ubuntu server. Because the source images are already on local disk, this removes Python from the hot image-serving path rather than solving a storage problem.

## Manifest

Add a per-task `manifest.json` that maps each image to its generated variants.

Each entry should include:

- `item_index`
- `kind`: `src` or `dst`
- `source_path`
- `source_mtime_ns`
- `source_size`
- `cache_key`
- `variants.thumb.path`
- `variants.thumb.url`
- `variants.thumb.width`
- `variants.thumb.height`
- `variants.thumb.size`
- `variants.preview.path`
- `variants.preview.url`
- `variants.preview.width`
- `variants.preview.height`
- `variants.preview.size`

The manifest is rebuilt or updated by the preview warming job. Cache invalidation follows the existing key strategy: source resolved path, mtime, size, and max edge. If the source file changes, the key changes and the generated static URL changes.

## Backend Design

Extend the existing preview cache system rather than replacing it.

Backend changes:

- Add helpers to generate one specific variant from a source image.
- Update `warm_preview_cache(task_id)` to generate both `thumb` and `preview` variants.
- Write `manifest.json` atomically at the end of a successful warm pass, with partial progress tracked in job state.
- Add store helpers that return image URL payloads from the manifest.
- Update item payloads and visualization rows to return:

```json
{
  "image_urls": {
    "src": "/annotation-assets/<task_id>/<key>.preview.jpg",
    "dst": "/annotation-assets/<task_id>/<key>.preview.jpg",
    "src_thumb": "/annotation-assets/<task_id>/<key>.thumb.jpg",
    "dst_thumb": "/annotation-assets/<task_id>/<key>.thumb.jpg",
    "src_original": "/api/tasks/<task_id>/images/<item_index>/src?original=1",
    "dst_original": "/api/tasks/<task_id>/images/<item_index>/dst?original=1"
  }
}
```

Fallback behavior:

- If a manifest entry is missing, return the existing Flask preview endpoint as `src` or `dst`.
- If a thumb is missing but preview exists, use the preview URL for both.
- If original files are missing, keep the existing `404` behavior.

The existing `/api/tasks/<task_id>/images/<item_index>/<kind>` route remains for fallback and original image access.

## Nginx Design

Deploy Nginx with a static asset location pointing to the preview cache root:

```nginx
location /annotation-assets/ {
    alias /srv/annotations_v2/preview-cache/;
    access_log off;
    expires 1y;
    add_header Cache-Control "public, max-age=31536000, immutable";
    try_files $uri =404;
}
```

If the deployment needs Flask-side authorization before serving files, use an internal location with `X-Accel-Redirect` instead of public static URLs. The first implementation can use public static preview URLs because the current app already exposes image content through API routes.

## Frontend Design

Update image rendering to load in two phases:

1. Set the image to the thumb URL immediately.
2. Start loading the preview URL in a detached `Image`.
3. Replace the visible image after `decode()` resolves.

Current item images should keep high priority. Neighbor preloads should be low priority and concurrency-limited. The preload queue should cancel or ignore stale work when users jump quickly between items.

Recommended behavior:

- Current source and destination previews load first.
- Preload the next 2 or 3 item pairs.
- Limit preload concurrency to 4 image requests.
- Do not assign `fetchPriority = "high"` to preloaded neighbor images.
- For visualization pages, keep the current metadata requests in the first implementation; a later optimization can add a batch endpoint for nearby result rows.

## Cache Status

Expose cache status in task payloads and task cards:

- `total_images`
- `generated_images`
- `failed_images`
- `missing_images`
- `cache_ready`
- `updated_at`

Task cards should keep the existing manual "cache previews" action and show whether the static preview cache is ready. The first implementation should not automatically start cache warming on task creation; automatic warming can be added later after cache generation time and disk usage are measured.

## Rollout Plan

1. Add manifest and variant generation while keeping all existing routes working.
2. Configure `ANNOTATIONS_V2_PREVIEW_CACHE_DIR` on the Ubuntu server to a local disk path.
3. Add Nginx `/annotation-assets/` alias.
4. Update API payloads to prefer manifest static URLs.
5. Update frontend thumb-to-preview loading and preload limits.
6. Verify fallback behavior by clearing a manifest entry and confirming Flask preview still works.

This can be rolled out task by task. Existing tasks need a preview warm run before they get static URLs.

## Testing

Backend tests in `tests/test_annotations_v2_app.py`:

- Preview warming creates thumb and preview files for both `src` and `dst`.
- Manifest entries include static URLs and image dimensions.
- Item payloads use static manifest URLs when available.
- Item payloads fall back to existing Flask preview URLs when manifest entries are missing.
- Original image URLs continue to point to `?original=1`.

Frontend static tests:

- `preparePreviewImage` or its replacement accepts thumb and preview URLs.
- Current images load thumbs first and decode previews before replacement.
- Neighbor preloading uses preview URLs and does not mark them high priority.
- Preload queue limits concurrency.

Manual deployment verification:

- Warm one task on Ubuntu.
- Confirm Nginx serves `/annotation-assets/<task_id>/...jpg` with `Cache-Control: public, max-age=31536000, immutable`.
- Confirm rating page network requests for normal images hit `/annotation-assets/`, not `/api/tasks/.../images/...`.
- Confirm Flask worker logs no longer show normal preview image traffic during annotation.

## Success Criteria

- After cache warming, normal annotation image traffic bypasses Flask.
- Browser sees a visible thumb quickly, then a full preview without layout shift.
- Remote annotators can flip through nearby items without repeated blank waits.
- Flask workers remain available for JSON APIs and saves.
- Existing original-image access and fallback routes remain compatible.
