"""Agents of one's own: the TuiAgent base, a session reached over HTTP by an agent elsewhere, AGENT, and the examples."""
import threading
import time
from types import SimpleNamespace

import pytest
from werkzeug.serving import make_server

import app as server
from examples.claude_agent import ClaudeAgent
from examples.hello_agent import HelloAgent, swatch
from sessions import Session, Sessions
from tuiagent import RemoteSession


@pytest.fixture
def live(monkeypatch):
    """The app on a real port, as an agent elsewhere reaches it."""
    monkeypatch.setattr(server, "sessions", Sessions(lambda session: lambda: None))
    http = make_server("127.0.0.1", 0, server.app, threaded=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_port}"
    http.shutdown()


def wait_for(session, predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [e for e in list(session.events) if predicate(e)]
        if found:
            return found
        time.sleep(0.02)
    raise AssertionError(f"no such event in {[e['type'] for e in session.events]}")


def test_an_agent_elsewhere_talks_through_a_session_over_http(live):
    remote = RemoteSession.open(live, public="http://agent.example")
    assert remote.url == f"http://agent.example/?session={remote.id}"
    threading.Thread(target=HelloAgent(remote, pace=0), daemon=True).start()
    session = server.sessions.get(remote.id)  # the page's side of the same session
    wait_for(session, lambda e: e["type"] == "turn_end")
    assert "small example agent" in "".join(e.get("delta", "") for e in session.events)

    session.post({"text": "colour please", "cols": 60})
    ask = wait_for(session, lambda e: e["type"] == "tool_use" and e["name"] == "tui.ask")[0]
    assert ask["input"]["options"] == ["Coral", "Teal", "Gold", "Lavender"]
    session.put_result(ask["id"], {"choice": "Teal", "index": 1})
    show = wait_for(session, lambda e: e["type"] == "tool_use" and e["name"] == "tui.show")[0]
    assert show["input"]["body"] == "#008080" and show["input"]["image"]["cols"] == 24
    session.put_result(show["id"], {"shown": True})
    wait_for(session, lambda e: e["type"] == "text" and "fading" in e["delta"])

    session.post({"text": "two words", "cols": 60})
    wait_for(session, lambda e: e["type"] == "tool_result" and e.get("output") == "2 words")
    show = wait_for(session, lambda e: e["type"] == "tool_use" and e["name"] == "tui.show" and e["input"]["title"] == "Your message")[0]
    session.interrupt()  # Esc while the panel waits: the turn ends, and the next one starts clean
    wait_for(session, lambda e: e["type"] == "turn_end" and e["seq"] > show["seq"])
    session.post({"text": "hi", "cols": 60})
    wait_for(session, lambda e: e["type"] == "turn_start" and e["seq"] > show["seq"])
    assert session.interrupted is False


def test_the_agent_routes_want_the_sessions_token(live, monkeypatch):
    client = server.app.test_client()
    made = client.post("/api/v1/agents/sessions").json
    path = f"/api/v1/sessions/{made['id']}/agent"
    assert client.get(path).status_code == 403
    assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 403
    auth = {"Authorization": f"Bearer {made['token']}"}
    assert client.get(path, headers=auth).json == {"interrupted": False, "closed": False}
    assert client.post(f"{path}/events", json={"type": "session"}, headers=auth).status_code == 400  # only the agent's own events
    assert client.post(f"{path}/events", json={"type": "text", "delta": "hi"}, headers=auth).status_code == 200
    assert client.get(f"/api/v1/sessions/{made['id']}").json == {"id": made["id"], "agent": "remote", "tutor": False}
    own = client.post("/api/v1/sessions").json["id"]  # a session whose agent runs on the server has no token to guess
    assert client.get(f"/api/v1/sessions/{own}/agent", headers={"Authorization": "Bearer "}).status_code == 403
    assert client.get(f"/api/v1/sessions/{own}").json["agent"] == "server"


def test_agent_names_the_class_the_server_runs(monkeypatch):
    monkeypatch.setattr(server, "AGENT", "examples.hello_agent:HelloAgent")
    assert isinstance(server.agent_factory()(Session("x")), HelloAgent)
    monkeypatch.setattr(server, "AGENT", "")
    assert type(server.agent_factory()(Session("x"))).__name__ == "EmulatedAgent"


def test_a_turn_start_clears_an_interrupt():
    session = Session("x")
    session.interrupt()
    session.emit("turn_start")
    assert session.interrupted is False


def test_the_swatch_is_a_png():
    png = swatch((255, 0, 0), 4, 2)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png[12:16] == b"IHDR" and int.from_bytes(png[16:20], "big") == 4


# -- the Claude example, with a stand-in for the API client -----------------------------------------------------------------------------

def block(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


class FakeClaude:
    """Answers with the replies given, in order, and keeps what it was sent."""

    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.sent.append({**request, "messages": list(request["messages"])})
        return self.replies.pop(0)


def answer_tui(session, answers):
    """Play the page: answer TUI tools, in order, with `answers`."""
    def run():
        answered = set()
        deadline = time.time() + 10
        while answers and time.time() < deadline:
            for event in list(session.events):
                if event["type"] == "tool_use" and event["name"].startswith("tui.") and event["id"] not in answered:
                    answered.add(event["id"])
                    session.put_result(event["id"], answers.pop(0))
            time.sleep(0.01)
    threading.Thread(target=run, daemon=True).start()


def test_claude_uses_the_tui_as_tools():
    session = Session("x")
    client = FakeClaude(
        SimpleNamespace(stop_reason="tool_use", content=[
            block("text", text="Let me ask."),
            block("tool_use", id="c1", name="ask_user", input={"question": "Tea or coffee?", "options": ["Tea", "Coffee"]})]),
        SimpleNamespace(stop_reason="tool_use", content=[
            block("tool_use", id="c2", name="show_panel", input={"title": "Coffee", "body": "Strong."})]),
        SimpleNamespace(stop_reason="end_turn", content=[block("text", text="Enjoy.")]))
    agent = ClaudeAgent(session, client=client, pace=0)
    answer_tui(session, [{"choice": "Coffee", "index": 1}, {"shown": True, "lines": 1}])
    agent.turn(lambda: agent.respond("Help me choose", 60))

    uses = [(e["name"], e["input"]) for e in session.events if e["type"] == "tool_use"]
    assert uses == [("tui.ask", {"question": "Tea or coffee?", "options": ["Tea", "Coffee"]}),
                    ("tui.show", {"title": "Coffee", "body": "Strong."})]
    assert "".join(e.get("delta", "") for e in session.events) == "Let me ask.Enjoy."
    first = client.sent[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default" and {t["name"] for t in first["tools"]} == {"show_panel", "ask_user", "point_out"}
    results = client.sent[1]["messages"][-1]["content"]
    assert results == [{"type": "tool_result", "tool_use_id": "c1", "content": '{"choice": "Coffee", "index": 1}'}]


def test_claude_closes_tool_calls_an_interrupt_left_open():
    session = Session("x")
    client = FakeClaude(
        SimpleNamespace(stop_reason="tool_use", content=[
            block("tool_use", id="c1", name="ask_user", input={"question": "Sure?", "options": ["Yes", "No"]})]),
        SimpleNamespace(stop_reason="end_turn", content=[block("text", text="Fine.")]))
    agent = ClaudeAgent(session, client=client, pace=0)
    threading.Thread(target=lambda: (wait_for(session, lambda e: e["type"] == "tool_use"), session.interrupt()), daemon=True).start()
    agent.turn(lambda: agent.respond("Do it", 60))
    agent.turn(lambda: agent.respond("Never mind", 60))
    opened = client.sent[1]["messages"][-1]["content"]
    assert opened[0] == {"type": "tool_result", "tool_use_id": "c1", "content": "The user interrupted this.", "is_error": True}
    assert opened[1] == {"type": "text", "text": "Never mind"}


def test_an_agent_elsewhere_can_open_its_session_in_tutorial_mode_or_be_given_a_tutor_later(live, monkeypatch):
    started = []
    monkeypatch.setattr(server.sessions, "tutor_factory", lambda session: lambda: started.append(session.id))
    tutored = RemoteSession.open(live, tutor=True)  # the agent needs nothing for it: the server's tutor watches beside it
    session = server.sessions.get(tutored.id)
    assert session.tutor and session.events[0]["tutor"] is True
    plain = RemoteSession.open(live)
    assert not server.sessions.get(plain.id).tutor
    client = server.app.test_client()
    assert client.post(f"/api/v1/sessions/{plain.id}/tutor").json == {"tutor": True}  # the user asks later, from the page
    assert server.sessions.get(plain.id).tutor
    deadline = time.time() + 2
    while len(started) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert started == [tutored.id, plain.id]
    monkeypatch.setattr(server.sessions, "tutor_factory", None)
    other = RemoteSession.open(live)
    assert client.post(f"/api/v1/sessions/{other.id}/tutor").status_code == 404  # a server with no tutor says so
