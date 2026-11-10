"""A session on a molterm agent server, for an agent that runs somewhere else: on your machine, in a notebook, on another server.

    session = RemoteSession.open("http://molterm-agent.localhost")
    print("Open", session.url)          # the page that shows this session's TUI
    MyAgent(session)()                  # runs until the session ends

The agent reaches the session over HTTP with a token only it holds; the user's page reads the same session's stream. The standard
library only, so it runs wherever Python 3.9+ does.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

POLL = 25  # seconds a long poll waits on the server, under the usual proxy timeouts


class SessionEnded(Exception):
    pass


class RemoteSession:
    def __init__(self, server, session_id, token, url=None, context=None):
        self.server, self.id, self.token = server.rstrip("/"), session_id, token
        self.context = dict(context or {})
        self.url = url or f"{self.server}/?session={session_id}"
        self.closed = False
        self._interrupted, self._checked = False, 0.0

    @classmethod
    def open(cls, server, public=None, tutor=False, context=None):
        """A new session for an agent of your own. `public` is the server's address as the user's browser reaches it, when this
        process reaches it by another (a container name, say). With `tutor`, in tutorial mode: the server's tutor (True) or one it
        knows by name watches it beside your agent, which needs nothing for it (the user can also start one later, with /tutor).
        `context`, a JSON object, travels with the session, for its tutor (and as `session.context` here)."""
        request = {**({"tutor": tutor} if tutor else {}), **({"context": context} if context is not None else {})}
        data = _request("POST", f"{server.rstrip('/')}/api/v1/agents/sessions", request)
        url = f"{public.rstrip('/')}/?session={data['id']}" if public else data["url"]
        return cls(server, data["id"], data["token"], url, data.get("context"))

    def _call(self, method, path, data=None, timeout=POLL + 10):
        try:
            return _request(method, f"{self.server}/api/v1/sessions/{self.id}/agent{path}", data, self.token, timeout)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                self.closed = True
                raise SessionEnded("The session has ended.") from None
            raise

    def _note(self, state):
        self._interrupted, self._checked = state.get("interrupted", False), time.time()
        self.closed = self.closed or state.get("closed", False)

    # -- the five members an agent uses (see base.py) ---------------------------------------------------------------------------------

    def emit(self, type_, **data):
        self._note(self._call("POST", "/events", {"type": type_, **data}))
        if type_ == "turn_start":
            self._interrupted = False

    def next_message(self, timeout):
        try:
            state = self._call("GET", f"/messages?timeout={min(timeout, POLL)}")
        except SessionEnded:
            return None
        self._note(state)
        return state.get("message")

    def wait_result(self, tool_id, timeout=10 * 60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._call("GET", f"/tool_results/{urllib.parse.quote(tool_id)}?timeout={min(POLL, max(int(deadline - time.time()), 1))}")
            self._note(state)
            if state.get("result") is not None:
                return state["result"]
            if self._interrupted or self.closed:
                return None
        return None

    @property
    def interrupted(self):
        if time.time() - self._checked > 0.25:  # asked often while the agent pauses, so it's checked at most four times a second
            self._note(self._call("GET", ""))
        return self._interrupted


def _request(method, url, data=None, token=None, timeout=30):
    headers = {"Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def run(agent_class, server, public=None, tutor=False, context=None, **options):
    """Open a session on `server` (in tutorial mode with `tutor`, True or a tutor's name; with `context`, see open), print its page's
    address, and run `agent_class(session, **options)` until it ends."""
    session = RemoteSession.open(server, public, tutor, context)
    print(f"Open {session.url} to talk to the agent. Ctrl+C here ends it.", flush=True)
    try:
        agent_class(session, **options)()
    except (SessionEnded, KeyboardInterrupt):
        pass
    print("Session over.")
