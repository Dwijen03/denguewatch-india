"""DengueWatch India dashboard (Dash, served at /dashboard/ on the same Flask server as the API).

Pages
  1. Risk this week   - district risk map, counts and the list of districts to watch
  2. District explorer - one district: risk, reasons, weekly outbreaks vs rain and temperature
  3. Compare           - districts x years, seasonality and yearly totals for a state
  4. Model & data      - how well the model works, data freshness and pipeline runs

All data comes from the REST API (/api/v1), called in-process, so the dashboard and
external users see exactly the same numbers.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update
from flask import Flask
from plotly.subplots import make_subplots

from denguewatch import config
from denguewatch.api.routes import api as api_blueprint

# ---------------------------------------------------------------- design tokens
TEXT, TEXT2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"
BLUE, ORANGE = "#2a78d6", "#eb6834"
LEVEL_COLOR = {"high": "#d03b3b", "medium": "#fab219", "low": "#d9d8d3"}
LEVEL_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}
SEQ_BLUE = [[0, "#f3f7fd"], [0.15, "#cde2fb"], [0.35, "#86b6ef"], [0.6, "#3987e5"], [0.8, "#1c5cab"], [1, "#0d366b"]]
FONT = dict(family="Segoe UI, Inter, Arial, sans-serif", size=12, color=TEXT2)
GEOJSON_PATH = config.DATA_DIR / "reference" / "districts_2011.geojson"

# Census 2011 spellings that differ from the names people use today (shown as "Raigad (Raigarh)")
COMMON_NAMES = {
    ("Maharashtra", "Raigarh"): "Raigad", ("Maharashtra", "Bid"): "Beed", ("Maharashtra", "Garhchiroli"): "Gadchiroli",
    ("Maharashtra", "Ahmadnagar"): "Ahmednagar", ("Maharashtra", "Buldana"): "Buldhana", ("Maharashtra", "Gondiya"): "Gondia",
    ("West Bengal", "Hugli"): "Hooghly", ("West Bengal", "Haora"): "Howrah", ("West Bengal", "Barddhaman"): "Bardhaman",
    ("West Bengal", "Koch Bihar"): "Cooch Behar", ("West Bengal", "Darjiling"): "Darjeeling", ("West Bengal", "Maldah"): "Malda",
    ("Karnataka", "Belgaum"): "Belagavi", ("Karnataka", "Bellary"): "Ballari", ("Karnataka", "Gulbarga"): "Kalaburagi",
    ("Karnataka", "Bangalore"): "Bengaluru Urban", ("Karnataka", "Mysore"): "Mysuru", ("Karnataka", "Shimoga"): "Shivamogga",
    ("Karnataka", "Tumkur"): "Tumakuru", ("Karnataka", "Bijapur"): "Vijayapura", ("Tamil Nadu", "Virudunagar"): "Virudhunagar",
    ("Tamil Nadu", "Thoothukkudi"): "Thoothukudi", ("Andhra Pradesh", "Y.s.r."): "YSR Kadapa",
    ("Andhra Pradesh", "Sri Potti Sriramulu Nellore"): "Nellore", ("Haryana", "Gurgaon"): "Gurugram", ("Haryana", "Mewat"): "Nuh",
    ("Uttar Pradesh", "Allahabad"): "Prayagraj", ("Uttar Pradesh", "Faizabad"): "Ayodhya", ("Odisha", "Baleshwar"): "Balasore",
    ("Gujarat", "Ahmadabad"): "Ahmedabad", ("Jharkhand", "Purbi Singhbhum"): "East Singhbhum",
}


def nice(district: str, state: str) -> str:
    common = COMMON_NAMES.get((state, district))
    return f"{common} ({district})" if common else district


def base_layout(fig: go.Figure, height: int = 320, **kw) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor=SURF, plot_bgcolor=SURF,
                      font=FONT, hoverlabel=dict(bgcolor="white", font_size=12), **kw)
    fig.update_xaxes(gridcolor=GRID, linecolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, zeroline=False)
    return fig


def empty_figure(message: str, height: int = 300) -> go.Figure:
    fig = base_layout(go.Figure(), height)
    fig.add_annotation(text=message, showarrow=False, font=dict(color=TEXT2, size=13))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


@lru_cache(maxsize=1)
def geojson() -> dict:
    return json.loads(Path(GEOJSON_PATH).read_text())


def tile(label: str, value, hint: str = "", dot: str | None = None) -> html.Div:
    return html.Div(className="card tile", children=[
        html.Div(className="label", children=[html.Span(className=f"dot {dot}") if dot else None, label]),
        html.Div(className="value", children=value),
        html.Div(className="hint", children=hint),
    ])


def reasons_list(factors) -> html.Ul:
    if not factors:
        return html.Ul(className="reasons", children=[html.Li("No strong factors")])
    return html.Ul(className="reasons", children=[html.Li(f"{f['label']}: {f['value']:g}") for f in factors])


def create_dashboard(server: Flask) -> Dash:
    app = Dash(__name__, server=server, url_base_pathname="/dashboard/", title="DengueWatch India",
               assets_folder=str(Path(__file__).parent / "assets"), suppress_callback_exceptions=True)

    # The dashboard reads everything through the REST API. It uses an internal Flask app that holds
    # only the API blueprint, so calls stay in-process (no network) and avoid Dash's request hooks.
    api_app = Flask("denguewatch-api-internal")
    api_app.json.sort_keys = False
    api_app.register_blueprint(api_blueprint)

    def api(path: str, **params):
        """Call the REST API in-process; returns parsed JSON or None on error."""
        with api_app.test_client() as client:
            resp = client.get(f"/api/v1{path}", query_string={k: v for k, v in params.items() if v not in (None, "")})
        return resp.get_json() if resp.status_code == 200 else None

    # ------------------------------------------------------------ layout
    def header():
        h = api("/health") or {}
        d = h.get("data", {}) if isinstance(h.get("data"), dict) else {}
        yw = d.get("latest_report_yearweek")
        report = f"week {yw % 100} of {yw // 100}" if yw else "–"
        return html.Div(className="header", children=[
            html.Div(className="brand", children=[
                html.H1("DengueWatch India"),
                html.P("Weather-driven early warning of dengue outbreaks for Indian districts"),
            ]),
            html.Div(className="freshness", children=[
                html.Div(f"Latest IDSP outbreak report: {report} (ending {d.get('latest_report_week_end', '–')})"),
                html.Div(f"Weather observed up to {d.get('latest_weather_date', '–')} · forecast 16 days ahead"),
                html.Div("Decision support only - not an official alert"),
            ]),
        ])

    def district_options():
        res = (api("/districts") or {}).get("results", [])
        return [{"label": f"{nice(r['district_name'], r['state_name'])}, {r['state_name']}", "value": r["census_code"]}
                for r in res]

    def state_options(all_label="All India"):
        res = (api("/states") or {}).get("results", [])
        return [{"label": all_label, "value": ""}] + [{"label": r["state_name"], "value": r["state_name"]}
                                                       for r in res if r["outbreaks"] > 0 or r["districts"] > 0]

    def serve_layout():
        states = state_options()
        districts = district_options()
        risk = api("/risk/current") or {}
        top = (risk.get("results") or [{}])[0].get("census_code")
        return html.Div(className="page", children=[
            header(),
            dcc.Store(id="selected-district", data=top),
            dcc.Tabs(id="tabs", value="risk", className="tabs-container", children=[
                dcc.Tab(label="Risk this week", value="risk", className="tab", selected_className="tab--selected",
                        children=risk_page(states)),
                dcc.Tab(label="District explorer", value="district", className="tab",
                        selected_className="tab--selected", children=district_page(districts)),
                dcc.Tab(label="Compare districts & years", value="compare", className="tab",
                        selected_className="tab--selected", children=compare_page(states)),
                dcc.Tab(label="Model & data", value="model", className="tab",
                        selected_className="tab--selected", children=model_page()),
            ]),
        ])

    def risk_page(states):
        return html.Div([
            html.Div(className="filters", children=[
                html.Div(className="filter", children=[html.Label("State"), dcc.Dropdown(
                    id="risk-state", options=states, value="", clearable=False)]),
                html.Div(className="filter", children=[html.Label("Show"), dcc.Checklist(
                    id="risk-levels", options=[{"label": f" {LEVEL_LABEL[k]}", "value": k} for k in LEVEL_COLOR],
                    value=["high", "medium", "low"], inline=True, inputStyle={"marginLeft": "10px"})]),
            ]),
            html.Div(id="risk-tiles", className="grid grid-4"),
            html.Div(className="grid grid-2", style={"marginTop": "14px"}, children=[
                html.Div(className="card", children=[
                    html.H3("Outbreak risk by district"),
                    html.P(id="risk-window", className="sub"),
                    dcc.Graph(id="risk-map", config={"displayModeBar": False}),
                    html.Div(className="note", children=[
                        html.Span(className="dot high"), "High  ", html.Span(className="dot medium"), "Medium  ",
                        html.Span(className="dot low"), "Low  ·  Click a district to open it in the explorer. "
                        "Boundaries: Census 2011 districts (newer districts are part of their 2011 parent)."]),
                ]),
                html.Div(className="card", children=[
                    html.H3("Districts to watch"),
                    html.P("Highest risk first. Reasons are the factors that raised each district's score.",
                           className="sub"),
                    dash_table.DataTable(
                        id="risk-table", page_size=14, sort_action="native", style_as_list_view=True,
                        columns=[{"name": "District", "id": "district"}, {"name": "State", "id": "state"},
                                 {"name": "Risk", "id": "level"}, {"name": "Score", "id": "score"},
                                 {"name": "Main reasons", "id": "reasons"}],
                        style_header={"fontWeight": "600", "color": TEXT2, "backgroundColor": "white",
                                      "borderBottom": f"1px solid {GRID}"},
                        style_cell={"fontFamily": FONT["family"], "fontSize": 12.5, "padding": "6px",
                                    "textAlign": "left", "whiteSpace": "normal", "height": "auto",
                                    "border": "none", "borderBottom": f"1px solid {GRID}"},
                        style_cell_conditional=[{"if": {"column_id": "reasons"}, "width": "42%", "color": TEXT2}],
                        style_data_conditional=[
                            {"if": {"filter_query": '{level} = "High"', "column_id": "level"},
                             "color": "#8f2020", "fontWeight": "600"},
                            {"if": {"filter_query": '{level} = "Medium"', "column_id": "level"},
                             "color": "#7a5200", "fontWeight": "600"},
                            {"if": {"state": "active"}, "backgroundColor": "#f3f7fd", "border": "none"}],
                    ),
                ]),
            ]),
        ])

    def district_page(districts):
        return html.Div([
            html.Div(className="filters", children=[
                html.Div(className="filter", style={"minWidth": "320px"}, children=[
                    html.Label("District"), dcc.Dropdown(id="district-select", options=districts,
                                                         placeholder="Type a district name", clearable=False)]),
                html.Div(className="filter", children=[html.Label("Period"), dcc.RadioItems(
                    id="district-period", value=2, inline=True, inputStyle={"marginLeft": "10px"},
                    options=[{"label": " 1 year", "value": 1}, {"label": " 2 years", "value": 2},
                             {"label": " 5 years", "value": 5}, {"label": " Since 2010", "value": 17}])]),
                html.A("Download this district's outbreaks (CSV)", id="district-csv", href="#",
                       style={"marginLeft": "auto", "fontSize": "13px"}),
            ]),
            html.Div(id="district-tiles", className="grid grid-4"),
            html.Div(className="grid grid-2", style={"marginTop": "14px"}, children=[
                html.Div(className="card", children=[
                    html.H3("Outbreaks, rain and temperature by week"),
                    html.P("Dengue usually follows rain and warm nights by several weeks. "
                           "Shaded weeks use the weather forecast.", className="sub"),
                    dcc.Graph(id="district-timeline", config={"displayModeBar": False}),
                ]),
                html.Div(className="card", children=[
                    html.H3("Risk score over recent weeks"),
                    html.P("Each point is the risk for the 4 weeks starting that week.", className="sub"),
                    dcc.Graph(id="district-risk", config={"displayModeBar": False}),
                    html.H3("Why this risk level?", style={"marginTop": "10px"}),
                    html.Div(id="district-reasons"),
                ]),
            ]),
        ])

    def compare_page(states):
        return html.Div([
            html.Div(className="filters", children=[
                html.Div(className="filter", children=[html.Label("State"), dcc.Dropdown(
                    id="cmp-state", options=states, value="Maharashtra", clearable=False)]),
                html.Div(className="filter", style={"minWidth": "360px"}, children=[
                    html.Label("Years"), dcc.RangeSlider(id="cmp-years", min=2010, max=2026, step=1, value=[2015, 2026],
                                                         marks={y: str(y) for y in range(2010, 2027, 4)},
                                                         tooltip={"placement": "bottom"})]),
            ]),
            html.Div(className="card", children=[
                html.H3("Outbreaks per district and year"),
                html.P("Darker = more outbreaks. The 25 districts with most outbreaks in the selected years.",
                       className="sub"),
                dcc.Graph(id="cmp-heatmap", config={"displayModeBar": False}),
            ]),
            html.Div(className="grid grid-2-even", style={"marginTop": "14px"}, children=[
                html.Div(className="card", children=[html.H3("When do outbreaks start?"),
                                                     html.P("Outbreaks by month of onset", className="sub"),
                                                     dcc.Graph(id="cmp-season", config={"displayModeBar": False})]),
                html.Div(className="card", children=[html.H3("Outbreaks per year"),
                                                     html.P("As listed in the IDSP weekly reports", className="sub"),
                                                     dcc.Graph(id="cmp-years-chart", config={"displayModeBar": False})]),
            ]),
        ])

    def model_page():
        m = api("/model")
        if not m:
            return html.Div(className="card", children="No model has been trained yet - run train_model.bat.")
        ev = m["evaluation"]
        base = ev["test_base_rate"]
        w = ev["warned"]
        comp = sorted(ev["comparison"], key=lambda r: r["pr_auc"])
        comp_fig = go.Figure(go.Bar(
            x=[r["pr_auc"] for r in comp], y=[r["model"] for r in comp], orientation="h",
            marker_color=[BLUE if r["model"].startswith("DengueWatch") else "#9cc3ef" for r in comp],
            text=[f"{r['pr_auc']:.3f} ({r['pr_auc'] / base:.1f}×)" for r in comp], textposition="outside",
            hovertemplate="%{y}<br>PR AUC %{x:.3f}<extra></extra>"))
        comp_fig.add_vline(x=base, line_dash="dash", line_color=TEXT2)
        base_layout(comp_fig, 260, xaxis_title="Precision–recall AUC on unseen years")
        comp_fig.update_xaxes(range=[0, max(r["pr_auc"] for r in comp) * 1.45])
        ks = [1, 2, 3, 4]
        lead = go.Figure([
            go.Bar(name="Medium or high alert", x=[f"≥{k} wk" for k in ks], marker_color=BLUE,
                   y=[100 * w[f"medium_or_high_{k}plus_weeks"] for k in ks], texttemplate="%{y:.0f}%",
                   textposition="outside", hovertemplate="%{y:.0f}% warned %{x} ahead<extra></extra>"),
            go.Bar(name="High alert only", x=[f"≥{k} wk" for k in ks], marker_color=ORANGE,
                   y=[100 * w[f"high_{k}plus_weeks"] for k in ks], texttemplate="%{y:.0f}%",
                   textposition="outside", hovertemplate="%{y:.0f}% warned %{x} ahead<extra></extra>")])
        base_layout(lead, 260, barmode="group", bargap=0.3, yaxis_title="% of outbreaks warned",
                    legend=dict(orientation="h", y=1.12, x=0))
        lead.update_yaxes(range=[0, 100])
        h, mh = ev["high_alert"], ev["medium_or_high_alert"]
        runs = (api("/pipeline-runs", limit=8) or {}).get("results", [])
        return html.Div([
            html.Div(className="grid grid-4", children=[
                tile("Accuracy on unseen years", f"{ev['test_pr_auc'] / base:.1f}× chance",
                     f"PR AUC {ev['test_pr_auc']:.3f} · ROC AUC {ev['test_roc_auc']:.2f} · {ev['test_period']}"),
                tile("High alerts catch", f"{100 * h['recall']:.0f}%",
                     f"of outbreaks, flagging {100 * h['alert_rate']:.0f}% of district-weeks"),
                tile("Medium + high catch", f"{100 * mh['recall']:.0f}%",
                     f"of outbreaks, flagging {100 * mh['alert_rate']:.0f}% of district-weeks"),
                tile("Warned ≥ 2 weeks ahead", f"{100 * w['medium_or_high_2plus_weeks']:.0f}%",
                     "of outbreaks (medium or high alert)"),
            ]),
            html.Div(className="grid grid-2-even", style={"marginTop": "14px"}, children=[
                html.Div(className="card", children=[html.H3("Model comparison"), html.P(
                    f"All trained on 2010–2022, tested on later years they never saw. Dashed line = chance ({base:.3f}).",
                    className="sub"),
                    dcc.Graph(figure=comp_fig, config={"displayModeBar": False})]),
                html.Div(className="card", children=[html.H3("How early were outbreaks flagged?"), html.P(
                    "Share of outbreaks already flagged 1–4 weeks before they started.", className="sub"),
                    dcc.Graph(figure=lead, config={"displayModeBar": False})]),
            ]),
            html.Div(className="grid grid-2-even", style={"marginTop": "14px"}, children=[
                html.Div(className="card", children=[
                    html.H3("About the model"),
                    html.Ul(className="reasons", children=[
                        html.Li(f"{m['algorithm']}, trained on {m['train_period']}."),
                        html.Li("Predicts whether a district will report a dengue outbreak in the next 4 weeks."),
                        html.Li("Uses weather of the last 16 weeks, season, location and outbreak history. "
                                "Only outbreaks that would already be published are used (reports are ~8 weeks late)."),
                        html.Li(f"High = score ≥ {ev['thresholds']['high']:.2f}, medium ≥ {ev['thresholds']['medium']:.2f} "
                                "(top 5% / 15% of a validation year). Scores rank risk; they are not probabilities."),
                        html.Li("Limitations: learns from reported outbreaks, so under-reporting lowers apparent risk; "
                                "alerts mean 'check here first', not a certain outbreak."),
                    ]),
                ]),
                html.Div(className="card", children=[
                    html.H3("Recent data updates"),
                    dash_table.DataTable(
                        data=[{"job": r["job_name"], "finished": (r["finished_at"] or "")[:16].replace("T", " "),
                               "status": r["status"], "rows": r["rows_added"]} for r in runs],
                        columns=[{"name": n, "id": n} for n in ("job", "finished", "status", "rows")],
                        style_as_list_view=True, style_header={"fontWeight": "600", "backgroundColor": "white"},
                        style_cell={"fontFamily": FONT["family"], "fontSize": 12.5, "textAlign": "left",
                                    "border": "none", "borderBottom": f"1px solid {GRID}"}),
                    html.Div(className="note", children=[
                        "Data: IDSP Weekly Outbreak Reports (NCDC), NASA POWER, Open-Meteo (CC BY 4.0), "
                        "DataMeet district boundaries (CC BY 2.5 IN). API: ", html.A("/api/v1/", href="/api/v1/")]),
                ]),
            ]),
        ])

    app.layout = serve_layout

    # ------------------------------------------------------------ callbacks: risk page
    @app.callback(Output("risk-tiles", "children"), Output("risk-map", "figure"), Output("risk-table", "data"),
                  Output("risk-window", "children"), Input("risk-state", "value"), Input("risk-levels", "value"))
    def update_risk(state, levels):
        res = api("/risk/current", state=state) or {}
        rows = pd.DataFrame(res.get("results", []))
        if rows.empty:
            msg = "No risk scores yet - run train_model.bat"
            return [tile("Status", "–", msg)], empty_figure(msg, 520), [], msg
        win = res["forecast_window"]
        counts = rows["risk_level"].value_counts()
        tiles = [
            tile("High risk", int(counts.get("high", 0)), "districts", "high"),
            tile("Medium risk", int(counts.get("medium", 0)), "districts", "medium"),
            tile("Low risk", int(counts.get("low", 0)), "districts", "low"),
            tile("Forecast window", f"{pd.Timestamp(win['from']):%d %b} – {pd.Timestamp(win['to']):%d %b}",
                 "risk of an outbreak starting in these 4 weeks"),
        ]
        rows["reasons"] = rows["top_factors"].map(lambda f: "; ".join(x["label"] for x in (f or [])))
        rows["reasons_short"] = rows["top_factors"].map(lambda f: "; ".join(x["label"] for x in (f or [])[:2]))
        shown = rows[rows["risk_level"].isin(levels or [])]
        fig = go.Figure()
        for level in ["low", "medium", "high"]:  # high drawn last, on top
            part = shown[shown["risk_level"] == level]
            if part.empty:
                continue
            fig.add_trace(go.Choroplethmap(
                geojson=geojson(), locations=part["census_code"], featureidkey="id",
                z=[1] * len(part), colorscale=[[0, LEVEL_COLOR[level]], [1, LEVEL_COLOR[level]]], showscale=False,
                marker_line_color="white", marker_line_width=0.5, marker_opacity=0.95, name=LEVEL_LABEL[level],
                customdata=list(zip([nice(a, b) for a, b in zip(part["district_name"], part["state_name"])],
                                    part["state_name"], part["risk_score"], part["reasons"])),
                hovertemplate="<b>%{customdata[0]}</b>, %{customdata[1]}<br>" + LEVEL_LABEL[level] +
                              " risk · score %{customdata[2]:.2f}<br>%{customdata[3]}<extra></extra>"))
        # frame the map on the selected districts ("white-bg" needs no map tiles, so it also works offline)
        lat, lon = rows["latitude"], rows["longitude"]
        span = max(lat.max() - lat.min(), (lon.max() - lon.min()) * 0.8, 1.5)
        zoom = max(3.3, min(7.0, math.log2(360 / span) - 0.15))
        fig.update_layout(map=dict(style="white-bg", center=dict(lat=float((lat.max() + lat.min()) / 2),
                                                                lon=float((lon.max() + lon.min()) / 2)), zoom=zoom))
        base_layout(fig, 640, showlegend=False)
        table = [{"district": nice(r.district_name, r.state_name), "state": r.state_name, "level": LEVEL_LABEL[r.risk_level],
                  "score": round(r.risk_score, 2), "reasons": r.reasons_short, "code": r.census_code}
                 for r in rows[rows["risk_level"].isin(["high", "medium"])].itertuples()]
        window = (f"Risk that a new outbreak starts between {pd.Timestamp(win['from']):%d %b %Y} and "
                  f"{pd.Timestamp(win['to']):%d %b %Y}, based on weather up to the week before.")
        return tiles, fig, table, window

    # map click / table click -> open district explorer
    @app.callback(Output("selected-district", "data"), Output("tabs", "value"),
                  Input("risk-map", "clickData"), Input("risk-table", "active_cell"),
                  State("risk-table", "derived_viewport_data"), prevent_initial_call=True)
    def open_district(click, cell, viewport):
        if ctx.triggered_id == "risk-map" and click:
            return int(click["points"][0]["location"]), "district"
        if ctx.triggered_id == "risk-table" and cell and viewport:
            return int(viewport[cell["row"]]["code"]), "district"
        return no_update, no_update

    @app.callback(Output("district-select", "value"), Input("selected-district", "data"))
    def sync_district(code):
        return code

    # ------------------------------------------------------------ callbacks: district page
    @app.callback(Output("district-tiles", "children"), Output("district-timeline", "figure"),
                  Output("district-risk", "figure"), Output("district-reasons", "children"),
                  Output("district-csv", "href"),
                  Input("district-select", "value"), Input("district-period", "value"))
    def update_district(code, years):
        if not code:
            msg = "Choose a district"
            return [], empty_figure(msg), empty_figure(msg, 220), "", "#"
        d = api(f"/districts/{code}")
        s, risk = d["summary"], d.get("current_risk")
        level = risk["risk_level"] if risk else None
        tiles = [
            tile("Current risk", html.Span(className=f"badge {level}", children=LEVEL_LABEL[level]) if level else "–",
                 f"score {risk['risk_score']:.2f} for {pd.Timestamp(risk['target_week_start']):%d %b} – "
                 f"{pd.Timestamp(risk['target_week_end']):%d %b}" if risk else "no score yet"),
            tile("Outbreaks since 2009", s["outbreaks"], f"{s['cases']:,} cases, {s['deaths']} deaths"),
            tile("Years with outbreaks", s["years_with_outbreaks"], "out of 17 years of reports"),
            tile("Last reported outbreak", pd.Timestamp(s["last_outbreak"]).strftime("%d %b %Y")
                 if s["last_outbreak"] else "none", f"{nice(d['district_name'], d['state_name'])}, {d['state_name']}"),
        ]
        to = pd.Timestamp.today().normalize() + pd.Timedelta(days=21)
        tl = pd.DataFrame((api(f"/districts/{code}/timeline", **{"from": str((to - pd.DateOffset(years=years)).date()),
                                                                 "to": str(to.date())}) or {}).get("results", []))
        if tl.empty:
            timeline_fig = empty_figure("No weather loaded for this district")
        else:
            tl["week_start"] = pd.to_datetime(tl["week_start"])
            timeline_fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                                         row_heights=[0.34, 0.33, 0.33],
                                         subplot_titles=("New outbreaks per week", "Rainfall per week (mm)",
                                                         "Minimum temperature (°C, weekly mean)"))
            timeline_fig.add_bar(x=tl["week_start"], y=tl["outbreaks"], marker_color=LEVEL_COLOR["high"], row=1, col=1,
                                 customdata=tl["cases"], hovertemplate="Week of %{x|%d %b %Y}<br>%{y} outbreak(s), "
                                                                       "%{customdata} cases<extra></extra>")
            timeline_fig.add_bar(x=tl["week_start"], y=tl["rain_mm"], marker_color=BLUE, row=2, col=1,
                                 hovertemplate="Week of %{x|%d %b %Y}<br>%{y:.0f} mm<extra></extra>")
            timeline_fig.add_scatter(x=tl["week_start"], y=tl["temp_min_c"], mode="lines", line=dict(color=ORANGE, width=2),
                                     row=3, col=1, hovertemplate="Week of %{x|%d %b %Y}<br>%{y:.1f} °C<extra></extra>")
            fc = tl[tl["includes_forecast"]]
            if not fc.empty:
                for row in (1, 2, 3):
                    timeline_fig.add_vrect(x0=fc["week_start"].min() - pd.Timedelta(days=3),
                                           x1=fc["week_start"].max() + pd.Timedelta(days=4),
                                           fillcolor="#efeee9", opacity=0.6, line_width=0, row=row, col=1)
            base_layout(timeline_fig, 470, showlegend=False, bargap=0.15, hovermode="x unified")
            timeline_fig.update_layout(margin=dict(l=8, r=8, t=26, b=8))
            timeline_fig.update_annotations(font=dict(size=12, color=TEXT2), x=0, xanchor="left")
            timeline_fig.update_yaxes(rangemode="tozero", dtick=1, row=1, col=1)
        hist = pd.DataFrame((api(f"/risk/{code}") or {}).get("results", []))
        if hist.empty:
            risk_fig = empty_figure("No risk history yet", 220)
            reasons = html.P("No risk score yet.", className="sub")
        else:
            hist["target_week_start"] = pd.to_datetime(hist["target_week_start"])
            risk_fig = go.Figure(go.Scatter(
                x=hist["target_week_start"], y=hist["risk_score"], mode="lines+markers",
                line=dict(color=BLUE, width=2), marker=dict(size=8, color=[LEVEL_COLOR[x] for x in hist["risk_level"]],
                                                            line=dict(color="white", width=2)),
                customdata=hist["risk_level"], hovertemplate="4 weeks from %{x|%d %b}<br>score %{y:.2f} "
                                                             "(%{customdata})<extra></extra>"))
            ev = (api("/model") or {}).get("evaluation", {})
            thr = ev.get("thresholds", {})
            for lvl in ("high", "medium"):
                if lvl in thr:
                    risk_fig.add_hline(y=thr[lvl], line_dash="dot", line_color=LEVEL_COLOR[lvl],
                                       annotation_text=f"{LEVEL_LABEL[lvl]} from {thr[lvl]:.2f}",
                                       annotation_position="top left", annotation_font_color=TEXT2)
            base_layout(risk_fig, 220, yaxis_title="Risk score")
            risk_fig.update_yaxes(range=[0, max(0.6, hist["risk_score"].max() * 1.15)])
            latest = hist.iloc[-1]
            reasons = html.Div([html.P("Factors that raised this district's latest score most:", className="sub"),
                                reasons_list(latest["top_factors"])])
        return tiles, timeline_fig, risk_fig, reasons, f"/api/v1/export/outbreaks.csv?district_code={code}"

    # ------------------------------------------------------------ callbacks: compare page
    @app.callback(Output("cmp-heatmap", "figure"), Output("cmp-season", "figure"), Output("cmp-years-chart", "figure"),
                  Input("cmp-state", "value"), Input("cmp-years", "value"))
    def update_compare(state, years):
        y0, y1 = years
        res = (api("/outbreaks", state=state or None, limit=5000) or {}).get("results", [])
        ob = pd.DataFrame(res)
        if ob.empty:
            msg = "No outbreaks for this selection"
            return empty_figure(msg, 520), empty_figure(msg), empty_figure(msg)
        ob = ob[(ob["report_year"] >= y0) & (ob["report_year"] <= y1) & ob["district_name"].notna()]
        if ob.empty:
            msg = "No outbreaks in these years"
            return empty_figure(msg, 520), empty_figure(msg), empty_figure(msg)
        label = pd.Series([nice(a, b) for a, b in zip(ob["district_name"], ob["state_name"])], index=ob.index)
        if not state:
            label = label + ", " + ob["state_name"]
        mat = pd.crosstab(label, ob["report_year"]).reindex(columns=range(y0, y1 + 1), fill_value=0)
        mat = mat.loc[mat.sum(axis=1).sort_values(ascending=False).index[:25]][::-1]
        heat = go.Figure(go.Heatmap(
            z=mat.to_numpy(), x=[str(c) for c in mat.columns], y=mat.index, colorscale=SEQ_BLUE, xgap=2, ygap=2,
            colorbar=dict(title=dict(text="Outbreaks", side="right"), thickness=10),
            hovertemplate="%{y} · %{x}<br>%{z} outbreak(s)<extra></extra>"))
        base_layout(heat, max(320, 22 * len(mat) + 60))
        heat.update_xaxes(showgrid=False, side="top")
        heat.update_yaxes(showgrid=False)
        ob["month"] = pd.to_datetime(ob["start_date"]).dt.month
        months = ob["month"].value_counts().reindex(range(1, 13), fill_value=0)
        season = go.Figure(go.Bar(
            x=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            y=months.to_numpy(), marker_color=BLUE, hovertemplate="%{x}: %{y} outbreaks<extra></extra>"))
        base_layout(season, 280, yaxis_title="Outbreaks")
        per_year = ob["report_year"].value_counts().reindex(range(y0, y1 + 1), fill_value=0)
        yearly = go.Figure(go.Bar(x=per_year.index, y=per_year.to_numpy(), marker_color=BLUE,
                                  hovertemplate="%{x}: %{y} outbreaks<extra></extra>"))
        if y0 <= 2020 <= y1:
            yearly.add_annotation(x=2020, y=per_year.get(2020, 0), text="COVID-19", showarrow=True, arrowhead=0,
                                  ay=-30, font=dict(color=TEXT2, size=11))
        base_layout(yearly, 280, yaxis_title="Outbreaks")
        yearly.update_xaxes(dtick=1 if y1 - y0 <= 10 else 2)
        return heat, season, yearly

    return app
