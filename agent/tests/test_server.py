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

    def picture(self, smiles, cols):
        if smiles == "C1CC":
            raise ValueError("Not valid SMILES")
        return {"png": "iVBORw0KGgo=", "cols": 7, "rows": 2}

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
    render = next(e for e in events if e["type"] == "tool_use" and e["name"] == "molterm.render")
    assert render["input"]["mode"] == "kitty"  # a picture by default
    shown = next(e for e in events if e["type"] == "tool_use" and e["name"] == "tui.show")
    assert shown["input"]["image"] == {"png": "iVBORw0KGgo=", "cols": 7, "rows": 2}


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
    types = [e["type"] for e in session.events]
    assert "interrupt" in types[types.index("tool_use"):]  # when: in the log, for a tutor
    assert session.events[-1] == {"seq": len(types) - 1, "type": "turn_end", "interrupted": True}  # and what it did to the turn
    assert all("interrupted" not in e for e in session.events[:-1] if e["type"] == "turn_end")  # a finished turn says nothing


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


def test_the_page_gives_the_keyboard_back_from_a_click_anywhere(client):
    """A click on the top bar, on the note, or in the frame's margin takes the keys off the terminal, and the TUI cannot say so: the
    terminal's own cue, a hollow cursor, is hidden exactly then, because a TUI waiting for an answer hides the cursor. So the page
    listens for a click anywhere, not only on the terminal. That's this page's choice, made by name, since it would take the keyboard
    from a host's own text boxes: bridge.js's focusPolicy keeps to the terminal's box unless asked for the page."""
    page = client.get("/static/page.js").get_data(as_text=True)
    assert 'focusPolicy("page", { box, focus: () => terminal.focus?.() })' in page
    module = client.get("/static/bridge.js").get_data(as_text=True)
    assert 'export function focusPolicy(policy = "terminal"' in module
    assert 'const target = policy === "page" ? document : box;' in module


def test_smiles_detection():
    assert agent_module.looks_like_smiles("CC(=O)Oc1ccccc1C(=O)O")
    assert not agent_module.looks_like_smiles("show me a drug")
    assert not agent_module.looks_like_smiles("12345")


def test_an_edited_molecule_is_reviewed(client):
    session_id = client.post("/api/v1/sessions").json["id"]
    session = server.sessions.get(session_id)
    wait_for(session, lambda e: e["type"] == "turn_end")
    answer_tools(client, session, lambda _: {"index": 0})
    client.post(f"/api/v1/sessions/{session_id}/messages",
                json={"text": "⟦mol 1 Ethanol, edited: CCO |atomProp:2.atomNote.hydroxyl|⟧ what now?", "cols": 60})
    events = wait_for(session, lambda e: e["type"] == "tool_use" and e["input"].get("title") == "Properties of Ethanol, edited")
    text = "".join(e.get("delta", "") for e in events if e["type"] == "text")
    assert 'You edited Ethanol. Your notes: "hydroxyl".' in text


def test_a_drawing_is_shown_with_its_smiles_so_the_user_can_edit_it(client):
    session_id = client.post("/api/v1/sessions").json["id"]
    session = server.sessions.get(session_id)
    wait_for(session, lambda e: e["type"] == "turn_end")
    answer_tools(client, session, lambda _: {"index": 1})
    client.post(f"/api/v1/sessions/{session_id}/messages", json={"text": "CCO", "cols": 60})
    events = wait_for(session, lambda e: e["type"] == "tool_use" and e["name"] == "tui.show")
    assert next(e for e in events if e["type"] == "tool_use" and e["name"] == "tui.show")["input"]["smiles"] == "CCO"


