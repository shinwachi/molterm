"""The agent server: a page with the TUI (Python in Pyodide, shown in restty), and sessions, each with its own agent.

  POST /api/v1/sessions                       a new session, whose agent starts at once     -> {"id"}
  GET  /api/v1/sessions/<id>/events?after=N   the agent's events, as Server-Sent Events, from the N-th on
  POST /api/v1/sessions/<id>/messages         {"text", "cols"}: what the user typed
  POST /api/v1/sessions/<id>/tool_results     {"id", "result"}: the TUI's answer to one of its tools
  POST /api/v1/sessions/<id>/interrupt        stop the agent's turn
"""
import json
import os
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context

from agent import TOOLS, EmulatedAgent, Molterm
from sessions import SessionError, Sessions

ROOT = Path(__file__).resolve().parent.parent
TUI_DIR = ROOT / "tui"
VENDOR = Path(os.environ.get("VENDOR_DIR", "/opt/vendor"))
MOLTERM_URL = os.environ.get("MOLTERM_URL", "http://molterm-prod-molterm-1:8910")
KEEPALIVE = 15  # seconds between comments on an idle stream, so that proxies keep it open
MAX_TEXT = 10_000

app = Flask(__name__)
molterm = Molterm(MOLTERM_URL)
sessions = Sessions(lambda session: EmulatedAgent(session, molterm, pace=float(os.environ.get("AGENT_PACE", "1"))))


@app.errorhandler(SessionError)
def session_error(error):
    return jsonify(error={"code": error.code, "message": error.message}), error.status


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise SessionError("bad_request", "Send a JSON object.")
    return data


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    pyodide = (VENDOR / "pyodide" / "pyodide.asm.wasm").is_file()
    status = "ok" if pyodide else "degraded"
    return jsonify(service="molterm-agent", status=status, env=os.environ.get("APP_ENV", "dev"), sessions=sessions.count(),
                   pyodide=pyodide, agent="emulated"), 200 if pyodide else 503


@app.route("/vendor/<path:name>")
def vendor(name):
    return send_from_directory(VENDOR, name, max_age=7 * 24 * 3600)


@app.route("/tui/manifest.json")
def tui_manifest():
    """The TUI's Python files, for the page to copy into Pyodide's file system."""
    files = sorted(str(p.relative_to(TUI_DIR)) for p in TUI_DIR.rglob("*.py"))
    return jsonify(files=files)


@app.route("/tui/<path:name>")
def tui_file(name):
    if not name.endswith(".py"):
        return jsonify(error={"code": "not_found", "message": "Only the TUI's Python files are here."}), 404
    return send_from_directory(TUI_DIR, name, mimetype="text/x-python", max_age=0)


@app.route("/api/v1/tools")
def tools():
    return jsonify(tools=TOOLS)


@app.route("/api/v1/sessions", methods=["POST"])
def create_session():
    session = sessions.create()
    return jsonify(id=session.id), 201


@app.route("/api/v1/sessions/<session_id>/events")
def events(session_id):
    session = sessions.get(session_id)
    after = request.args.get("after", request.headers.get("Last-Event-ID", "-1"))
    start = int(after) + 1 if after.lstrip("-").isdigit() else 0

    def stream():
        position = start
        yield "retry: 2000\n\n"
        while not session.closed:
            fresh = session.wait_events(position, KEEPALIVE)
            if not fresh:
                yield ": keepalive\n\n"
                continue
            for event in fresh:
                yield f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"
            position += len(fresh)
            session.touched = time.time()

    return Response(stream_with_context(stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/v1/sessions/<session_id>/messages", methods=["POST"])
def message(session_id):
    session, data = sessions.get(session_id), body()
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SessionError("bad_request", "Send the message as \"text\".")
    if len(text) > MAX_TEXT:
        raise SessionError("too_large", f"A message can be at most {MAX_TEXT:,} characters.")
    cols = data.get("cols", 72)
    session.post({"text": text.strip(), "cols": cols if isinstance(cols, int) and 20 <= cols <= 300 else 72})
    return jsonify(queued=True), 202


@app.route("/api/v1/sessions/<session_id>/tool_results", methods=["POST"])
def tool_result(session_id):
    session, data = sessions.get(session_id), body()
    tool_id, result = data.get("id"), data.get("result")
    if not isinstance(tool_id, str) or not isinstance(result, dict):
        raise SessionError("bad_request", "Send the tool's \"id\" and its \"result\" as an object.")
    session.put_result(tool_id, result)
    return jsonify(ok=True)


@app.route("/api/v1/sessions/<session_id>/interrupt", methods=["POST"])
def interrupt(session_id):
    sessions.get(session_id).interrupt()
    return jsonify(ok=True)
