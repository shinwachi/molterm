"""Sessions: one per browser tab, each with its own agent, an event log the page reads as a stream, and the TUI tools' answers.

The agent runs in this process (a thread per session), or elsewhere: a session made for an agent of its own has a token, with which
that agent reaches it over HTTP (app.py's /agent routes, and sdk/tuiagent/remote.py).

A session in tutorial mode has a tutor as well, another thread in this process, that reads the same event log as the page, with the
user's messages and their answers to the TUI's tools in it too (`user_message`, `tool_answer`, which the page ignores), and puts
its tips in (`tutor`) and suggests what to type next (`suggest`). The agent sees none of it. A tip waits while the agent's reply is
streaming, so that it comes between replies, never inside one, and a tip about a tool waits for the tool's answer. Which tutor: the
server's own (tutor true), or one it knows by name (TUTORS). Every event a tutor can see is listed in sdk/tuiagent/tutor.py (EVENTS).

A session can carry a `context`, a JSON object given when it's made (or when its tutor is started), that this server never reads:
it travels with the session, for its agent and its tutor, such as which program a lesson is about.

Kept in memory, in one process (gunicorn runs one worker with threads), so a restart ends every session.
"""
import secrets
import threading
import time
from dataclasses import dataclass, field

MAX_SESSIONS = 200
IDLE_SECONDS = 30 * 60
TOOL_TIMEOUT = 10 * 60  # how long the agent waits for the user to answer a TUI tool
TIP_WAIT = 60  # how long a tutor's tip waits for the agent's reply to finish streaming, before it goes in after all
MAX_CONTEXT = 64 * 1024  # a session's context, as JSON


class SessionError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class Session:
    id: str
    created: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.time)
    events: list = field(default_factory=list)
    results: dict = field(default_factory=dict)  # tool id -> the TUI's answer
    interrupted: bool = False
    closed: bool = False
    inbox: list = field(default_factory=list)  # the user's messages, oldest first, for the agent's thread
    changed: threading.Condition = field(default_factory=threading.Condition)
    token: str = ""  # for a session whose agent runs elsewhere: what that agent sends to reach it
    tutor: object = False  # tutorial mode: a tutor watches the session and chimes in; True for the server's own, or the tutor's name
    streaming: bool = False  # the agent's reply is coming in, text between its first word and text_end
    tutor_from: int = 0  # the event its tutor started at: past 0 if it was started later, on a session going already
    context: dict = field(default_factory=dict)  # opaque: what whoever made the session gave it, for its agent and tutor
    answered: set = field(default_factory=set)  # the tool calls that have their result or answer, for a tip about one
    turn_ends: int = -1  # the last turn_end's place in the log

    def emit(self, type_, **data):
        """Add an event to the log and wake whoever is waiting: the page's stream, the agent, or the tutor."""
        with self.changed:
            if type_ == "turn_start":
                self.interrupted = False  # an interrupt ends the turn it came in, not the next one
            if type_ == "text":
                self.streaming = True
            elif type_ in ("text_end", "turn_end", "tool_use", "error"):
                self.streaming = False
            if type_ == "tool_result":
                self.answered.add(data.get("id"))
            if type_ == "turn_end":
                self.turn_ends = len(self.events)
            self._append(type_, data)

    def _append(self, type_, data):
        self.events.append({"seq": len(self.events), "type": type_, **data})
        self.changed.notify_all()

    def _settled(self, about):
        """Whether what a tip is `about` is over: a tool call once it has its result (tool_result) or the user's answer (a TUI tool's),
        or its turn has ended without one; anything else at once."""
        if not about or about.get("type") != "tool_use":
            return True
        return about.get("id") in self.answered or self.turn_ends > about.get("seq", -1)

    def interject(self, type_, timeout=TIP_WAIT, about=None, **data):
        """The tutor's event (`tutor`, a tip; `suggest`, what to type next), once the agent's reply has finished streaming, so that a
        tip never lands inside one, and, if it's `about` a tool call (its tool_use event), once that has its answer, so the tip comes
        after what it describes; after `timeout` seconds it goes in anyway."""
        deadline = time.time() + timeout
        with self.changed:
            while (self.streaming or not self._settled(about)) and not self.closed and time.time() < deadline:
                self.changed.wait(min(deadline - time.time(), 1))
            if not self.closed:
                self._append(type_, data)

    def wait_events(self, after, timeout):
        """Events after index `after`, waiting up to `timeout` seconds for one."""
        with self.changed:
            if len(self.events) <= after and not self.closed:
                self.changed.wait(timeout)
            return self.events[after:]

    def put_result(self, tool_id, result):
        with self.changed:
            self.results[tool_id] = result
            self.touched = time.time()
            self.answered.add(tool_id)
            if self.tutor:  # for the tutor to see what the user answered
                self._append("tool_answer", {"id": tool_id, "result": result})
            self.changed.notify_all()

    def wait_result(self, tool_id, timeout=TOOL_TIMEOUT):
        """The TUI's answer to a tool, or None if it doesn't come, or the turn is interrupted."""
        deadline = time.time() + timeout
        with self.changed:
            while tool_id not in self.results and not self.interrupted and not self.closed:
                left = deadline - time.time()
                if left <= 0:
                    return None
                self.changed.wait(left)
            return self.results.pop(tool_id, None)

    def post(self, message):
        with self.changed:
            self.inbox.append(message)
            self.touched = time.time()
            if self.tutor:  # for the tutor to see what the user said
                self._append("user_message", {"text": message["text"]})
            self.changed.notify_all()

    def next_message(self, timeout):
        with self.changed:
            if not self.inbox and not self.closed:
                self.changed.wait(timeout)
            return self.inbox.pop(0) if self.inbox else None

    def interrupt(self):
        """Esc: the turn ends (the agent's next step raises Interrupted), and the log says when (`interrupt`); its turn_end then has
        interrupted: true."""
        with self.changed:
            self.interrupted = True
            self._append("interrupt", {})

    def close(self):
        with self.changed:
            self.closed = True
            self.changed.notify_all()


