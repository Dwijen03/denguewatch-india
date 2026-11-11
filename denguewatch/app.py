"""Application factory: one Flask server hosting the REST API and the Dash dashboard.

This is the Step 3 skeleton. The real endpoints arrive in Step 8 and the
dashboard pages in Step 9.
"""

from dash import Dash, html
from flask import Flask, jsonify, redirect

from denguewatch import __version__
from denguewatch.db.connection import check_connection


def create_app() -> Flask:
    server = Flask(__name__)

    @server.get("/")
    def index():
        return redirect("/dashboard/")

    @server.get("/api/v1/health")
    def health():
        db_ok, db_message = check_connection()
        return jsonify(
            {
                "service": "DengueWatch India API",
                "version": __version__,
                "database": {"connected": db_ok, "detail": db_message},
            }
        ), (200 if db_ok else 503)

    dash_app = Dash(__name__, server=server, url_base_pathname="/dashboard/", title="DengueWatch India")
    dash_app.layout = html.Div(
        style={"fontFamily": "Arial, sans-serif", "maxWidth": "760px", "margin": "60px auto"},
        children=[
            html.H1("DengueWatch India"),
            html.P("A weather-driven dengue early warning system for Indian districts."),
            html.P("Setup complete. Dashboard pages will be added in Step 9."),
            html.A("Check API health", href="/api/v1/health"),
        ],
    )
    return server
