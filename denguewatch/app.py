"""Application factory: one Flask server hosting the REST API and the Dash dashboard.

    /api/v1/...   REST API (denguewatch/api/routes.py)
    /dashboard/   Dash dashboard (Step 9)
"""

from flask import Flask, jsonify, redirect, request

from denguewatch.api.routes import api
from denguewatch.dashboard.app import create_dashboard


def create_app() -> Flask:
    server = Flask(__name__)
    server.json.sort_keys = False  # keep fields in the order the API defines them
    server.register_blueprint(api)

    @server.errorhandler(404)
    def not_found(err):
        if request.path.startswith("/api/"):
            return jsonify({"error": {"status": 404, "message": f"No endpoint {request.path} - see /api/v1/"}}), 404
        return err

    @server.get("/")
    def index():
        return redirect("/dashboard/")

    create_dashboard(server)
    return server