class Sessions:
    def __init__(self, agent_factory, tutor_factory=None, tutors=None):
        self.agent_factory = agent_factory  # session -> a callable that runs the agent until the session closes
        self.tutor_factory = tutor_factory  # session -> a callable that runs its tutor, for a session in tutorial mode
        self.tutors = dict(tutors or {})  # a tutor's name -> its factory, for a session that names its tutor (a lesson)
        self.lock = threading.Lock()
        self.sessions = {}

    def tutor_for(self, tutor):
        """The factory for `tutor` as a request gives it: True, the server's own (None if it has none); a name, that tutor, refused if
        the server doesn't know it; anything else, no tutor."""
        if tutor is True:
            return self.tutor_factory
        if isinstance(tutor, str) and tutor:
            if tutor not in self.tutors:
                known = ", ".join(sorted(self.tutors)) or "none"
                raise SessionError("no_tutor", f"No tutor is called {tutor!r} here (known: {known}).", 400)
            return self.tutors[tutor]
        return None

    def create(self, external=False, tutor=False, context=None):
        """A new session, its agent started in a thread; or, if `external`, one with a token, for an agent that runs elsewhere. With
        `tutor` (True, or a tutor's name), in tutorial mode: its tutor starts in a thread of its own, whichever agent it has. `context`
        is kept on the session as it is."""
        self.reap()
        factory = self.tutor_for(tutor)  # an unknown name is refused before anything is made
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                raise SessionError("busy", "Too many sessions are open; try again in a few minutes.", 503)
            session = Session(secrets.token_hex(8), token=secrets.token_urlsafe(24) if external else "",
                              tutor=tutor if factory else False, context=dict(context or {}))
            self.sessions[session.id] = session
        session.emit("session", id=session.id, **({"tutor": session.tutor} if factory else {}))
        if factory:
            self._run_tutor(session, factory)
        if external:
            return session
        threading.Thread(target=self.agent_factory(session), name=f"agent-{session.id}", daemon=True).start()
        return session

    def start_tutor(self, session, tutor=True, context=None):
        """Tutorial mode for a session going already, whatever its agent, here or elsewhere: its tutor (True, the server's own, or
        one by name) starts from now on, and the page is told (a `session` event again, with "tutor"). `context`, if given, replaces
        the session's. Whether it has a tutor now: false if the server has none to give; a session has one tutor at most."""
        factory = self.tutor_for(tutor)
        if not factory:
            return False
        with self.lock:
            if session.tutor:
                return True
            session.tutor = tutor
            if context is not None:
                session.context = dict(context)
        session.emit("session", id=session.id, tutor=tutor)
        with session.changed:
            session.tutor_from = len(session.events)
        self._run_tutor(session, factory)
        return True

    def _run_tutor(self, session, factory):
        threading.Thread(target=factory(session), name=f"tutor-{session.id}", daemon=True).start()

    def get(self, session_id):
        with self.lock:
            session = self.sessions.get(session_id)
        if not session or session.closed:
            raise SessionError("no_session", "No such session: it may have ended. Reload the page for a new one.", 404)
        session.touched = time.time()
        return session

    def reap(self, now=None):
        """End sessions nobody has used for IDLE_SECONDS."""
        now = now or time.time()
        with self.lock:
            stale = [s for s in self.sessions.values() if now - s.touched > IDLE_SECONDS]
            for session in stale:
                del self.sessions[session.id]
        for session in stale:
            session.close()
        return len(stale)

    def count(self):
        with self.lock:
            return len(self.sessions)
