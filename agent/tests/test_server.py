"""The server: sessions, the event stream, messages, the TUI's tool answers, and the emulated agent's script, with molterm stubbed."""
import json
import threading
import time

import pytest

import agent as agent_module
import app as server
from sessions import Session, Sessions


class FakeMolterm:
    def render(self, smiles, cols):
        if smiles == "C1CC":
            raise ValueError("Not valid SMILES")
        return "\x1b[31mO\x1b[0m⠒⠒\n⠉⠉"

    def properties(self, smiles):
        return {"formula": "C2H6O", "mw": 46.07, "logp": -0.0014, "tpsa": 20.23, "hbd": 1, "hba": 1, "qed": 0.41, "ro5": 0}


@pytest.fixture
def client(monkeypatch):
    sessions = Sessions(lambda session: agent_module.EmulatedAgent(session, FakeMolterm(), pace=0))
    monkeypatch.setattr(server, "sessions", sessions)
    return server.app.test_client()


def wait_for(session, predicate, timeout=5):
    """The session's events once one of them satisfies `predicate`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(predicate(e) for e in session.events):
            return session.events
        time.sleep(0.01)
    raise AssertionError(f"no such event in {[e['type'] for e in session.events]}")


def answer_tools(client, session, choose):
    """Play the page: answer each TUI tool as it comes, choosing with `choose(input)`."""
    answered = set()

    def run():
        deadline = time.time() + 5
        while time.time() < deadline and not session.closed:
            for event in list(session.events):
                if event["type"] == "tool_use" and event["name"].startswith("tui.") and event["id"] not in answered:
                    answered.add(event["id"])
                    result = {"shown": True} if event["name"] == "tui.show" else choose(event["input"])
                    client.post(f"/api/v1/sessions/{session.id}/tool_results", json={"id": event["id"], "result": result})
            time.sleep(0.01)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_a_session_starts_with_a_greeting(client):
    response = client.post("/api/v1/sessions")
    assert response.status_code == 201
    session = server.sessions.get(response.json["id"])
    events = wait_for(session, lambda e: e["type"] == "turn_end")
    assert events[0] == {"seq": 0, "type": "session", "id": session.id}
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert text.startswith("Hello! I'm an emulated agent")


def test_the_event_stream_sends_the_log_as_server_sent_events(client):
    session_id = client.post("/api/v1/sessions").json["id"]
    wait_for(server.sessions.get(session_id), lambda e: e["type"] == "turn_end")
    response = client.get(f"/api/v1/sessions/{session_id}/events?after=0", buffered=False)
    assert response.mimetype == "text/event-stream"
    received = ""
    for chunk in response.response:
        received += chunk.decode() if isinstance(chunk, bytes) else chunk
        if '"turn_end"' in received:
            break
    response.close()
    assert received.startswith("retry: 2000")
    assert "id: 0\n" not in received  # after=0: from the second event on
    first = received.split("\n\n")[1]
    assert first.startswith("id: 1\ndata: ")
    assert json.loads(first.split("data: ", 1)[1])["type"] == "turn_start"


def test_a_smiles_is_drawn_shown_in_the_tui_and_its_properties_offered(client):
    session_id = client.post("/api/v1/sessions").json["id"]
    session = server.sessions.get(session_id)
    wait_for(session, lambda e: e["type"] == "turn_end")
    answer_tools(client, session, lambda input_: {"choice": "Yes", "index": 0})
    assert client.post(f"/api/v1/sessions/{session_id}/messages", json={"text": "CCO", "cols": 60}).status_code == 202
    events = wait_for(session, lambda e: e["type"] == "text" and "drug-like" in e["delta"] or (e["type"] == "text" and "QED" in e["delta"]))
    tools = [(e["name"], e["input"].get("title") or e["input"].get("smiles") or e["input"].get("question")) for e in events if e["type"] == "tool_use"]
    assert tools == [("molterm.render", "CCO"), ("tui.show", "CCO"), ("tui.ask", "Do you want the properties of this molecule too?"),
                     ("molterm.properties", "CCO"), ("tui.show", "Properties of CCO")]
    shown = next(e for e in events if e["type"] == "tool_use" and e["name"] == "tui.show")
    assert "⠒⠒" in shown["input"]["body"]


def test_asking_for_a_drug_goes_through_a_question(client):
    session_id = client.post("/api/v1/sessions").json["id"]
    session = server.sessions.get(session_id)
    wait_for(session, lambda e: e["type"] == "turn_end")
    answer_tools(client, session, lambda input_: {"choice": input_["options"][1], "index": 1})
    client.post(f"/api/v1/sessions/{session_id}/messages", json={"text": "show me a drug"})
    events = wait_for(session, lambda e: e["type"] == "tool_use" and e["name"] == "molterm.render")
    render = [e for e in events if e["type"] == "tool_use" and e["name"] == "molterm.render"][0]
    assert render["input"]["smiles"] == agent_module.SAMPLES["Caffeine"]


def test_a_failing_server_tool_is_reported_not_fatal(client):
    session_id = client.post("/api/v1/sessions").json["id"]
    session = server.sessions.get(session_id)
    wait_for(session, lambda e: e["type"] == "turn_end")
    client.post(f"/api/v1/sessions/{session_id}/messages", json={"text": "C1CC"})
    events = wait_for(session, lambda e: e["type"] == "tool_result" and e.get("is_error"))
    assert any("Not valid SMILES" in e["output"] for e in events if e["type"] == "tool_result")
    wait_for(session, lambda e: e["type"] == "text" and "couldn't draw" in e["delta"] or (e["type"] == "text" and "typos" in e["delta"]))


def test_interrupting_ends_a_turn_waiting_on_the_user(client):
    session_id = client.post("/api/v1/sessions").json["id"]
    session = server.sessions.get(session_id)
    wait_for(session, lambda e: e["type"] == "turn_end")
    client.post(f"/api/v1/sessions/{session_id}/messages", json={"text": "pick one"})
    wait_for(session, lambda e: e["type"] == "tool_use" and e["name"] == "tui.ask")
    ends = sum(e["type"] == "turn_end" for e in session.events)
    client.post(f"/api/v1/sessions/{session_id}/interrupt")
    wait_for(session, lambda e: sum(x["type"] == "turn_end" for x in session.events) > ends)


def test_bad_requests_and_unknown_sessions(client):
    assert client.post("/api/v1/sessions/nope/messages", json={"text": "hi"}).status_code == 404
    session_id = client.post("/api/v1/sessions").json["id"]
    assert client.post(f"/api/v1/sessions/{session_id}/messages", json={"text": "  "}).status_code == 400
    assert client.post(f"/api/v1/sessions/{session_id}/tool_results", json={"id": 3}).status_code == 400


def test_idle_sessions_are_reaped():
    sessions = Sessions(lambda session: lambda: None)
    session = sessions.create()
    assert sessions.reap(now=session.touched + 10) == 0
    assert sessions.reap(now=session.touched + 31 * 60) == 1
    assert session.closed and sessions.count() == 0


def test_the_tui_files_are_served_for_pyodide(client):
    files = client.get("/tui/manifest.json").json["files"]
    assert "molterm_tui/app.py" in files and "molterm_tui/__init__.py" in files
    assert b"class App" in client.get("/tui/molterm_tui/app.py").data
    assert client.get("/tui/../server/app.py").status_code == 404


def test_smiles_detection():
    assert agent_module.looks_like_smiles("CC(=O)Oc1ccccc1C(=O)O")
    assert not agent_module.looks_like_smiles("show me a drug")
    assert not agent_module.looks_like_smiles("12345")
