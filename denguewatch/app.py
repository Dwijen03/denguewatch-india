"""Application factory: one Flask server hosting the REST API and the Dash dashboard.

    /api/v1/...   REST API (denguewatch/api/routes.py)
    /dashboard/   Dash dashboard (Step 9)
"""

from dash import Dash, html
from flask import Flask, jsonify, redirect, request

from denguewatch.api.routes import api


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

    dash_app = Dash(__name__, server=server, url_base_pathname="/dashboard/", title="DengueWatch India")
    dash_app.layout = html.Div(
        style={"fontFamily": "Arial, sans-serif", "maxWidth": "760px", "margin": "60px auto"},
        children=[
            html.H1("DengueWatch India"),
            html.P("A weather-driven dengue early warning system for Indian districts."),
            html.P("The dashboard is added in Step 9. The REST API is live:"),
            html.Ul([html.Li(html.A(p, href=p)) for p in
                     ["/api/v1/", "/api/v1/health", "/api/v1/risk/current?level=high", "/api/v1/model"]]),
        ],
    )
    return server
