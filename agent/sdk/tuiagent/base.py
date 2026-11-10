"""An agent that talks to the user through the molterm agent TUI: the base class every agent here builds on.

An agent is given a session and runs until it closes. The session is either the server's own (the agent runs in the server's process,
one thread per session) or a `RemoteSession` (the agent runs anywhere else, and reaches the session over HTTP); both have the same five
members, so an agent class runs unchanged in either place:

    emit(type, **data)            add an event to the transcript's stream: text, tool_use, tool_result, status, turn_start ...
    next_message(timeout)         the user's next message, {"text", "cols"}, or None after `timeout` seconds
    wait_result(tool_id, timeout) the TUI's answer to a tool call, or None if the turn was interrupted or nobody answered
    interrupted                   true once the user pressed Esc in this turn (reset by the next turn_start)
    closed                        true once the session has ended

Subclass `TuiAgent` and write `greet()` and `respond(text, cols)`. Inside them:

    self.say(text)                         stream a reply into the transcript
    self.show(title, body=, image=)        put a panel in the transcript (text with ANSI colours, and/or a PNG picture)
    self.ask(question, options)            ask the user to pick one; returns {"choice", "index"}, or {"choice": None, "skipped": True}
    self.mark(mol=, text=, ...)            point something out: highlight atoms of a shown molecule, or text in the transcript, with a
                                           note in a speech bubble if you like
    self.suggest(text)                     suggest what the user might type next: shown faintly in the empty input, Tab takes it
    self.use_server_tool(name, input, run) show a tool of your own running, and its result
    self.use_tui(name, input)              call a TUI tool by name, for tools beyond show and ask
"""
import base64
import itertools
import re
import time


class Interrupted(Exception):
    """The user interrupted the turn (Esc); `turn` ends it quietly."""


