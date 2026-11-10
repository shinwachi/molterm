"""A tutor: it watches a session as it goes, whatever agent the session has, and chimes in with tips, for tutorial mode.

The tutor runs in the agent server's process, a thread beside the session's agent (sessions.py starts it for a session made with
"tutor": true). It reads the session's event log, the same events the page gets, with two more: what the user said
(`user_message`, {"text"}) and how they answered a TUI tool (`tool_answer`, {"id", "result"}). The agent doesn't see the tutor, nor
the tutor's tips; the user sees them in the transcript, in bubbles of their own, and can hide them (/tutor).

Subclass `Tutor` and write `observe(event)`. Inside it:

    self.tip(text)               a tip in the transcript, after a pause; while the agent's reply is still streaming, it waits for the end
    self.tip(text, about=event)  the same, about a tool call (its tool_use event): it waits for the call's result or the user's
                                 answer, so it comes after what it describes
    self.suggest(text)           suggest what the user might type next, as the tutor: shown faintly in the empty input, Tab takes it
    self.once(key)               true the first time it's asked with `key`, for a tip that should come once a session
    self.name                    the tutor's name, if the session named it (TUTORS), else True
    self.context                 the session's context: a JSON object whoever made the session gave it, as it was given

EVENTS below lists every event a tutor can see, and says which exist only in tutorial mode, which never reach observe, and which a
late tutor's history lacks.
"""
import time

# Every event in a session's log, as a tutor sees it: the page gets all but TUTORIAL_ONLY (and ignores what it has no use for).
EVENTS = {
    "session": "{id, tutor?}: the session began; again, with tutor (true or its name), when a tutor was started on it later",
    "turn_start": "{label}: the agent's turn began",
    "status": "{label}: what the agent is doing, shown by the spinner",
    "text": "{delta}: words of the agent's reply, as they stream",
    "text_end": "{}: the reply so far is complete",
    "tool_use": "{id, name, input}: the agent called a tool, its own (molterm.*) or the TUI's (tui.*)",
    "tool_result": "{id, output, is_error?}: an agent's own tool's result (a TUI tool's comes back as tool_answer)",
    "error": "{message}: what went wrong, shown to the user",
    "turn_end": "{interrupted?}: the agent's turn ended; interrupted: true if Esc cut it short",
    "interrupt": "{}: the user pressed Esc to stop the agent's turn; its turn_end follows, with interrupted: true",
    "suggest": "{text, by?}: a suggestion for the user's next message, the agent's, or the tutor's (by: tutor); empty takes it back",
    "tutor": "{text, by: tutor}: a tutor's tip",
    "user_message": "{text}: what the user sent, as the agent gets it, contexts in full",
    "tool_answer": "{id, result}: the user's answer to a TUI tool (tui.show, tui.ask, tui.mark), as the agent gets it",
}
TUTORIAL_ONLY = {"user_message", "tool_answer"}  # logged only while the session has a tutor; not sent to the page
NOT_OBSERVED = {"tutor", "suggest"}  # with by: tutor, the tutor's own, never passed to observe (the agent's suggest is)
NOT_IN_HISTORY = TUTORIAL_ONLY  # what joined(history) lacks, for a tutor started later: they weren't kept before it came


class Tutor:
    def __init__(self, session, pace=1.0):
        self.session, self.pace = session, pace
        self.given = set()

    @property
    def name(self):
        return getattr(self.session, "tutor", True)

    @property
    def context(self):
        return getattr(self.session, "context", {})

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
                if event["type"] in NOT_OBSERVED and event.get("by") == "tutor":
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

    def tip(self, text, wait=0.4, about=None):
        """A tip in the transcript, `wait` seconds on (as if the tutor read what happened), after the agent's reply if it's streaming;
        `about` a tool call (its tool_use event), after the call's result or the user's answer too."""
        self.pause(wait)
        self.session.interject("tutor", about=about, text=str(text).strip(), by="tutor")

    def suggest(self, text):
        self.session.interject("suggest", text=str(text).strip()[:500], by="tutor")
