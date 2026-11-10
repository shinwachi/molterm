"""The demo's tutor, for tutorial mode: a script that watches the session and explains what's going on, as a tutor would.

It reads the session's events (sdk/tuiagent/tutor.py) and, the first time something happens, says what it is and how to use it:
the agent's question, a server-side tool, a molecule shown, the agent's marks, what the user's edits and highlights send. It
suggests things to try in the input, for Tab to take, such as /edit on the molecule just shown. A model could do the same with
the events as its context; only the deciding would differ.
"""
from tuiagent import Tutor

WELCOME = ("Tutorial mode. I'm the tutor: I watch this session, the agent's replies, its tools and what you do, and chime in "
           "here. The agent doesn't see me. The faint text in the input is a suggestion: Tab takes it, or type your own. "
           "/tutor hides me.")
LATE = ("Tutorial mode, from here on. I'm the tutor: I watch this session from now, the agent's replies, its tools and what you "
        "do, and chime in here. The agent doesn't see me. /tutor hides me.")


class EmulatedTutor(Tutor):
    def __init__(self, session, pace=1.0):
        super().__init__(session, pace)
        self.tools = {}  # tool id -> name, for what the user's answers were to
        self.shown = 0  # the last molecule shown, by its number
        self.turns = 0

    def observe(self, event):
        kind = event["type"]
        if kind == "tool_use":
            self.tools[event.get("id")] = event.get("name", "")
            self.tool(event.get("name", ""), event.get("input") or {}, event)
        elif kind == "tool_answer":
            self.answer(self.tools.get(event.get("id"), ""), event.get("result") or {})
        elif kind == "user_message":
            self.message(event.get("text", ""))
        elif kind == "turn_end" and event.get("interrupted"):
            self.turns += 1
            if self.once("interrupted"):
                self.tip("Esc stopped the agent's turn: what it was doing ended there, and it waits for your next message. Esc "
                         "interrupts it whenever the spinner shows.")
        elif kind == "turn_end":
            self.turn_end()
        elif kind == "error" and not str(event.get("message", "")).startswith("The tutor") and self.once("error"):
            self.tip("That error came from the agent's side, shown as it came. The session carries on: try again, or something else.")

    def joined(self, history):
        """Asked for later: what was shown so far (the TUI numbers the molecules it keeps in the order they come), and a welcome."""
        self.shown = sum(1 for e in history if e["type"] == "tool_use" and e.get("name") == "tui.show" and (e.get("input") or {}).get("smiles"))
        self.turns = sum(1 for e in history if e["type"] == "turn_end")
        if self.once("welcome"):
            self.tip(LATE, wait=0)
        self.try_edit()

    def turn_end(self):
        self.turns += 1
        if self.turns == 1 and self.once("welcome"):
            self.tip(WELCOME)
        else:
            self.try_edit()

    def try_edit(self):
        if self.shown and self.once("try edit"):
            self.tip(f"Try editing the molecule: /edit {self.shown} opens molterm's editor on the picture, where it is (or click "
                     "it). Right-click an atom for a highlight or a note; Done puts your version in the message, for the agent.")
            self.suggest(f"/edit {self.shown}")

    def tool(self, name, input_, event):
        if name == "tui.ask" and self.once("ask"):
            self.tip("The agent is asking you with tui.ask, one of this interface's tools. The question waits under the transcript: "
                     "↑ ↓ and Enter answer it, Esc skips it. Your answer goes back to the agent as the tool's result.", wait=0.3)
        elif name.startswith("molterm.") and self.once("server tool"):
            self.tip(f"{name} is a server-side tool: the agent asked the molterm server for it, and the line under it is what came "
                     "back. The tui.* tools (Show, Ask, Mark) run here instead, in your browser, and the agent waits for their "
                     "answers.", about=event)
        elif name == "tui.mark" and input_.get("mol") and self.once("mark mol"):
            self.tip("tui.mark: the agent highlighted part of the molecule, in pink, its colour, and the same atoms are painted in "
                     "the SMILES under the picture. Its note is in the amber bubble: drag it aside, – folds it, × hides it. "
                     "/smiles hides the SMILES.", about=event)
        elif name == "tui.mark" and input_.get("text") and self.once("mark text"):
            self.tip("It can point at text too. Drag across any words yourself: they're highlighted, wait above the input, and a "
                     "click there puts them in your message, so the agent knows what you mean.")

    def answer(self, name, result):
        if name == "tui.show" and result.get("mol"):
            self.shown = result["mol"]
            if self.once("show"):
                self.tip(f"Shown with its SMILES, the picture is a molecule this interface keeps, mol {self.shown}. Its SMILES is "
                         "under it. Click it to edit or annotate it where it is.")
        elif name == "tui.ask" and result.get("skipped") and self.once("skipped"):
            self.tip("Esc skipped the question: the agent got no answer, and carries on without one.")

    def message(self, text):
        if "⟦highlighted:" in text and self.once("highlighted"):
            self.tip("Your highlight went to the agent as ⟦highlighted: \"…\"⟧ before your words: that's how it knows what you're "
                     "pointing at.")
        elif ", edited:" in text and self.once("edited"):
            self.tip("Your edit went to the agent as CXSMILES: the molecule, with your notes and highlights in its |…| extension, "
                     "which is where the agent reads them.")