class TuiAgent:
    # the tools the TUI runs, as a model's tool list would describe them
    TUI_TOOLS = [
        {"name": "tui.show", "where": "client",
         "description": "Show a panel in the user's terminal: a title, and a picture (image: png, cols, rows) and/or text (body, ANSI "
                        "colours allowed). With smiles, the picture is that molecule, which the user can edit and annotate."},
        {"name": "tui.ask", "where": "client", "description": "Ask the user to pick one of some options; returns their choice."},
        {"name": "tui.mark", "where": "client",
         "description": "Point something out in the user's terminal. On a molecule shown with smiles (mol: its number, which tui.show "
                        "returned): highlight atoms (atoms: indices in its SMILES, from 0, or smarts: every match) and/or put a note in "
                        "a speech bubble with a leader to the first atom. Or highlight text in the transcript (text: where it last "
                        "appears), with a note in a bubble under it. highlight: yellow, green, blue or pink; clear: true removes "
                        "what was marked first."},
    ]

    def __init__(self, session, pace=1.0):
        self.session, self.pace = session, pace
        self.ids = itertools.count(1)

    # -- what a subclass writes -------------------------------------------------------------------------------------------------------

    def greet(self):
        """The first turn, before the user has typed anything."""

    def respond(self, text, cols):
        """One turn: answer the user's message. `cols` is the transcript's width, for drawing to fit."""
        raise NotImplementedError

    # -- the session's loop -----------------------------------------------------------------------------------------------------------

    def __call__(self):
        self.turn(self.greet)
        while not self.session.closed:
            message = self.session.next_message(timeout=25)
            if message:
                self.turn(lambda: self.respond(message["text"], message.get("cols", 72)))

    run = __call__

    def turn(self, work):
        self.session.emit("turn_start", label="Thinking")
        interrupted = False
        try:
            self.pause(0.6)
            work()
        except Interrupted:
            interrupted = True  # said on its turn_end, for whoever watches (a tutor): a turn cut short, not finished
        except Exception as error:  # noqa: BLE001 - the user sees what went wrong, and the session goes on
            self.session.emit("error", message=f"The agent stumbled: {error}")
        finally:
            self.session.emit("text_end")
            self.session.emit("turn_end", **({"interrupted": True} if interrupted else {}))

    def pause(self, seconds):
        """Wait, as a model takes time to think, and end the turn if the user interrupts."""
        deadline = time.time() + seconds * self.pace
        while time.time() < deadline:
            if self.session.interrupted:
                raise Interrupted
            time.sleep(min(0.05, max(deadline - time.time(), 0)))
        if self.session.interrupted:
            raise Interrupted

    # -- what the agent can do --------------------------------------------------------------------------------------------------------

    def say(self, text, stream=True):
        """Put text in the transcript: a word at a time, as a model streams, or all at once."""
        for word in (re.findall(r"\S+\s*|\s+", text) if stream else [text]):
            self.session.emit("text", delta=word)
            self.pause(0.03 if stream else 0)
        self.session.emit("text_end")

    def suggest(self, text):
        """Suggest the user's next message: the TUI shows it faintly in the input while it's empty (or begins it), and Tab takes it.
        It stays until the user sends a message or another suggestion comes; an empty `text` takes it back."""
        self.session.emit("suggest", text=str(text).strip()[:500])

    def use_server_tool(self, name, input_, run, label="Working", summary=str, errors=(Exception,)):
        """Show the user a tool of the agent's own running: `run()` does the work, and `summary(output)` is the line shown under it.
        Returns the output, or None if `run` raised one of `errors` (the user sees the error, and the agent carries on)."""
        tool_id = f"tool_{next(self.ids)}"
        self.session.emit("tool_use", id=tool_id, name=name, input=input_)
        self.session.emit("status", label=label)
        self.pause(0.3)
        try:
            output = run()
        except errors as error:
            self.session.emit("tool_result", id=tool_id, output=f"Failed: {error}", is_error=True)
            return None
        self.session.emit("tool_result", id=tool_id, output=str(summary(output))[:200])
        return output

    def use_tui(self, name, input_, label="Waiting for you"):
        """Call one of the TUI's tools and wait for the page to answer."""
        tool_id = f"tool_{next(self.ids)}"
        self.session.emit("tool_use", id=tool_id, name=name, input=input_)
        self.session.emit("status", label=label)
        result = self.session.wait_result(tool_id)
        if result is None:
            raise Interrupted
        self.session.emit("status", label="Thinking")
        return result

    def show(self, title, body=None, image=None, smiles=None):
        """A panel in the transcript. `body` is text (ANSI colours allowed); `image` is a picture, either {"png": base64, "cols",
        "rows"} or a PNG's bytes, with `cols` and `rows` given as `image=(png_bytes, cols, rows)`. With `smiles`, the picture is that
        molecule: the user can edit and annotate it where it is, and their edit comes back in their next message (see TUTORIAL.md)."""
        input_ = {"title": title}
        if smiles is not None:
            input_["smiles"] = smiles
        if body is not None:
            input_["body"] = body
        if isinstance(image, tuple):
            png, cols, rows = image
            image = {"png": base64.b64encode(png).decode("ascii"), "cols": cols, "rows": rows}
        if image is not None:
            input_["image"] = image
        return self.use_tui("tui.show", input_, "Showing")

    def ask(self, question, options):
        return self.use_tui("tui.ask", {"question": question, "options": list(options)})

    def mark(self, mol=None, text=None, atoms=None, smarts=None, highlight=None, note=None, clear=False):
        """Point something out: on molecule `mol` (the number tui.show returned), the `atoms` (indices in its SMILES) or what `smarts`
        matches, highlighted and/or with a `note` in a bubble; or `text` in the transcript, highlighted, with a `note` under it.
        Returns the TUI's answer: for a molecule {"marked": [atoms], "smiles": its CXSMILES with the annotations}."""
        input_ = {name: value for name, value in (("mol", mol), ("text", text), ("atoms", atoms), ("smarts", smarts),
                                                  ("highlight", highlight), ("note", note)) if value is not None}
        if clear:
            input_["clear"] = True
        return self.use_tui("tui.mark", input_, "Pointing it out")