def test_the_editor_and_pictures_go_through_the_session(client, monkeypatch):
    sent = []

    def edit(fields):
        sent.append(fields)
        return 200, "0 STATE\n\x1b[2;1Hdrawn"

    monkeypatch.setattr(server.molterm, "edit", edit)
    asked = []
    monkeypatch.setattr(server.molterm, "callouts", lambda smiles, cols, mark, clear: asked.append((mark, clear))
                        or ((200, {"png": "x", "cols": cols, "rows": 3}) if smiles != "bad" else (400, {"error": {"message": "nope"}})))
    session_id = client.post("/api/v1/sessions").json["id"]
    response = client.post(f"/api/v1/sessions/{session_id}/edit", json={"smiles": "CCO", "cols": 100, "rows": 40, "box": "3,0,16,99"})
    assert response.status_code == 200 and response.text == "0 STATE\n\x1b[2;1Hdrawn" and sent[0]["smiles"] == "CCO"
    assert client.post(f"/api/v1/sessions/{session_id}/picture", json={"smiles": "CCO", "cols": 50}).json["cols"] == 50
    marked = client.post(f"/api/v1/sessions/{session_id}/picture", json={"smiles": "CCO", "cols": 50, "mark": {"atoms": [2]}, "clear": True})
    assert marked.status_code == 200 and asked[-1] == ({"atoms": [2]}, True)
    failed = client.post(f"/api/v1/sessions/{session_id}/picture", json={"smiles": "bad", "cols": 50})
    assert failed.status_code == 400 and failed.json["error"]["message"] == "nope"
    assert client.post("/api/v1/sessions/nope/edit", json={}).status_code == 404
    assert client.post(f"/api/v1/sessions/{session_id}/picture", json={"cols": 50}).status_code == 400


def test_the_editor_asks_molterm_for_a_compact_box_in_the_pages_colours(monkeypatch):
    posted = {}

    class Answer:
        status_code, text = 200, "0 S\n"

    monkeypatch.setattr(agent_module.requests, "post", lambda url, data, timeout: posted.update(url=url, data=data) or Answer())
    agent_module.Molterm("http://molterm").edit({"state": "S", "events": "1b", "cols": 100, "evil": "x"})
    assert posted["url"] == "http://molterm/api/v1/edit"
    assert posted["data"] == {"state": "S", "events": "1b", "cols": "100", **agent_module.TERMINAL, "compact": "1"}


def test_a_tutors_tip_waits_for_the_agents_reply_to_finish():
    session = Session("s1", tutor=True)
    session.emit("text", delta="Hello ")
    done = threading.Event()
    threading.Thread(target=lambda: (session.interject("tutor", text="a tip", by="tutor"), done.set()), daemon=True).start()
    time.sleep(0.1)
    assert not done.is_set() and [e["type"] for e in session.events] == ["text"]  # not inside the reply
    session.emit("text", delta="there")
    session.emit("text_end")
    assert done.wait(2)
    assert [e["type"] for e in session.events] == ["text", "text", "text_end", "tutor"]


def test_the_tutor_sees_the_users_messages_and_answers_only_in_tutorial_mode():
    plain, tutored = Session("a"), Session("b", tutor=True)
    for session in (plain, tutored):
        session.post({"text": "hi", "cols": 72})
        session.put_result("tool_1", {"index": 0})
    assert [e["type"] for e in plain.events] == []
    assert tutored.events == [{"seq": 0, "type": "user_message", "text": "hi"},
                              {"seq": 1, "type": "tool_answer", "id": "tool_1", "result": {"index": 0}}]


def test_a_tutorial_session_has_a_tutor_who_explains_and_suggests(monkeypatch):
    import tutor as tutor_module
    sessions = Sessions(lambda session: agent_module.EmulatedAgent(session, FakeMolterm(), pace=0),
                        lambda session: tutor_module.EmulatedTutor(session, pace=0))
    monkeypatch.setattr(server, "sessions", sessions)
    client = server.app.test_client()
    assert client.post("/api/v1/sessions").status_code == 201
    plain = list(sessions.sessions.values())[-1]
    assert plain.tutor is False and "tutor" not in plain.events[0]
    created = client.post("/api/v1/sessions", json={"tutor": True})
    session = sessions.sessions[created.get_json()["id"]]
    assert session.events[0] == {"seq": 0, "type": "session", "id": session.id, "tutor": True}
    assert client.get(f"/api/v1/sessions/{session.id}").get_json()["tutor"] is True
    events = wait_for(session, lambda e: e["type"] == "tutor")
    assert {"type": "suggest", "text": "show me a drug"}.items() <= next(e for e in events if e["type"] == "suggest").items()
    assert "Tutorial mode" in next(e for e in events if e["type"] == "tutor")["text"]

    def play():  # the page: Aspirin, shown as mol 1, marked, and no properties
        answered, deadline = set(), time.time() + 5
        while time.time() < deadline and not session.closed:
            for event in list(session.events):
                if event["type"] == "tool_use" and event["name"].startswith("tui.") and event["id"] not in answered:
                    answered.add(event["id"])
                    name, input_ = event["name"], event["input"]
                    result = ({"shown": True, "mol": 1} if name == "tui.show" else {"marked": [1]} if name == "tui.mark"
                              else {"choice": "Aspirin", "index": 0} if "drug" in input_["question"] else {"choice": None, "skipped": True})
                    client.post(f"/api/v1/sessions/{session.id}/tool_results", json={"id": event["id"], "result": result})
            time.sleep(0.01)

    threading.Thread(target=play, daemon=True).start()
    client.post(f"/api/v1/sessions/{session.id}/messages", json={"text": "show me a drug"})
    events = wait_for(session, lambda e: e["type"] == "suggest" and e.get("text") == "/edit 1")
    tips = [e["text"] for e in events if e["type"] == "tutor"]
    for words in ("tui.ask", "server-side tool", "mol 1", "in pink", "point at text", "Esc skipped", "/edit 1 opens"):
        assert any(words in tip for tip in tips), words
    assert all(e.get("by") == "tutor" for e in events if e["type"] == "tutor")
    texts = [i for i, e in enumerate(events) if e["type"] in ("text", "text_end")]
    for i, e in enumerate(events):  # never inside one of the agent's replies
        if e["type"] == "tutor":
            before = [events[j]["type"] for j in texts if j < i]
            assert not before or before[-1] == "text_end"


