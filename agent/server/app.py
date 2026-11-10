"""The agent server: a page with the TUI (Python in Pyodide, shown in restty), and sessions, each with its own agent.

  POST /api/v1/sessions                       a new session, whose agent starts at once     -> {"id"}; with {"tutor": true}, in
                                               tutorial mode: a tutor watches it and chimes in (tutor.py), or {"tutor": "<name>"},
                                               one of TUTORS (an unknown name is refused); "context": {...} rides with the session
  GET  /api/v1/tutors                         the tutors a session can name, and whether there's a server's own
  GET  /api/v1/sessions/<id>/events?after=N   the agent's events, as Server-Sent Events, from the N-th on
  POST /api/v1/sessions/<id>/messages         {"text", "cols"}: what the user typed
  POST /api/v1/sessions/<id>/tool_results     {"id", "result"}: the TUI's answer to one of its tools
  POST /api/v1/sessions/<id>/interrupt        stop the agent's turn
  GET  /api/v1/sessions/<id>                  whether the session is open, where its agent runs, and whether it has a tutor
  POST /api/v1/sessions/<id>/tutor            tutorial mode for a session going already, whatever its agent: its tutor
                                               starts from now on; optional {"tutor": true or "<name>", "context"} -> {"tutor"}
  POST /api/v1/sessions/<id>/edit             one step of molterm's editor, for a molecule edited where it's shown (see the TUI)
  POST /api/v1/sessions/<id>/picture          {"smiles", "cols", "mark", "clear"}: a molecule's picture with its annotations, the
                                               notes left for the page's bubbles  -> molterm's /api/v1/callouts answer

For an agent that runs elsewhere (sdk/tuiagent/remote.py; see TUTORIAL.md), with the session's token as "Authorization: Bearer":
  POST /api/v1/agents/sessions                       a session with no agent of the server's   -> {"id", "token", "url"}; with
                                                     {"tutor": true or "<name>"}, a tutor watches it; with "context", as above
  POST /api/v1/sessions/<id>/agent/events            {"type", ...}: an event for the transcript
  GET  /api/v1/sessions/<id>/agent/messages?timeout  the user's next message, waiting up to `timeout` s -> {"message", "interrupted", "closed"}
  GET  /api/v1/sessions/<id>/agent/tool_results/<tool id>?timeout   a TUI tool's answer             -> {"result", "interrupted", "closed"}
  GET  /api/v1/sessions/<id>/agent                   -> {"interrupted", "closed"}

The agent the server runs itself is EmulatedAgent, or the class named by AGENT ("module:Class", made with the session alone); the
tutor, EmulatedTutor, or the class named by TUTOR, the same way; and the tutors a session can name, TUTORS: "name=module:Class",
several separated by commas or spaces (names of letters, digits, - and _).
"""
import hmac
import importlib
import json
import os
import re
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context

from agent import TOOLS, EmulatedAgent, Molterm
from sessions import MAX_CONTEXT, SessionError, Sessions
from tutor import EmulatedTutor

ROOT = Path(__file__).resolve().parent.parent
TUI_DIR = ROOT / "tui"
VENDOR = Path(os.environ.get("VENDOR_DIR", "/opt/vendor"))
MOLTERM_URL = os.environ.get("MOLTERM_URL", "http://molterm-prod-molterm-1:8910")
KEEPALIVE = 15  # seconds between comments on an idle stream, so that proxies keep it open
MAX_TEXT = 10_000

app = Flask(__name__)
molterm = Molterm(MOLTERM_URL)
AGENT = os.environ.get("AGENT", "")
TUTOR = os.environ.get("TUTOR", "")
TUTORS = os.environ.get("TUTORS", "")
AGENT_EVENTS = {"turn_start", "status", "text", "text_end", "tool_use", "tool_result", "error", "turn_end", "suggest"}
MAX_EVENT = 4 * 1024 * 1024  # a picture's PNG travels in its tui.show event
MAX_POLL = 25


def agent_factory():
    """What makes the server's own agent for a session: EmulatedAgent, or the class AGENT names."""
    pace = float(os.environ.get("AGENT_PACE", "1"))
    if not AGENT:
        return lambda session: EmulatedAgent(session, molterm, pace=pace)
    module, _, name = AGENT.partition(":")
    agent_class = getattr(importlib.import_module(module), name)
    return lambda session: agent_class(session)


def _made_by(spec):
    """A factory for the class "module:Class" names, made with the session alone."""
    module, _, name = spec.partition(":")
    tutor_class = getattr(importlib.import_module(module), name)
    return lambda session: tutor_class(session)


def tutor_factory():
    """What makes a session's tutor, in tutorial mode: EmulatedTutor, or the class TUTOR names."""
    pace = float(os.environ.get("AGENT_PACE", "1"))
    if not TUTOR:
        return lambda session: EmulatedTutor(session, pace=pace)
    return _made_by(TUTOR)


def named_tutors(spec=None):
    """The tutors a session can name, from TUTORS: {"lesson-1": factory, ...}."""
    tutors = {}
    for entry in (TUTORS if spec is None else spec).replace(",", " ").split():
        name, _, target = entry.partition("=")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name) or ":" not in target:
            raise ValueError(f"TUTORS: {entry!r} isn't name=module:Class")
        tutors[name] = _made_by(target)
    return tutors


sessions = Sessions(agent_factory(), tutor_factory(), named_tutors())


@app.errorhandler(SessionError)
def session_error(error):
    return jsonify(error={"code": error.code, "message": error.message}), error.status


