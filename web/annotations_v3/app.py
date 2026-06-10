from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from web.annotations_v3 import assets
from web.annotations_v3 import assignments
from web.annotations_v3 import datasets
from web.annotations_v3 import records
from web.annotations_v3 import sampling
from web.annotations_v3 import schema
from web.annotations_v3 import visualization


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/api/datasets")
    def api_list_datasets():
        return jsonify({"datasets": datasets.list_datasets()})

    @app.get("/")
    def page_index():
        return render_template("index.html", datasets=datasets.list_datasets())

    @app.get("/datasets/<dataset_id>")
    def page_dataset(dataset_id: str):
        try:
            return render_template("dataset.html", dataset=datasets.get_dataset(dataset_id))
        except FileNotFoundError:
            return "dataset not found", 404

    @app.get("/datasets/<dataset_id>/rate")
    def page_rate(dataset_id: str):
        stage = request.args.get("stage", "rough")
        try:
            dataset_doc = datasets.get_dataset(dataset_id)
        except FileNotFoundError:
            return "dataset not found", 404
        return render_template("rate.html", dataset=dataset_doc, stage=stage)

    @app.get("/datasets/<dataset_id>/sample")
    def page_sample(dataset_id: str):
        try:
            return render_template("sample.html", dataset=datasets.get_dataset(dataset_id))
        except FileNotFoundError:
            return "dataset not found", 404

    @app.get("/datasets/<dataset_id>/visualize")
    def page_visualize(dataset_id: str):
        try:
            return render_template(
                "visualize.html",
                dataset=datasets.get_dataset(dataset_id),
                stage=request.args.get("stage", "rough"),
            )
        except FileNotFoundError:
            return "dataset not found", 404

    @app.post("/api/datasets")
    def api_create_dataset():
        try:
            dataset_doc = datasets.create_dataset(request.get_json(silent=True) or {})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(dataset_doc), 201

    @app.get("/api/datasets/<dataset_id>")
    def api_get_dataset(dataset_id: str):
        try:
            return jsonify(datasets.get_dataset(dataset_id))
        except FileNotFoundError:
            return jsonify({"error": "dataset not found"}), 404

    @app.get("/api/datasets/<dataset_id>/order-manifest")
    def api_get_order_manifest(dataset_id: str):
        try:
            return jsonify(datasets.get_order_manifest(dataset_id))
        except FileNotFoundError:
            return jsonify({"error": "dataset not found"}), 404

    @app.post("/api/datasets/<dataset_id>/assignments/claim")
    def api_claim_assignment(dataset_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(
                assignments.claim_assignment(
                    dataset_id,
                    str(payload.get("stage") or ""),
                    str(payload.get("username") or ""),
                )
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError:
            return jsonify({"error": "dataset not found"}), 404

    @app.get("/api/datasets/<dataset_id>/assignments/<assignment_id>/items")
    def api_assignment_items(dataset_id: str, assignment_id: str):
        try:
            return jsonify(assignments.get_assignment_items(dataset_id, assignment_id))
        except FileNotFoundError:
            return jsonify({"error": "assignment not found"}), 404

    @app.post("/api/datasets/<dataset_id>/assignments/<assignment_id>/release")
    def api_release_assignment(dataset_id: str, assignment_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(assignments.release_assignment(dataset_id, assignment_id, str(payload.get("username") or "")))
        except FileNotFoundError:
            return jsonify({"error": "assignment not found"}), 404

    @app.post("/api/datasets/<dataset_id>/items/<item_id>/annotation-patch")
    def api_annotation_patch(dataset_id: str, item_id: str):
        try:
            return jsonify(records.save_annotation_patch(dataset_id, item_id, request.get_json(silent=True) or {}))
        except records.RecordServiceError as exc:
            body = {"error": str(exc), "code": exc.code}
            if hasattr(exc, "latest"):
                body["latest"] = exc.latest
            return jsonify(body), exc.status_code
        except schema.ValidationError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 400
        except FileNotFoundError:
            return jsonify({"error": "dataset or item not found"}), 404

    @app.get("/api/datasets/<dataset_id>/sample-buckets")
    def api_sample_buckets(dataset_id: str):
        paths = request.args.get("paths", "")
        selected_paths = [path.split("/") for path in paths.split(",") if path]
        try:
            return jsonify(sampling.sample_buckets(dataset_id, selected_paths))
        except FileNotFoundError:
            return jsonify({"error": "dataset not found"}), 404

    @app.post("/api/datasets/<dataset_id>/sample")
    def api_run_sample(dataset_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(
                sampling.run_sample(
                    dataset_id,
                    str(payload.get("username") or ""),
                    payload.get("selected_label_paths") or [],
                    int(payload.get("per_bucket") or 1),
                    payload.get("seed"),
                )
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except FileNotFoundError:
            return jsonify({"error": "dataset not found"}), 404

    @app.get("/api/datasets/<dataset_id>/visualization-results")
    def api_visualization_results(dataset_id: str):
        try:
            return jsonify(
                visualization.visualization_results(
                    dataset_id,
                    request.args.get("stage", "rough"),
                    int(request.args.get("page", 1)),
                    int(request.args.get("page_size", 50)),
                )
            )
        except FileNotFoundError:
            return jsonify({"error": "dataset not found"}), 404

    @app.post("/api/datasets/<dataset_id>/assets/jobs")
    def api_create_asset_job(dataset_id: str):
        payload = request.get_json(silent=True) or {}
        return jsonify(assets.create_asset_job(dataset_id, payload.get("item_ids")))

    @app.get("/api/datasets/<dataset_id>/assets/jobs/<job_id>")
    def api_get_asset_job(dataset_id: str, job_id: str):
        try:
            return jsonify(assets.get_asset_job(dataset_id, job_id))
        except FileNotFoundError:
            return jsonify({"error": "job not found"}), 404

    @app.get("/api/datasets/<dataset_id>/assets/manifest")
    def api_asset_manifest(dataset_id: str):
        raw_item_ids = request.args.get("item_ids")
        if raw_item_ids:
            return jsonify(assets.manifest_for_items(dataset_id, [value for value in raw_item_ids.split(",") if value]))
        return jsonify(assets.load_manifest(dataset_id))

    @app.get("/api/datasets/<dataset_id>/assets/<asset_id>")
    def api_get_asset(dataset_id: str, asset_id: str):
        try:
            return assets.serve_asset(
                dataset_id,
                asset_id,
                request.headers.get("Range"),
                request.headers.get("If-None-Match"),
            )
        except FileNotFoundError:
            return jsonify({"error": "asset not found"}), 404

    return app


app = create_app()