def test_a_remote_agent_can_suggest(client):
    created = client.post("/api/v1/agents/sessions").get_json()
    sent = client.post(f"/api/v1/sessions/{created['id']}/agent/events", json={"type": "suggest", "text": "show me a drug"},
                       headers={"Authorization": f"Bearer {created['token']}"})
    assert sent.status_code == 200


def test_a_tutor_started_later_watches_from_then_on():
    import tutor as tutor_module
    sessions = Sessions(lambda session: lambda: None, lambda session: tutor_module.EmulatedTutor(session, pace=0))
    session = sessions.create()
    session.emit("turn_start")
    session.emit("tool_use", id="tool_1", name="tui.ask", input={"question": "Which?", "options": ["A"]})
    session.emit("tool_use", id="tool_2", name="tui.show", input={"title": "Aspirin", "smiles": "CC(=O)Oc1ccccc1C(=O)O"})
    session.emit("turn_end")
    begun = len(session.events)
    assert sessions.start_tutor(session) and sessions.start_tutor(session)  # once: asked again, it has one
    events = wait_for(session, lambda e: e["type"] == "suggest")
    assert events[begun] == {"seq": begun, "type": "session", "id": session.id, "tutor": True}
    tips = [e["text"] for e in events if e["type"] == "tutor"]
    assert tips[0].startswith("Tutorial mode, from here on") and "/edit 1" in tips[1]  # what was shown before counts
    assert not any("tui.ask" in tip for tip in tips)  # the past isn't explained
    assert [e for e in events if e["type"] == "suggest"][0]["text"] == "/edit 1"
    session.emit("tool_use", id="tool_3", name="tui.ask", input={"question": "Which?", "options": ["A"]})
    events = wait_for(session, lambda e: e["type"] == "tutor" and "tui.ask" in e["text"])  # from now on, it is
    assert sum(1 for e in events if e["type"] == "session") == 2


def test_a_session_can_name_its_tutor_and_an_unknown_name_is_refused(monkeypatch):
    made = []

    def lesson(name):
        return lambda session: lambda: made.append((name, session.id))

    sessions = Sessions(lambda session: lambda: None, lesson("own"), {"lesson-1": lesson("lesson-1"), "lesson-2": lesson("lesson-2")})
    monkeypatch.setattr(server, "sessions", sessions)
    client = server.app.test_client()
    assert client.get("/api/v1/tutors").json == {"default": True, "named": ["lesson-1", "lesson-2"]}
    named = client.post("/api/v1/sessions", json={"tutor": "lesson-2"}).json["id"]
    own = client.post("/api/v1/sessions", json={"tutor": True}).json["id"]
    assert sessions.get(named).events[0]["tutor"] == "lesson-2" and sessions.get(own).events[0]["tutor"] is True
    refused = client.post("/api/v1/sessions", json={"tutor": "lesson-9"})
    assert refused.status_code == 400 and "lesson-1, lesson-2" in refused.json["error"]["message"]
    assert sessions.count() == 2  # refused where the session is made: nothing was
    assert client.post("/api/v1/sessions", json={"tutor": 3}).status_code == 400
    assert client.post("/api/v1/agents/sessions", json={"tutor": "nope"}).status_code == 400
    later = client.post("/api/v1/sessions").json["id"]
    assert client.post(f"/api/v1/sessions/{later}/tutor", json={"tutor": "lesson-9"}).status_code == 400
    assert client.post(f"/api/v1/sessions/{later}/tutor", json={"tutor": "lesson-1"}).json == {"tutor": "lesson-1"}
    deadline = time.time() + 2
    while len(made) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert sorted(made) == sorted([("lesson-2", named), ("own", own), ("lesson-1", later)])
    assert set(server.named_tutors("a=tutor:EmulatedTutor, b-2=tutor:EmulatedTutor")) == {"a", "b-2"}
    with pytest.raises(ValueError):
        server.named_tutors("no colon here")