def wants():
    """What a new session (or its tutor, started later) is asked for, the body being optional: (tutor: True, a name, or False;
    context: a dict or None)."""
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    tutor = data.get("tutor", False)
    if not (isinstance(tutor, bool) or isinstance(tutor, str) and len(tutor) <= 40):
        raise SessionError("bad_request", "\"tutor\" is true, or a tutor's name.")
    context = data.get("context")
    if context is not None and (not isinstance(context, dict) or len(json.dumps(context)) > MAX_CONTEXT):
        raise SessionError("bad_request", f"\"context\" is an object, at most {MAX_CONTEXT // 1024} KB as JSON.")
    return tutor, context


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
                   pyodide=pyodide, agent=AGENT or "emulated"), 200 if pyodide else 503


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
    tutor, context = wants()
    session = sessions.create(tutor=tutor, context=context)
    return jsonify(id=session.id), 201


@app.route("/api/v1/sessions/<session_id>")
def session_info(session_id):
    session = sessions.get(session_id)
    return jsonify(id=session.id, agent="remote" if session.token else "server", tutor=session.tutor)


@app.route("/api/v1/sessions/<session_id>/tutor", methods=["POST"])
def start_tutor(session_id):
    session = sessions.get(session_id)
    tutor, context = wants()
    if not sessions.start_tutor(session, tutor if tutor else True, context):
        raise SessionError("no_tutor", "This server has no tutor to give.", 404)
    return jsonify(tutor=session.tutor)


@app.route("/api/v1/tutors")
def tutors():
    return jsonify(default=sessions.tutor_factory is not None, named=sorted(sessions.tutors))


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


# -- molecules edited in place: molterm's editor and pictures, for the page, which can't reach molterm itself -------------------------

MAX_EDIT = 2_000_000  # a drawing's state travels with each step


@app.route("/api/v1/sessions/<session_id>/edit", methods=["POST"])
def edit(session_id):
    sessions.get(session_id)
    if (request.content_length or 0) > MAX_EDIT:
        raise SessionError("too_large", "That edit is too large.")
    try:
        status, text = molterm.edit(body())
    except requests.RequestException as error:
        raise SessionError("unavailable", f"molterm can't be reached: {error.__class__.__name__}", 502)
    return Response(text, status=200 if status == 200 else 502 if status >= 500 else 400, mimetype="text/plain")


@app.route("/api/v1/sessions/<session_id>/picture", methods=["POST"])
def picture(session_id):
    sessions.get(session_id)
    data = body()
    smiles, cols, mark = data.get("smiles"), data.get("cols", 72), data.get("mark")
    if not isinstance(smiles, str) or not smiles or len(smiles) > MAX_TEXT or not isinstance(cols, int):
        raise SessionError("bad_request", "Send the molecule as \"smiles\", and \"cols\".")
    if mark is not None and not isinstance(mark, dict):
        raise SessionError("bad_request", "mark must be an object.")
    try:
        status, answer = molterm.callouts(smiles, cols, mark, data.get("clear") is True)
    except requests.RequestException as error:
        raise SessionError("unavailable", f"molterm couldn't draw it: {error}", 502)
    if status != 200:  # molterm's own words, such as a SMARTS it can't read
        raise SessionError("bad_request", answer.get("error", {}).get("message", "molterm couldn't draw it."))
    return jsonify(answer)


# -- for an agent that runs elsewhere ------------------------------------------------------------------------------------------------

def agent_session(session_id):
    """The session, if the request carries its token."""
    session = sessions.get(session_id)
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not session.token or not hmac.compare_digest(sent, session.token):
        raise SessionError("forbidden", "This session's agent runs on the server, or the token is wrong.", 403)
    return session


def poll_timeout():
    value = request.args.get("timeout", "0")
    return min(float(value), MAX_POLL) if value.replace(".", "", 1).isdigit() else 0


def state(session, **data):
    return jsonify(interrupted=session.interrupted, closed=session.closed, **data)


@app.route("/api/v1/agents/sessions", methods=["POST"])
def create_agent_session():
    tutor, context = wants()
    session = sessions.create(external=True, tutor=tutor, context=context)
    return jsonify(id=session.id, token=session.token, url=f"{request.host_url.rstrip('/')}/?session={session.id}",
                   tutor=session.tutor, context=session.context), 201


@app.route("/api/v1/sessions/<session_id>/agent")
def agent_state(session_id):
    return state(agent_session(session_id))


@app.route("/api/v1/sessions/<session_id>/agent/events", methods=["POST"])
def agent_event(session_id):
    session = agent_session(session_id)
    if (request.content_length or 0) > MAX_EVENT:
        raise SessionError("too_large", f"An event can be at most {MAX_EVENT // 1024 // 1024} MB.", 413)
    data = body()
    type_ = data.pop("type", None)
    if type_ not in AGENT_EVENTS:
        raise SessionError("bad_request", f"\"type\" must be one of {', '.join(sorted(AGENT_EVENTS))}.")
    data.pop("seq", None)
    session.emit(type_, **data)
    return state(session)


@app.route("/api/v1/sessions/<session_id>/agent/messages")
def agent_messages(session_id):
    session = agent_session(session_id)
    return state(session, message=session.next_message(poll_timeout()))


@app.route("/api/v1/sessions/<session_id>/agent/tool_results/<tool_id>")
def agent_tool_result(session_id, tool_id):
    session = agent_session(session_id)
    return state(session, result=session.wait_result(tool_id, poll_timeout()))
