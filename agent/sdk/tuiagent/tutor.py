"""A tutor: it watches a session as it goes, whatever agent the session has, and chimes in with tips, for tutorial mode.

The tutor runs in the agent server's process, a thread beside the session's agent (sessions.py starts it for a session made with
"tutor": true). It reads the session's event log, the same events the page gets, with two more: what the user said
(`user_message`, {"text"}) and how they answered a TUI tool (`tool_answer`, {"id", "result"}). The agent doesn't see the tutor, nor
the tutor's tips; the user sees them in the transcript, in bubbles of their own, and can hide them (/tutor).

Subclass `Tutor` and write `observe(event)`. Inside it:

    self.tip(text)        a tip in the transcript, after a pause; while the agent's reply is still streaming, it waits for the end
    self.suggest(text)    suggest what the user might type next, as the tutor: shown faintly in the empty input, Tab takes it
    self.once(key)        true the first time it's asked with `key`, for a tip that should come once a session
"""
import time


class Tutor:
    def __init__(self, session, pace=1.0):
        self.session, self.pace = session, pace
        self.given = set()

    def observe(self, event):
        """One event of the session: {"type", ...} as the page gets it, or a user_message or tool_answer."""

    def joined(self, history):
        """Started on a session going already (the user asked for a tutor later): `history` is its events so far, without the
        user's messages and answers, which weren't kept for a tutor then. The tutor watches from now on."""

    def __call__(self):
        position = getattr(self.session, "tutor_from", 0)
        if position:
            self.joined(self.session.events[:position])
        while not self.session.closed:
            for event in self.session.wait_events(position, 25):
                position = event["seq"] + 1
                if event["type"] in ("tutor", "suggest") and event.get("by") == "tutor":
                    continue  # its own
                try:
                    self.observe(event)
                except Exception as error:  # noqa: BLE001 - a tutor's mistake shouldn't end the session
                    self.session.emit("error", message=f"The tutor stumbled: {error}")

    run = __call__

    def once(self, key):
        if key in self.given:
            return False
        self.given.add(key)
        return True

    def pause(self, seconds):
        deadline = time.time() + seconds * self.pace
        while time.time() < deadline and not self.session.closed:
            time.sleep(min(0.05, max(deadline - time.time(), 0)))

    def tip(self, text, wait=0.4):
        """A tip in the transcript, `wait` seconds on (as if the tutor read what happened), after the agent's reply if it's streaming."""
        self.pause(wait)
        self.session.interject("tutor", text=str(text).strip(), by="tutor")

    def suggest(self, text):
        self.session.interject("suggest", text=str(text).strip()[:500], by="tutor")
