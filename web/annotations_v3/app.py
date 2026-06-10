from __future__ import annotations

from flask import Flask, jsonify, request

from web.annotations_v3 import assignments
from web.annotations_v3 import datasets


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/api/datasets")
    def api_list_datasets():
        return jsonify({"datasets": datasets.list_datasets()})

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

    return app


app = create_app()