def test_a_session_carries_its_context_for_its_agent_and_tutor(monkeypatch):
    from tuiagent import Tutor
    sessions = Sessions(lambda session: lambda: None, lambda session: lambda: None, {"lesson-1": lambda session: lambda: None})
    monkeypatch.setattr(server, "sessions", sessions)
    client = server.app.test_client()
    context = {"program": "malksketch", "library": ["a.sdf"]}
    made = client.post("/api/v1/sessions", json={"tutor": "lesson-1", "context": context}).json["id"]
    session = sessions.get(made)
    assert session.context == context and "context" not in session.events[0]  # not in the page's stream
    tutor = Tutor(session)
    assert tutor.context == context and tutor.name == "lesson-1"
    remote = client.post("/api/v1/agents/sessions", json={"context": context}).json
    assert remote["context"] == context and remote["tutor"] is False
    later = client.post("/api/v1/sessions").json["id"]
    client.post(f"/api/v1/sessions/{later}/tutor", json={"context": {"lesson": 10}})
    assert sessions.get(later).context == {"lesson": 10} and sessions.get(later).tutor is True
    assert client.post("/api/v1/sessions", json={"context": [1]}).status_code == 400
    assert client.post("/api/v1/sessions", json={"context": {"x": "y" * 70_000}}).status_code == 400


def test_a_tip_about_a_tool_comes_after_its_answer():
    session = Session("s1", tutor=True)
    session.emit("tool_use", id="tool_1", name="molterm.render", input={})
    session.emit("tool_use", id="tool_2", name="tui.ask", input={})
    about_server, about_tui = session.events[0], session.events[1]
    done = []
    for about in (about_server, about_tui):
        threading.Thread(target=lambda a=about: (session.interject("tutor", about=a, text=a["id"], by="tutor"), done.append(a["id"])),
                         daemon=True).start()
    time.sleep(0.1)
    assert done == []  # streaming is over (tool_use), but neither has its answer
    session.emit("tool_result", id="tool_1", output="drawn")
    deadline = time.time() + 2
    while not done and time.time() < deadline:
        time.sleep(0.01)
    assert done == ["tool_1"] and [e["type"] for e in session.events] == ["tool_use", "tool_use", "tool_result", "tutor"]
    session.put_result("tool_2", {"index": 0})  # a TUI tool's answer, from the page
    while len(done) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert [e["type"] for e in session.events][-2:] == ["tool_answer", "tutor"]
    session.emit("tool_use", id="tool_3", name="tui.ask", input={})
    third = session.events[-1]
    session.emit("turn_end", interrupted=True)  # its turn ended with no answer: the tip needn't wait for one
    session.interject("tutor", about=third, timeout=1, text="after all", by="tutor")
    assert session.events[-1]["text"] == "after all"


def test_every_event_a_tutor_can_see_is_listed_beside_tutor():
    import inspect
    import re as regex

    import sessions as sessions_module
    import tuiagent
    from molterm_tui import app as tui_app
    from tuiagent import base, tutor as tutor_sdk
    listed = set(tuiagent.EVENTS)
    assert server.AGENT_EVENTS | {"session", "interrupt", "tutor", "user_message", "tool_answer"} == listed
    emitted = set()
    for module in (sessions_module, base, tutor_sdk, agent_module):
        source = inspect.getsource(module)
        emitted |= set(regex.findall(r"(?:emit|_append|interject)\(\"(\w+)\"", source))
    assert emitted <= listed and emitted >= {"session", "interrupt", "tutor", "suggest", "user_message", "tool_answer", "turn_end"}
    handled = {name[4:] for name in dir(tui_app.App) if name.startswith("_on_")}
    assert handled <= listed - tuiagent.TUTORIAL_ONLY  # what the page draws is among what a tutor sees
    assert tuiagent.TUTORIAL_ONLY <= listed and tuiagent.NOT_OBSERVED <= listed and tuiagent.NOT_IN_HISTORY <= listed
