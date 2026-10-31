"""Molterm TUI Renderer — TUI renderer for chemical structures using RDKit and advanced text graphics"""
import os

import rdkit
from flask import Flask, jsonify, render_template, request

import molecule
import render as renderer

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)


def version():
    try:
        with open(os.path.join(HERE, "..", "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "dev"


SERVICE = {"service": "molterm", "version": version(), "env": os.environ.get("APP_ENV", "dev")}


@app.route("/")
def index():
    return render_template("index.html", cols=renderer.DEFAULT_COLS, rows=renderer.DEFAULT_ROWS,
                           cols_range=renderer.COLS_RANGE, rows_range=renderer.ROWS_RANGE, **SERVICE)


@app.route("/health")
def health():
    return jsonify(status="ok", rdkit=rdkit.__version__, **SERVICE)


@app.route("/api/v1/hello")
def hello():
    return jsonify(message="hello from molterm")


@app.route("/api/v1/parse", methods=["POST"])
def parse():
    """JSON body {"input": "<SMILES or molblock>", "format": "auto|smiles|molblock"}; `format` is optional."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error={"code": "bad_request", "message": "Send a JSON object with an \"input\" field."}), 400
    try:
        return jsonify(molecule.parse(body.get("input"), body.get("format", "auto")).to_dict())
    except molecule.ParseError as e:
        return jsonify(error={"code": e.code, "message": e.message}), 400


def canvas_size(values):
    """Read cols and rows from a mapping of request values, or raise ParseError."""
    sizes = []
    for name, default, (low, high) in (("cols", renderer.DEFAULT_COLS, renderer.COLS_RANGE),
                                       ("rows", renderer.DEFAULT_ROWS, renderer.ROWS_RANGE)):
        value = values.get(name, default)
        try:
            if isinstance(value, (bool, float)):
                raise ValueError
            size = int(value)
        except (TypeError, ValueError):
            size = None
        if size is None or not low <= size <= high:
            raise molecule.ParseError("bad_request", f"{name} must be a whole number from {low} to {high}.")
        sizes.append(size)
    return sizes


@app.route("/api/v1/render", methods=["POST"])
def render_json():
    """Like /api/v1/parse, with optional `cols` and `rows`; the result adds `render` (text, ansi, labels, ...)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error={"code": "bad_request", "message": "Send a JSON object with an \"input\" field."}), 400
    try:
        cols, rows = canvas_size(body)
        parsed = molecule.parse(body.get("input"), body.get("format", "auto"))
    except molecule.ParseError as e:
        return jsonify(error={"code": e.code, "message": e.message}), 400
    return jsonify(**parsed.to_dict(), render=renderer.render(parsed.mol, cols, rows).to_dict())


@app.route("/api/v1/render.txt")
def render_text():
    """Plain text for terminals: ?input=<SMILES or molblock>[&format=][&cols=][&rows=][&color=1]."""
    plain = {"Content-Type": "text/plain; charset=utf-8"}
    try:
        cols, rows = canvas_size(request.args)
        parsed = molecule.parse(request.args.get("input"), request.args.get("format", "auto"))
    except molecule.ParseError as e:
        return e.message + "\n", 400, plain
    result = renderer.render(parsed.mol, cols, rows)
    return (result.ansi if request.args.get("color") == "1" else result.text) + "\n", 200, plain


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8910)
