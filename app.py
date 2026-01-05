from flask import Flask
from dash import Dash, html

# Flask server that Dash will attach to
server = Flask(__name__)

# Simple Dash app for demo purposes
dash_app = Dash(__name__, server=server, url_base_pathname="/")
dash_app.layout = html.Div(
    [
        html.H1("Ralph Orchestrator Dashboard", style={"marginBottom": "0.25rem"}),
        html.Div("Quick status view (static demo content)", style={"color": "#555", "marginBottom": "1rem"}),
        html.Div(
            [
                html.Div(
                    [
                        html.H3("Run 000"),
                        html.Div("Status: stopped_by_loop_detection"),
                        html.Div("Iterations: 5 (success 5 / fail 0)"),
                        html.Div("Adapter: ollama • Model: gemma3:1b"),
                        html.Div("Last hash root: 421be66c9c25…bc2"),
                    ],
                    style={
                        "padding": "1rem",
                        "border": "1px solid #ddd",
                        "borderRadius": "8px",
                        "backgroundColor": "#f7f9fb",
                        "marginBottom": "1rem",
                    },
                ),
                html.Div(
                    [
                        html.H4("Useful commands"),
                        html.Ul(
                            [
                                html.Li("python app.py  # dev server"),
                                html.Li("gunicorn --bind 0.0.0.0:8000 app:server  # prod-ish"),
                                html.Li("ralph run -v --output-verbosity verbose"),
                            ]
                        ),
                    ],
                    style={
                        "padding": "1rem",
                        "border": "1px solid #eee",
                        "borderRadius": "8px",
                        "backgroundColor": "#fff",
                    },
                ),
            ],
            style={"display": "grid", "gap": "1rem"},
        ),
    ],
    style={
        "maxWidth": "720px",
        "margin": "0 auto",
        "padding": "2rem",
        "fontFamily": "system-ui, -apple-system, sans-serif",
    },
)


@server.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    dash_app.run(host="0.0.0.0", port=8000, debug=True)
