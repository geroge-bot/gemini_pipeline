from __future__ import annotations

import hashlib
import mimetypes
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Response, send_file
from PIL import Image

from web.annotations_v3 import datasets, storage


SPECS = {"thumb": 384, "preview": 1024, "full": 1600}


def manifest_path(dataset_id: str) -> Path:
    return storage.dataset_dir(dataset_id) / "preview_cache" / "manifest.json"


def cache_dir(dataset_id: str) -> Path:
    return storage.dataset_dir(dataset_id) / "preview_cache"


def load_manifest(dataset_id: str) -> dict[str, Any]:
    return storage.read_json(manifest_path(dataset_id), {"version": 1, "dataset_id": dataset_id, "assets": {}})


def save_manifest(dataset_id: str, manifest: dict[str, Any]) -> None:
    storage.write_json_atomic(manifest_path(dataset_id), manifest)


def missing_side() -> dict[str, Any]:
    return {"status": "missing"}


def asset_entry_for_item(dataset_id: str, item_id: str) -> dict[str, Any]:
    assets = load_manifest(dataset_id).get("assets", {})
    return assets.get(item_id, {"src": missing_side(), "dst": missing_side()})


def manifest_for_items(dataset_id: str, item_ids: list[str]) -> dict[str, Any]:
    manifest = load_manifest(dataset_id)
    assets = manifest.get("assets", {})
    return {
        "version": manifest.get("version", 1),
        "dataset_id": dataset_id,
        "assets": {
            item_id: assets.get(item_id, {"src": missing_side(), "dst": missing_side()})
            for item_id in item_ids
        },
    }


def _source_path(dataset_id: str, item: dict[str, Any], side: str) -> Path:
    dataset_doc = datasets.get_dataset(dataset_id)
    raw_path = Path(str(item[f"{side}_image"]))
    return raw_path if raw_path.is_absolute() else Path(dataset_doc["root_dir"]) / raw_path


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_id(item_id: str, side: str, spec: str, content_hash: str) -> str:
    safe_item = item_id.replace(":", "_")
    return f"{safe_item}_{side}_{spec}_{content_hash[:16]}.webp"


def _resize_to_spec(image: Image.Image, max_edge: int) -> Image.Image:
    image = image.convert("RGB")
    width, height = image.size
    scale = min(1.0, max_edge / max(width, height))
    if scale < 1.0:
        image = image.resize((int(width * scale), int(height * scale)))
    return image


def _write_derivative_tmp(source_path: Path, tmp_path: Path, max_edge: int) -> dict[str, Any]:
    with Image.open(source_path) as image:
        resized = _resize_to_spec(image, max_edge)
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        resized.save(tmp_path, format="WEBP", quality=86)
        stat = tmp_path.stat()
        return {"width": resized.width, "height": resized.height, "bytes": stat.st_size}


def _find_item(dataset_id: str, item_id: str) -> dict[str, Any]:
    for item in datasets.load_items(dataset_id):
        if item["item_id"] == item_id:
            return item
    raise FileNotFoundError(item_id)


def generate_item_assets(dataset_id: str, item_id: str) -> dict[str, Any]:
    item = _find_item(dataset_id, item_id)
    manifest = load_manifest(dataset_id)
    entry: dict[str, Any] = {}
    for side in ("src", "dst"):
        source_path = _source_path(dataset_id, item, side)
        if not source_path.exists():
            entry[side] = {"status": "error", "error": f"image not found: {source_path}"}
            continue
        source_hash = _file_hash(source_path)
        side_entry: dict[str, Any] = {"status": "ready", "source_hash": source_hash}
        previous_side = manifest.get("assets", {}).get(item_id, {}).get(side, {})
        for spec, max_edge in SPECS.items():
            existing = previous_side.get(spec)
            if existing and previous_side.get("source_hash") == source_hash:
                side_entry[spec] = existing
                continue
            tmp_path = cache_dir(dataset_id) / f".{uuid.uuid4().hex}.webp"
            meta = _write_derivative_tmp(source_path, tmp_path, max_edge)
            derivative_hash = _file_hash(tmp_path)
            asset_id = _asset_id(item_id, side, spec, derivative_hash)
            output_path = cache_dir(dataset_id) / asset_id
            if not output_path.exists():
                os.replace(tmp_path, output_path)
            elif tmp_path.exists():
                tmp_path.unlink()
            etag = f"sha256:{derivative_hash}"
            side_entry[spec] = {
                "url": f"/api/datasets/{dataset_id}/assets/{asset_id}",
                "width": meta["width"],
                "height": meta["height"],
                "bytes": meta["bytes"],
                "etag": etag,
            }
        entry[side] = side_entry
    manifest.setdefault("assets", {})[item_id] = entry
    save_manifest(dataset_id, manifest)
    return entry


def jobs_dir(dataset_id: str) -> Path:
    path = cache_dir(dataset_id) / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_asset_job(dataset_id: str, job: dict[str, Any]) -> None:
    storage.write_json_atomic(jobs_dir(dataset_id) / f"{job['job_id']}.json", job)


def get_asset_job(dataset_id: str, job_id: str) -> dict[str, Any]:
    job = storage.read_json(jobs_dir(dataset_id) / f"{job_id}.json", None)
    if job is None:
        raise FileNotFoundError(job_id)
    return job


def create_asset_job(dataset_id: str, item_ids: list[str] | None = None) -> dict[str, Any]:
    if item_ids is None:
        ranks = datasets.item_rank_map(dataset_id)
        item_ids = [
            item["item_id"]
            for item in sorted(datasets.load_items(dataset_id), key=lambda item: ranks[item["item_id"]])
        ]
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "dataset_id": dataset_id,
        "status": "running",
        "total": len(item_ids),
        "completed": 0,
        "failed": 0,
        "errors": [],
        "item_ids": item_ids,
    }
    save_asset_job(dataset_id, job)
    thread = threading.Thread(target=run_asset_job, args=(dataset_id, job_id, item_ids), daemon=True)
    thread.start()
    return job


def run_asset_job(dataset_id: str, job_id: str, item_ids: list[str]) -> None:
    job = get_asset_job(dataset_id, job_id)
    for item_id in item_ids:
        result = generate_item_assets(dataset_id, item_id)
        if any(side.get("status") == "error" for side in result.values()):
            job["failed"] += 1
            job["errors"].append({"item_id": item_id, "result": result})
        else:
            job["completed"] += 1
        save_asset_job(dataset_id, job)
    job["status"] = "completed"
    save_asset_job(dataset_id, job)


def serve_asset(
    dataset_id: str,
    asset_id: str,
    range_header: str | None = None,
    if_none_match: str | None = None,
):
    path = cache_dir(dataset_id) / asset_id
    if not path.exists():
        raise FileNotFoundError(asset_id)
    etag = f"sha256:{_file_hash(path)}"
    if if_none_match == etag:
        return Response(status=304, headers={"ETag": etag})
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if range_header and range_header.startswith("bytes="):
        start_raw, end_raw = range_header.replace("bytes=", "", 1).split("-", 1)
        start = int(start_raw or 0)
        end = int(end_raw or path.stat().st_size - 1)
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(end - start + 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{path.stat().st_size}"
        return Response(data, 206, headers=headers, mimetype=mime)
    response = send_file(path, mimetype=mime)
    response.headers.update(headers)
    return response
