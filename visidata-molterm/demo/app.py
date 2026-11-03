"""The demo's web page: a recorded VisiData session with the plugin, played in a web terminal that shows Kitty graphics.

The session is recorded by demo/record.py against the molterm server at $MOLTERM_URL, when the service starts and whenever the plugin,
the script or the data are newer than the recording. Until it's ready the page says so and waits.
"""
import os
import subprocess
import sys
import threading

from flask import Flask, jsonify, render_template, send_from_directory

import record

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
VENDOR = os.environ.get("VENDOR_DIR", "/opt/vendor")
SOURCES = [os.path.join(ROOT, "molterm_visidata.py"), os.path.join(HERE, "record.py"), os.path.join(HERE, "drugs.csv"),
           os.path.join(HERE, "visidatarc")]

app = Flask(__name__)
_state = {"recording": False, "error": None}
_lock = threading.Lock()


def stale():
    """Whether the recording is missing, or older than what it shows."""
    try:
        made = os.path.getmtime(record.OUT)
    except OSError:
        return True
    return any(os.path.getmtime(path) > made for path in SOURCES if os.path.exists(path))


def _record():
    # in a process of its own, since forking a terminal from this threaded server could deadlock
    try:
        done = subprocess.run([sys.executable, os.path.join(HERE, "record.py")], capture_output=True, text=True, timeout=600)
        _state["error"] = None if done.returncode == 0 else (done.stderr.strip().splitlines() or ["the recorder failed"])[-1]
    except (OSError, subprocess.SubprocessError) as error:  # shown on the page, which then offers nothing to play
        _state["error"] = f"{type(error).__name__}: {error}"
    finally:
        _state["recording"] = False


def ensure_recording():
    """Start recording in the background if the recording is stale and nobody is recording it already."""
    with _lock:
        if _state["recording"] or not stale():
            return
        _state["recording"] = True
    threading.Thread(target=_record, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html", molterm_url=os.environ.get("MOLTERM_PUBLIC_URL", "http://molterm.localhost"))


@app.route("/health")
def health():
    return jsonify(status="ok", recording=_state["recording"], ready=os.path.exists(record.OUT))


@app.route("/api/v1/recording")
def recording():
    """The session as {cols, rows, recorded, frames: [{caption, key, output, pause}]}, or 503 while it's being recorded."""
    ensure_recording()
    if _state["recording"] or not os.path.exists(record.OUT):
        message = _state["error"] or "The session is being recorded against the molterm server. This takes about half a minute."
        return jsonify(error={"code": "recording" if _state["recording"] else "failed", "message": message}), 503
    with open(record.OUT) as f:
        return app.response_class(f.read(), mimetype="application/json")


@app.route("/vendor/<path:name>")
def vendor(name):
    """Front-end libraries and the house stylesheet, fetched and checked when the image is built."""
    return send_from_directory(VENDOR, name)


if not os.environ.get("MOLTERM_VD_NO_RECORDING"):  # tests make their own
    ensure_recording()
