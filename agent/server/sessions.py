"""Sessions: one per browser tab, each with its own agent, an event log the page reads as a stream, and the TUI tools' answers.

The agent runs in this process (a thread per session), or elsewhere: a session made for an agent of its own has a token, with which
that agent reaches it over HTTP (app.py's /agent routes, and sdk/tuiagent/remote.py).

Kept in memory, in one process (gunicorn runs one worker with threads), so a restart ends every session.
"""
import secrets
import threading
import time
from dataclasses import dataclass, field

MAX_SESSIONS = 200
IDLE_SECONDS = 30 * 60
TOOL_TIMEOUT = 10 * 60  # how long the agent waits for the user to answer a TUI tool


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

    def emit(self, type_, **data):
        """Add an event to the log and wake whoever is waiting: the page's stream, or the agent."""
        with self.changed:
            if type_ == "turn_start":
                self.interrupted = False  # an interrupt ends the turn it came in, not the next one
            self.events.append({"seq": len(self.events), "type": type_, **data})
            self.changed.notify_all()

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
            self.changed.notify_all()

    def next_message(self, timeout):
        with self.changed:
            if not self.inbox and not self.closed:
                self.changed.wait(timeout)
            return self.inbox.pop(0) if self.inbox else None

    def interrupt(self):
        with self.changed:
            self.interrupted = True
            self.changed.notify_all()

    def close(self):
        with self.changed:
            self.closed = True
            self.changed.notify_all()


class Sessions:
    def __init__(self, agent_factory):
        self.agent_factory = agent_factory  # session -> a callable that runs the agent until the session closes
        self.lock = threading.Lock()
        self.sessions = {}

    def create(self, external=False):
        """A new session, its agent started in a thread; or, if `external`, one with a token, for an agent that runs elsewhere."""
        self.reap()
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                raise SessionError("busy", "Too many sessions are open; try again in a few minutes.", 503)
            session = Session(secrets.token_hex(8), token=secrets.token_urlsafe(24) if external else "")
            self.sessions[session.id] = session
        session.emit("session", id=session.id)
        if external:
            return session
        threading.Thread(target=self.agent_factory(session), name=f"agent-{session.id}", daemon=True).start()
        return session

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
