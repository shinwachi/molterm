"""Molterm TUI Renderer — TUI renderer for chemical structures using RDKit and advanced text graphics"""
import os

from flask import Flask, jsonify, render_template

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
    return jsonify(status="ok", **SERVICE)


@app.route("/api/v1/hello")
def hello():
    return jsonify(message="hello from molterm")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8910)
