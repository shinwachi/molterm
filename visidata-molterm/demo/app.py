"""The demo's web page: a recorded VisiData session with the plugin, played in a web terminal that shows Kitty graphics.

Sessions are recorded by demo/record.py against the molterm server at $MOLTERM_URL: the demo's own when the service starts, and the
short scene for the main app's /showcase when that asks for it, at the size of the visitor's terminal there. Each is recorded again
whenever the plugin, the script, the data or its settings are newer than the recording. One is recorded at a time; until a recording
is ready its request is answered 503, and the page asks again.
"""
import glob
import os
import subprocess
import sys
import threading

from flask import Flask, jsonify, render_template, request, send_from_directory

import record

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
VENDOR = os.environ.get("VENDOR_DIR", "/opt/vendor")
SOURCES = [os.path.join(ROOT, "molterm_visidata.py"), os.path.join(HERE, "record.py"), os.path.join(HERE, "drugs.csv")]
SHOWCASE_COLS, SHOWCASE_ROWS = (80, 240), (24, 80)  # the showcase's terminal can be any size in these
KEPT_SHOWCASES = 24  # recordings of the showcase's scene kept, for as many sizes; the oldest go first

app = Flask(__name__)
_state = {"recording": None, "errors": {}}  # recording: the (script, cols, rows) being recorded
_lock = threading.Lock()


def stale(job=("demo", record.COLS, record.ROWS)):
    """Whether a recording is missing, or older than what it shows."""
    script = job[0]
    try:
        made = os.path.getmtime(record.output_path(*job))
    except OSError:
        return True
    sources = SOURCES + [os.path.join(HERE, record.SCRIPTS[script][1])]
    return any(os.path.getmtime(path) > made for path in sources if os.path.exists(path))


def _prune():
    kept = sorted(glob.glob(os.path.join(os.path.dirname(record.OUT), "showcase-*.json")), key=os.path.getmtime, reverse=True)
    for path in kept[KEPT_SHOWCASES:]:
        os.remove(path)


def _record(job):
    # in a process of its own, since forking a terminal from this threaded server could deadlock
    script, cols, rows = job
    try:
        done = subprocess.run([sys.executable, os.path.join(HERE, "record.py"), script, str(cols), str(rows)],
                              capture_output=True, text=True, timeout=600)
        if done.returncode == 0:
            _state["errors"].pop(job, None)
        else:
            _state["errors"][job] = (done.stderr.strip().splitlines() or ["the recorder failed"])[-1]
        _prune()
    except (OSError, subprocess.SubprocessError) as error:  # shown on the page, which then offers nothing to play
        _state["errors"][job] = f"{type(error).__name__}: {error}"
    finally:
        _state["recording"] = None


def ensure_recording(job=("demo", record.COLS, record.ROWS)):
    """Start recording in the background if the recording is stale and nothing is being recorded already."""
    with _lock:
        if _state["recording"] or not stale(job):
            return
        _state["recording"] = job
    threading.Thread(target=_record, args=(job,), daemon=True).start()


def _job(values):
    """The recording a request asks for: the demo's, or the showcase's scene at a size; None if it can't be."""
    script = values.get("script", "demo")
    if script == "demo":
        return ("demo", record.COLS, record.ROWS)
    if script != "showcase":
        return None
    try:
        cols, rows = int(values.get("cols", "")), int(values.get("rows", ""))
    except ValueError:
        return None
    if not (SHOWCASE_COLS[0] <= cols <= SHOWCASE_COLS[1] and SHOWCASE_ROWS[0] <= rows <= SHOWCASE_ROWS[1]):
        return None
    return ("showcase", cols, rows)


@app.route("/")
def index():
    return render_template("index.html", molterm_url=os.environ.get("MOLTERM_PUBLIC_URL", "http://molterm.localhost"))


@app.route("/health")
def health():
    return jsonify(status="ok", recording=_state["recording"] is not None, ready=os.path.exists(record.OUT))


@app.route("/api/v1/recording")
def recording():
    """A session as {cols, rows, recorded, frames: [{caption, key, output, pause}]}, or 503 while it's being recorded.
    ?script=showcase&cols=&rows= asks for the showcase's scene at that size."""
    job = _job(request.args)
    if job is None:
        return jsonify(error={"code": "bad_request", "message": f"Ask for script=demo, or script=showcase with cols from "
                              f"{SHOWCASE_COLS[0]} to {SHOWCASE_COLS[1]} and rows from {SHOWCASE_ROWS[0]} to {SHOWCASE_ROWS[1]}."}), 400
    ensure_recording(job)
    path = record.output_path(*job)
    if _state["recording"] == job or not os.path.exists(path) or stale(job):
        busy = _state["recording"] is not None
        message = _state["errors"].get(job) or "The session is being recorded against the molterm server. This takes about half a minute."
        return jsonify(error={"code": "recording" if busy else "failed", "message": message}), 503
    with open(path) as f:
        return app.response_class(f.read(), mimetype="application/json")


@app.route("/vendor/<path:name>")
def vendor(name):
    """Front-end libraries and the house stylesheet, fetched and checked when the image is built."""
    return send_from_directory(VENDOR, name)


if not os.environ.get("MOLTERM_VD_NO_RECORDING"):  # tests make their own
    ensure_recording()
