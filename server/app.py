"""Molterm TUI Renderer — TUI renderer for chemical structures using RDKit and advanced text graphics"""
import os

import rdkit
from flask import Flask, jsonify, render_template, request

import molecule

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
    return render_template("index.html", **SERVICE)


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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8910)
