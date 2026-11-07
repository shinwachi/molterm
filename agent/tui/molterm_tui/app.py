"""A Claude Code-like terminal interface for a server-side agent, written to run in Pyodide in the browser (or any Python).

The page hands this app the terminal's keys (`key`) and the agent's events from the server (`event`), and gives it a `bridge` to talk
back through: `write(text)` prints to the terminal, `post_message(text, cols)` sends what the user typed, `post_tool_result(id, json)`
answers one of the agent's TUI tools, and `interrupt()` stops the agent's turn. The app redraws the whole screen after each change.

Molecules: a picture shown with a `smiles` is a molecule the app keeps (`molecules`), numbered from 1, and it knows where each is on the
screen (`placed`). Clicking one, or /edit N, edits it where it is, with molterm's editor running on the molterm server through
`bridge.edit(fields as JSON)`, whose answer comes back to `edit_answer(text)` (or `edit_failed(message)`). Inline (the default), the
drawing stays where the picture was, and the editor's tools float over the text beside it, put away and back with F2; in a box
(/edit N box, or /editor box), a compact editor opens in a box over the picture. Done puts the edited molecule, as CXSMILES with its
annotations, into the input for the user's next message.

Pointing things out: a molecule's annotations (from the editor, or the agent's tui.mark) are highlights in its picture, and notes in
speech bubbles beside it, each with a leader to its atom. The picture comes from molterm (/api/v1/callouts) through
`bridge.picture(request as JSON)`, answered by `picture_answer(request, text)` or `picture_failed(request, message)`. Text in the
transcript can be highlighted too, by the agent (tui.mark with `text`) or by the user, dragging across it, which also puts what they
marked into their message.

The agent can use the interface as tools:
  tui.show {title, image, body,    puts a panel in the transcript: a picture (`image`: {png (base64), cols, rows}), drawn with the
            smiles}                Kitty graphics protocol, and/or text (`body`, which may carry ANSI colours, such as a Braille drawing);
                                   with `smiles`, the picture is that molecule, which the user can edit and annotate; the result
                                   gives its number, `mol`
  tui.ask  {question, options}     asks the user to pick one; the answer goes back to the agent as the tool's result
  tui.mark {mol, atoms or smarts,  points something out: on molecule `mol`, highlights atoms (by index in its SMILES, or those a SMARTS
            highlight, note, clear} matches) and/or puts a note in a bubble on the first; or, with `text`, highlights that text where it
  tui.mark {text, highlight, note, last appears in the transcript, with the note in a bubble under it. `clear` removes what was marked
            clear}                 first. Highlights: yellow, green, blue, pink
"""
import itertools
import json
import re
import time
from dataclasses import dataclass, field

from . import keys as keyboard
from . import kitty
from .screen import (ACCENT, BOLD, DIM, GREEN, GREY, ITALIC, RED, RESET, USER_BG, cells, overlay, pad, paint, strip, style, truncate,
                     width, wrap)

SPINNER = "·✢✳✶✻✽✻✶✳✢"
TOOL_NAMES = {"tui.show": "Show", "tui.ask": "Ask", "tui.mark": "Mark", "molterm.render": "Render", "molterm.properties": "Properties"}
INPUT_LINES_MAX = 6
EDIT_ROWS_MIN = 14  # a box editor: its border, the strip of tools, the drawing and two lines of status
EDIT_ROWS = 22  # what it gets when there is room: enough for an atom's menu to open inside it
EDIT_COLS_MIN = 60
INLINE_ROWS_MIN, INLINE_COLS_MIN = 8, 40  # an inline editor's drawing, at the least, around the picture
STRIP_COLS = 56  # its floating strip of tools, at the least (molterm's editor.STRIP_COLS)
EDIT_INPUT = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[1;3([ABCD])")  # what the TUI keeps for itself while editing inline
HOST_NOTICE = re.compile(r"\x1b\]5523;([a-z]+)(?:=([0-9]))?\x1b\\")  # what the inline editor tells its host (molterm's HOST_REPAINT)
HIGHLIGHTS = {"yellow": "\x1b[48;2;94;78;28m", "green": "\x1b[48;2;38;84;44m", "blue": "\x1b[48;2;32;62;104m", "pink": "\x1b[48;2;100;42;70m"}
SELECTING = "\x1b[48;2;70;70;84m"
NOTE_INK, NOTE_BG = "\x1b[38;2;229;192;123m", "\x1b[48;2;44;39;30m"  # bubbles, in the colour of molterm's leaders (callouts.LEADER)
BUBBLE_TEXT = 30  # a bubble's text at its widest
CHIP, CHIP_ON = "\x1b[48;2;48;52;64m\x1b[38;2;200;204;214m", "\x1b[48;2;56;78;120m\x1b[38;2;240;242;248m"  # a context, put away or in the text
TAG = "\x1b[48;2;56;78;120m"  # a context's tag in the input's text
NOTE_ROOM = 3 + 4 + BUBBLE_TEXT  # beside a picture with notes: the leader's turn, a bubble's border and its text
HELP = """Shortcuts
  enter          send            esc            interrupt the agent, or skip a question
  ↑ ↓            history         pgup pgdn      scroll the transcript (or the wheel)
  ctrl+u         clear the line  ctrl+l         redraw
Molecules
  click one      edit or annotate it where it is (the right button's menus have highlights and notes)
                 F2 puts the tools away or brings them back · Done (ctrl+q) puts it in your message · Cancel (ctrl+c)
  /edit [N]      the same for molecule N, or the last one; /edit N box opens the editor in a box instead
  /editor box    clicks open the box from now on (/editor inline goes back)
  the line under it  drag it (or alt+arrows while editing) to make the drawing larger or smaller
Pointing things out
  drag across text   highlight it; it waits above the input, and a click there puts it in your message
  a bubble           drag it aside; – folds it to ◆ (click to open), × hides it; /notes shows them all again,
                     /notes hide folds them all, /notes reset puts them back
  above the input    what you edited or highlighted: click to put it in your message (or take it out), × to drop it
Commands
  /help          this help       /clear         clear the transcript"""


def context(molecule):
    """How an edited molecule goes into the user's message: ⟦mol N name, edited: CXSMILES⟧. SMILES never holds ⟦ or ⟧."""
    return f"⟦mol {molecule.number} {molecule.name}, edited: {molecule.smiles}⟧"


def tag(molecule):
    """What the input box and the transcript show of it, the CXSMILES with its annotations being long: ⟦mol N name, edited #k⟧."""
    return f"⟦mol {molecule.number} {molecule.name}, edited #{molecule.edits}⟧"


@dataclass
class Context:
    tag: str  # what the input's text shows
    full: str  # what the message carries instead
    label: str  # what its chip above the input says
    group: tuple = ()  # a newer context of the same group replaces it (an edited molecule's latest edit)


def quoted(text):
    """How text the user highlighted goes into their message: ⟦highlighted: "text"⟧."""
    return f"⟦highlighted: \"{text}\"⟧ "


CONTEXT = re.compile(r"⟦mol (\d+) (.*?), edited: ([^⟧]+)⟧")
QUOTED = re.compile(r"⟦highlighted: \"(.*?)\"⟧")


def _normal(text):
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Block:
    kind: str  # banner, user, assistant, tool, note
    text: str = ""
    tool_id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    state: str = "running"  # for tools: running, done, error
    result: list = field(default_factory=list)  # lines under the tool, plain text
    panel: list = field(default_factory=list)  # a tui.show panel's lines, which may carry ANSI colours
    tone: str = ""  # for notes: "", "error"
    open: bool = True  # an assistant block still taking text
    molecule: object = None  # the Molecule a tui.show panel's picture is
    image_line: int = -1  # where its picture starts, among the block's lines


@dataclass
class Molecule:
    number: int
    name: str
    smiles: str  # CXSMILES, with its annotations
    block: Block
    image: int  # its Kitty picture's id
    cols: int
    rows: int
    edits: int = 0
    notes: list = field(default_factory=list)  # its notes, as bubbles: {text, row (of its leader in the picture)}


_KEYS = itertools.count(1)


@dataclass
class Highlight:
    block: Block
    phrase: str
    colour: str = "yellow"
    note: str = ""
    by: str = "agent"  # or "user"
    key: int = field(default_factory=lambda: next(_KEYS))


@dataclass
class Question:
    tool_id: str
    question: str
    options: list
    selected: int = 0


class App:
    def __init__(self, bridge, cols=80, rows=24):
        self.bridge = bridge
        self.cols, self.rows = cols, rows
        self.session = ""
        self.blocks = [Block("banner")]
        self.input, self.cursor = "", 0
        self.history, self.history_at = [], None
        self.question = None
        self.busy, self.busy_since, self.label = False, 0.0, "Thinking"
        self.scroll = 0  # lines scrolled back from the bottom
        self.frame = 0
        self.started = False
        self.images = 0  # the Kitty pictures sent, numbered from 1
        self.molecules = []  # the molecules shown, numbered from 1
        self.placed = {}  # molecule number -> (row, col, rows, cols) of its picture on the screen, as last drawn
        self.editing = None  # the molecule being edited, see edit()
        self.edit_mode = "inline"  # what a click opens: "inline" or "box"
        self.tools_shown = True  # the inline editor's tools, as the user last left them
        self.highlights = []  # text marked in the transcript
        self.selecting = None  # a drag across the transcript: [row, col, row, col] on the screen
        self.expansions = {}  # a context's tag in the input -> what the message carries instead, see tag()
        self.chips = []  # the contexts waiting above the input, see Context
        self.bubbles = {}  # a bubble's key -> how the user left it: {"dx", "dy", "min", "gone"}
        self.dragging = None  # a bubble being dragged
        self._bubble_hits, self._chip_hits = [], []

    # -- from the page ----------------------------------------------------------------------------------------------------------------

    def start(self):
        self.started = True
        # the alternate screen, pastes marked as pastes, and the mouse reported (SGR) with drags, to pick a molecule or mark text
        self.bridge.write("\x1b[?1049h\x1b[?2004h\x1b[?1002h\x1b[?1006h")
        self.draw()

    def resize(self, cols, rows):
        self.cols, self.rows = max(int(cols), 20), max(int(rows), 8)
        if self.editing:  # the editor was placed for the old size: the edit is cancelled
            self._edit_end(3, "")
        self.selecting = None
        self.draw()

    def tick(self):
        """Called a few times a second, to turn the spinner."""
        if self.busy:
            self.frame += 1
            self.draw()

    def key(self, data):
        if self.editing:  # everything goes to the editor, which answers with what to draw
            if self.editing["mode"] == "inline":
                data = self._edit_controls(data)
            self.editing["pending"] += data
            self._edit_pump()
            return
        for key in keyboard.parse(data):
            self._key(key)
        self.draw()

    def event(self, data):
        """One event from the agent, as JSON text or a dict."""
        event = json.loads(data) if isinstance(data, str) else data
        handler = getattr(self, "_on_" + event.get("type", ""), None)
        if handler:
            handler(event)
        self.draw()

    # -- the agent's events -----------------------------------------------------------------------------------------------------------

    def _on_session(self, event):
        self.session = event.get("id", "")

    def _on_turn_start(self, event):
        self.busy, self.busy_since, self.label = True, time.monotonic(), event.get("label", "Thinking")

    def _on_status(self, event):
        self.label = event.get("label", self.label)

    def _on_turn_end(self, event):
        self.busy = False
        self._close_text()

    def _on_text(self, event):
        last = self.blocks[-1]
        if last.kind == "assistant" and last.open:
            last.text += event.get("delta", "")
        else:
            self.blocks.append(Block("assistant", event.get("delta", "")))
        self.scroll = 0

    def _on_text_end(self, event):
        self._close_text()

    def _on_error(self, event):
        self.blocks.append(Block("note", event.get("message", "Something went wrong."), tone="error"))

    def _on_tool_use(self, event):
        self._close_text()
        block = Block("tool", tool_id=event["id"], name=event.get("name", ""), input=event.get("input") or {})
        self.blocks.append(block)
        self.scroll = 0
        if block.name == "tui.show":
            self._show(block)
        elif block.name == "tui.ask":
            self._ask(block)
        elif block.name == "tui.mark":
            self._mark(block)
        elif block.name.startswith("tui."):
            self._finish(block, f"No such TUI tool: {block.name}", error=True)

    def _on_tool_result(self, event):
        block = self._tool(event.get("id"))
        if block:
            block.state = "error" if event.get("is_error") else "done"
            block.result = str(event.get("output", "")).splitlines()[:4]

    # -- the TUI's own tools ----------------------------------------------------------------------------------------------------------

    def _show(self, block):
        image, body = block.input.get("image"), block.input.get("body")
        block.panel = []
        result = {"shown": True}
        if isinstance(image, dict) and image.get("png"):
            cols, rows = int(image.get("cols") or 1), int(image.get("rows") or 1)
            self.images += 1
            self.bridge.write(kitty.transmit(str(image["png"]), self.images, cols, rows))
            block.panel += kitty.lines(self.images, cols, rows)
            smiles = block.input.get("smiles")
            if smiles:
                molecule = Molecule(len(self.molecules) + 1, str(block.input.get("title") or smiles)[:60], str(smiles), block,
                                    self.images, cols, rows)
                self.molecules.append(molecule)
                block.molecule = molecule
                block.panel.append(style(f"✎ click to edit or annotate it, or /edit {molecule.number}", GREY))
                result["mol"] = molecule.number
                if "atomProp:" in molecule.smiles:  # annotated already: drawn again with its notes beside it
                    self._repicture(molecule)
        if body:
            block.panel += str(body).split("\n")
        block.state = "done"
        self.bridge.post_tool_result(block.tool_id, json.dumps({**result, "lines": len(block.panel)}))

    def _ask(self, block):
        options = [str(o) for o in block.input.get("options") or []]
        if not options:
            self._finish(block, "A question needs options.", error=True)
            return
        self.question = Question(block.tool_id, str(block.input.get("question", "")), options)

    def _answer(self, index):
        question, self.question = self.question, None
        block = self._tool(question.tool_id)
        if index is None:
            block.state, block.result = "done", ["(skipped)"]
            self.bridge.post_tool_result(question.tool_id, json.dumps({"choice": None, "skipped": True}))
        else:
            block.state, block.result = "done", [question.options[index]]
            self.bridge.post_tool_result(question.tool_id, json.dumps({"choice": question.options[index], "index": index}))

    def _mark(self, block):
        given = block.input
        colour = str(given.get("highlight") or "")
        if colour and colour not in HIGHLIGHTS:
            self._finish(block, f"highlight must be one of {', '.join(HIGHLIGHTS)}.", error=True)
            return
        if given.get("mol") is not None:
            molecule = self._molecule(given.get("mol"))
            if molecule is None:
                self._finish(block, f"There is no molecule {given.get('mol')}.", error=True)
                return
            mark = {name: given[name] for name in ("atoms", "smarts", "note") if given.get(name)}
            if mark and not colour and not given.get("note"):
                colour = "yellow"
            if colour:
                mark["highlight"] = colour
            self._repicture(molecule, mark or None, bool(given.get("clear")), block.tool_id)
            return
        phrase = _normal(str(given.get("text") or ""))
        if given.get("clear"):  # the agent's highlights of that text, or all of them
            self.highlights = [h for h in self.highlights if h.by != "agent" or (phrase and phrase.lower() != h.phrase.lower())]
            self._finish(block, "Cleared the highlights.")
            return
        if not phrase:
            self._finish(block, "Give mol (a molecule's number) or text to mark.", error=True)
            return
        target = next((b for b in reversed(self.blocks) if b is not block and b.kind in ("assistant", "user", "note")
                       and phrase.lower() in _normal(QUOTED.sub("", CONTEXT.sub("", b.text))).lower()), None)
        if target is None:
            self._finish(block, f"\"{phrase}\" isn't in the transcript.", error=True)
            return
        self.highlights.append(Highlight(target, phrase, colour or "yellow", str(given.get("note") or "")[:200]))
        self._finish(block, f"Highlighted \"{truncate(phrase, 40)}\"" + (" with a note" if given.get("note") else ""))

    def _finish(self, block, text, error=False):
        block.state, block.result = ("error" if error else "done"), [text]
        self.bridge.post_tool_result(block.tool_id, json.dumps({"error": text} if error else {"ok": text}))

    def _tool(self, tool_id):
        return next((b for b in reversed(self.blocks) if b.kind == "tool" and b.tool_id == tool_id), None)

    def _molecule(self, number):
        try:
            number = int(number)
        except (TypeError, ValueError):
            return None
        return next((m for m in self.molecules if m.number == number), None)

    # -- a molecule's picture, with its notes in bubbles beside it --------------------------------------------------------------------

    def _repicture(self, molecule, mark=None, clear=False, tool_id=""):
        """Ask molterm for the molecule's picture again: with its annotations, a mark added or cleared, and room for bubbles."""
        noted = "atomNote" in molecule.smiles or "_note." in molecule.smiles or bool(mark and mark.get("note"))
        cols = max(self.cols - 6 - (NOTE_ROOM if noted else 0), 20)
        request = {"number": molecule.number, "tool": tool_id, "smiles": molecule.smiles, "cols": cols}
        if mark:
            request["mark"] = mark
        if clear:
            request["clear"] = True
        self.bridge.picture(json.dumps(request))

    def picture_answer(self, request, text):
        """molterm's answer to bridge.picture: {png, cols, rows, smiles, notes, marked}, as JSON text."""
        request = json.loads(request) if isinstance(request, str) else request
        molecule = self._molecule(request.get("number"))
        answer = json.loads(text) if isinstance(text, str) else text
        if molecule is None or not isinstance(answer, dict) or not answer.get("png"):
            self.picture_failed(request, "molterm sent no picture")
            return
        old_rows = molecule.rows
        self.images += 1
        molecule.cols, molecule.rows = int(answer.get("cols") or 1), int(answer.get("rows") or 1)
        self.bridge.write(kitty.transmit(str(answer["png"]), self.images, molecule.cols, molecule.rows) + kitty.delete(molecule.image))
        molecule.image = self.images
        molecule.smiles = str(answer.get("smiles") or molecule.smiles)
        molecule.notes = [n for n in answer.get("notes") or [] if isinstance(n, dict)]
        block = molecule.block
        block.panel = kitty.lines(molecule.image, molecule.cols, molecule.rows) + block.panel[old_rows:]
        tool = self._tool(request.get("tool"))
        if tool:
            marked = answer.get("marked") or []
            tool.state = "done"
            tool.result = [f"Marked {len(marked)} atom{'s' if len(marked) != 1 else ''} of mol {molecule.number}" if marked
                           else f"Cleared mol {molecule.number}'s marks" if request.get("clear") else "Nothing matched"]
            self.bridge.post_tool_result(tool.tool_id, json.dumps({"marked": marked, "smiles": molecule.smiles}))
        self.draw()

    def picture_failed(self, request, message):
        request = json.loads(request) if isinstance(request, str) else request
        tool = self._tool(request.get("tool"))
        if tool:
            self._finish(tool, str(message), error=True)
        else:
            self.blocks.append(Block("note", f"The picture couldn't be drawn: {message}", tone="error"))
        self.draw()

    # -- molecules, edited where they are shown --------------------------------------------------------------------------------------

    def edit(self, number, mode=None):
        """Edit molecule `number` where it is shown, scrolling to it if need be: inline, or in a box over it."""
        molecule = self._molecule(number)
        if molecule is None or self.editing:
            if molecule is None:
                self.blocks.append(Block("note", f"There is no molecule {number}." if self.molecules else "No molecule is shown yet."))
            return
        mode = mode or self.edit_mode
        above = 3 if mode == "box" or self.tools_shown else 1  # rows the editor wants above the picture
        screen, _ = self.lines()
        room = self._room
        top = self.placed.get(number, (None,))[0]
        rows_wanted = max(molecule.rows, INLINE_ROWS_MIN) if mode == "inline" else molecule.rows + 3
        if top is None or top < above or top + rows_wanted > room:  # bring it into view, with room for the tools above
            self.scroll = max(self._content - (self._content_at[number] - above + room), 0)
            screen, _ = self.lines()
            top = self.placed.get(number, (0,))[0]
        if mode == "box":
            rows = min(max(molecule.rows + 7, EDIT_ROWS), room)
            if rows < EDIT_ROWS_MIN or self.cols - 1 < EDIT_COLS_MIN:
                self.blocks.append(Block("note", "The window is too small to edit here; make it larger and try again.", tone="error"))
                return
            box_top = min(max(top - 3, 0), room - rows)  # border and tools above the picture, so the drawing stays about where it was
            place = {"box": (box_top, 0, rows, self.cols - 1), "frozen": screen[:room]}
        else:
            if room - above - 1 < 4 or self.cols - 1 < STRIP_COLS:
                self.blocks.append(Block("note", "The window is too small to edit here; make it larger and try again.", tone="error"))
                return
            place = {"rows": max(molecule.rows, INLINE_ROWS_MIN), "cols": max(molecule.cols + 4, INLINE_COLS_MIN)}
        self.selecting = None
        self.editing = {"molecule": molecule, "mode": mode, **place, "state": "", "pending": "", "first": True, "waiting": False,
                        "closing": False, "result": None, "relayout": False, "resizing": None}
        if mode == "inline":  # the drawing takes the picture's place in the transcript, which makes room for it
            self._place_edit(top)
            inline = self.editing["inline"]
            self.editing["at"] = (inline[0], inline[1], min(molecule.rows, inline[2]), molecule.cols)
        self.bridge.write("\x1b[?25l\x1b[?1003h")  # no cursor, and every movement, for the editor's hover and drags
        self.draw()
        self._edit_pump()

    def _place_edit(self, top):
        """Lay the transcript out around the inline editor's drawing, its top on screen row `top` if it can be, with room above for
        the tools and below for the line that resizes it; keeps the drawing's place in `inline`."""
        e, number = self.editing, self.editing["molecule"].number
        above = 3 if self.tools_shown else 1
        room = self.rows - len(self._footer(self.cols - 1))
        top = min(max(top if top is not None else above, above), max(room - 5, 0))
        e["rows"] = max(min(e["rows"], room - top - 1), 4)
        e["cols"] = max(min(e["cols"], self.cols - 1 - EDIT_LEFT), 24)
        self.lines()
        self.scroll = max(self._content - (self._content_at[number] - top) - self._room, 0)
        self.lines()
        e["inline"] = (self.placed.get(number, (top,))[0], EDIT_LEFT, e["rows"], e["cols"])

    def _resize_edit(self, rows, cols):
        e = self.editing
        if (rows, cols) == (e["rows"], e["cols"]):
            return
        e["rows"], e["cols"] = rows, cols
        self._place_edit(e["inline"][0])
        e["relayout"] = True
        self._edit_pump()

    def _edit_controls(self, data):
        """Take out of what is typed or clicked what is the TUI's own while editing inline: the line under the drawing, dragged to
        resize it, and Alt with the arrows. The rest is the editor's."""
        kept, last = [], 0
        for match in EDIT_INPUT.finditer(data):
            kept.append(data[last:match.start()])
            last = match.end()
            if not self._edit_control(match):
                kept.append(match.group())
        kept.append(data[last:])
        return "".join(kept)

    def _edit_control(self, match):
        e = self.editing
        if e["closing"]:
            return False
        top, left, rows, cols = e["inline"]
        if match.group(5):
            step = {"A": (-2, 0), "B": (2, 0), "C": (0, 4), "D": (0, -4)}[match.group(5)]
            self._resize_edit(rows + step[0], cols + step[1])
            return True
        button, col, row, pressed = int(match.group(1)), int(match.group(2)) - 1, int(match.group(3)) - 1, match.group(4) == "M"
        if e["resizing"]:
            row0, col0 = e["resizing"]
            new_rows = max(min(rows + row - row0, self._room - top - 1), 4)
            new_cols = max(min(cols + col - col0, self.cols - 1 - left), 24)
            if not pressed and not button & 32:  # let go: the drawing takes the new size
                e["resizing"] = None
                self._resize_edit(new_rows, new_cols)
            else:  # where the line would go
                self.bridge.write(f"\x1b[{top + new_rows + 1};{left + 1}H" + _grip(new_cols, f"{new_rows} × {new_cols}") + RESET)
            return True
        if button == 0 and pressed and row == top + rows and left <= col <= left + cols:
            e["resizing"] = (row, col)
            return True
        return False

    def _edit_pump(self):
        """Send the editor what has been typed or clicked, one request at a time; what comes meanwhile waits for the next."""
        e = self.editing
        if not e or e["waiting"] or not (e["first"] or e["pending"] or e["closing"] or e["relayout"]):
            return
        molecule = e["molecule"]
        if e["closing"]:
            fields = {"close": "1"}
        else:
            fields = {"cols": self.cols, "rows": self.rows, "title": f"mol {molecule.number} · {molecule.name}",
                      "events": e["pending"].encode().hex()}
            if e["mode"] == "box":
                fields["box"] = ",".join(str(n) for n in e["box"])
            else:
                fields.update({"inline": ",".join(str(n) for n in e["inline"]), "at": ",".join(str(n) for n in e["at"]),
                               "strip": "1" if self.tools_shown else "0"})
            fields.update({"smiles": molecule.smiles} if e["first"] else {"state": e["state"]})
        e["pending"], e["first"], e["waiting"], e["relayout"] = "", False, True, False
        self.bridge.edit(json.dumps(fields))

    def edit_answer(self, text):
        """The editor's answer: "<status> <state>", a newline, and what to print (on Done, the CXSMILES)."""
        e = self.editing
        if not e:
            return
        e["waiting"] = False
        head, _, output = str(text).partition("\n")
        if e["closing"]:
            self.bridge.write(output)
            self._edit_closed()
            return
        status, _, state = head.partition(" ")
        if status == "0":
            e["state"] = state
            self.bridge.write(self._from_editor(output))
            self._edit_pump()
        else:
            self._edit_end(int(status) if status.isdigit() else 2, output.strip())

    def _from_editor(self, output):
        """The editor's output, with what it tells its host acted on: the screen drawn again where it asks, the tools remembered."""
        def notice(match):
            if match.group(1) == "tools":
                self.tools_shown = match.group(2) == "1"
                return ""
            if match.group(1) == "repaint":  # the transcript as it is now, around the drawing, and the footer
                screen = self.editing.get("frozen") or self.lines()[0]
                return "".join(f"\x1b[{row + 1};1H" + line + RESET + "\x1b[K" for row, line in enumerate(screen)) + "\x1b[?25l"
            return ""
        return HOST_NOTICE.sub(notice, output)

    def edit_failed(self, message):
        e = self.editing
        if not e:
            return
        e["waiting"] = False
        if e["closing"]:
            self._edit_closed()
        else:
            self._edit_end(2, f"The editor stopped: {message}")

    def _edit_end(self, status, text):
        """1 Done (text is the CXSMILES), 2 failed (text says why), 3 cancelled: remove the editor's pictures, then carry on."""
        e = self.editing
        e["result"], e["closing"] = (status, text), True
        if not e["waiting"]:
            self._edit_pump()

    def _edit_closed(self):
        e, self.editing = self.editing, None
        self.bridge.write("\x1b[?1003l\x1b[?1002h")
        status, text = e["result"]
        molecule = e["molecule"]
        if status == 1 and text and text != molecule.smiles:
            molecule.smiles, molecule.edits = text, molecule.edits + 1
            self._add_context(Context(tag(molecule), context(molecule), f"mol {molecule.number} {molecule.name} · edited #{molecule.edits}",
                                      ("mol", molecule.number)))
            self._repicture(molecule)
        elif status == 2:
            self.blocks.append(Block("note", text or "The editor stopped.", tone="error"))
        if e["mode"] == "inline" and e.get("inline"):  # the picture back where the drawing was, the transcript closing up below it
            self.lines()
            self.scroll = max(self._content - (self._content_at[molecule.number] - e["inline"][0]) - self._room, 0)
        self.draw()

    # -- contexts, waiting above the input to be put into a message -------------------------------------------------------------------

    def _add_context(self, ctx):
        """A context waits above the input, put away: a click puts it into the text, where its tag stands for it (sent in full)."""
        for old in [c for c in self.chips if c.tag == ctx.tag or (ctx.group and c.group == ctx.group)]:
            self._drop_context(old)
        self.expansions[ctx.tag] = ctx.full
        self.chips.append(ctx)

    def _drop_context(self, ctx, keep=False):
        """Take a context's tag out of the text; and the context away too, unless `keep` (it is then put away above the input)."""
        at = self.input.find(ctx.tag)
        if at >= 0:
            end = at + len(ctx.tag) + (1 if self.input[at + len(ctx.tag):at + len(ctx.tag) + 1] == " " else 0)
            self.input = self.input[:at] + self.input[end:]
            if self.cursor > at:
                self.cursor = max(at, self.cursor - (end - at))
        if not keep and ctx in self.chips:
            self.chips.remove(ctx)

    def _toggle_context(self, ctx):
        if ctx.tag in self.input:
            self._drop_context(ctx, keep=True)
        else:
            insert = ("" if not self.input[:self.cursor] or self.input[:self.cursor].endswith(" ") else " ") + ctx.tag + " "
            self.input = self.input[:self.cursor] + insert + self.input[self.cursor:]
            self.cursor += len(insert)

    def _chip_lines(self, inner):
        """The contexts' chips, as lines inside the input box, and where each one's parts are: [(line, start, end, context, part)]."""
        lines, hits, line, used = [], [], "", 0
        for ctx in self.chips:
            shown = ctx.tag in self.input
            label = f" {'◆' if shown else '◇'} {truncate(ctx.label, 40)} "
            chip = style(label, CHIP_ON if shown else CHIP) + style("×", CHIP_ON if shown else CHIP, GREY) + style(" ", CHIP_ON if shown else CHIP)
            size = width(label) + 2
            if used and used + size + 1 > inner:
                lines.append(line)
                line, used = "", 0
            start = used + (1 if used else 0)
            line += (" " if used else "") + chip
            hits.append((len(lines), start, start + width(label), ctx, "toggle"))
            hits.append((len(lines), start + width(label), start + size, ctx, "dismiss"))
            used = start + size
        if self.chips:
            hint = "  click: into your message · ×: drop"
            if used + width(hint) <= inner:
                line += style(hint, GREY)
            lines.append(line)
        return lines[-3:], [h for h in hits if h[0] >= len(lines) - 3]

    def _close_text(self):
        for block in self.blocks:
            if block.kind == "assistant":
                block.open = False

    # -- keys and the mouse -----------------------------------------------------------------------------------------------------------

    def _key(self, key):
        if key.name == "mouse":
            self._mouse(*key.mouse)
            return
        if key.name in ("pageup", "pagedown"):
            step = max(self.rows - 6, 1)
            self.scroll = max(self.scroll + (step if key.name == "pageup" else -step), 0)
            return
        if key.name == "ctrl-l":
            self.bridge.write("\x1b[2J")
            return
        if self.question:
            self._question_key(key)
        else:
            self._input_key(key)

    def _mouse(self, button, col, row, pressed):
        if button in (64, 65):  # the wheel
            self.scroll = max(self.scroll + (3 if button == 64 else -3), 0)
            return
        if self.dragging:  # a bubble, moved with the pointer until let go
            d = self.dragging
            state = self.bubbles.setdefault(d["key"], {})
            state["dx"], state["dy"] = d["dx"] + col - d["col"], d["dy"] + row - d["row"]
            if not pressed and not button & 32:
                self.dragging = None
            return
        if button == 32 and self.selecting:  # dragging, the left button held
            self.selecting[2:] = [row, col]
            return
        if button != 0:
            return
        if pressed and self._press_controls(row, col):
            return
        if pressed:
            for number, (top, left, rows, cols) in self.placed.items():
                if top <= row < top + rows and left <= col < left + cols:
                    if not self.question:
                        self.edit(number)
                    return
            self.selecting = [row, col, row, col] if row < self._room else None
            return
        selection, self.selecting = self.selecting, None
        if not selection:
            return
        selection[2:] = [row, col]
        if selection[:2] == selection[2:]:  # a click: on a highlight of the user's, it removes it
            found = self._highlight_at(row, col)
            if found:
                self.highlights.remove(found)
            return
        self._mark_selection(selection)

    def _press_controls(self, row, col):
        """A press on a bubble (its –, its ×, or itself, to drag it), a folded bubble, or a context above the input."""
        for line, start, end, ctx, part in self._chip_hits:
            if line == row and start <= col < end:
                (self._toggle_context if part == "toggle" else self._drop_context)(ctx)
                return True
        index = self._start + row
        for key, part, line, start, end in reversed(self._bubble_hits):  # the one drawn last is on top
            if line == index and start <= col < end:
                state = self.bubbles.setdefault(key, {})
                if part == "fold":
                    state["min"] = True
                elif part == "hide":
                    state["gone"] = True
                elif part == "open":
                    state["min"] = False
                else:
                    self.dragging = {"key": key, "row": row, "col": col, "dx": state.get("dx", 0), "dy": state.get("dy", 0)}
                return True
        return False

    def _selection_cells(self, selection):
        """The block and the screen cells a drag covered, in reading order: (block, [(content line, start col, end col)])."""
        (row1, col1), (row2, col2) = sorted([tuple(selection[:2]), tuple(selection[2:])])
        first = self._owner_at(row1)
        if first is None:
            return None, []
        block, _ = first
        spans = []
        for row in range(row1, min(row2, self._room - 1) + 1):
            owner = self._owner_at(row)
            if owner is None or owner[0] is not block:
                continue
            index = self._start + row
            start = max(col1 if row == row1 else 0, owner[1])
            end = col2 + 1 if row == row2 else self.cols
            if end > start:
                spans.append((index, start, end))
        return block, spans

    def _mark_selection(self, selection):
        block, spans = self._selection_cells(selection)
        if not spans:
            return
        content = self._content_lines
        text = _normal(" ".join("".join(t for _, t in cells(content[index])[start:end]) for index, start, end in spans))
        if len(text) < 2 or block.kind not in ("assistant", "user", "note"):
            return
        self.highlights.append(Highlight(block, text, "blue", by="user"))
        self._add_context(Context(quoted(text).strip(), quoted(text).strip(), f"“{truncate(text, 36)}”"))

    def _owner_at(self, row):
        """The block a screen row shows, if it is text that can be marked: (block, the column its text starts at)."""
        index = self._start + row
        if not 0 <= row < self._room or not 0 <= index < len(self._owners):
            return None
        return self._owners[index]

    def _highlight_at(self, row, col):
        index = self._start + row
        for highlight, line, start, end in self._painted:
            if line == index and start <= col < end and highlight.by == "user":
                return highlight
        return None

    def _question_key(self, key):
        question = self.question
        if key.name == "up":
            question.selected = (question.selected - 1) % len(question.options)
        elif key.name in ("down", "tab"):
            question.selected = (question.selected + 1) % len(question.options)
        elif key.name == "enter":
            self._answer(question.selected)
        elif key.name == "escape":
            self._answer(None)
        elif key.name == "text" and key.text.isdigit() and 1 <= int(key.text) <= len(question.options):
            self._answer(int(key.text) - 1)

    def _input_key(self, key):
        if key.name in ("text", "paste") and not key.alt:
            self.input = self.input[:self.cursor] + key.text + self.input[self.cursor:]
            self.cursor += len(key.text)
            self.history_at = None
        elif key.name == "backspace" and self.cursor:
            before = self.input[:self.cursor]
            start = before.rfind("⟦") if before.endswith("⟧") else -1
            gone = self.cursor - start if start >= 0 and before[start:] in self.expansions else 1  # a context's tag goes whole
            self.input = self.input[:self.cursor - gone] + self.input[self.cursor:]
            self.cursor -= gone
        elif key.name == "delete":
            self.input = self.input[:self.cursor] + self.input[self.cursor + 1:]
        elif key.name == "left":
            self.cursor = max(self.cursor - 1, 0)
        elif key.name == "right":
            self.cursor = min(self.cursor + 1, len(self.input))
        elif key.name in ("home", "ctrl-a"):
            self.cursor = 0
        elif key.name in ("end", "ctrl-e"):
            self.cursor = len(self.input)
        elif key.name == "ctrl-u":
            self.input, self.cursor = "", 0
        elif key.name == "enter" and key.alt:
            self.input = self.input[:self.cursor] + "\n" + self.input[self.cursor:]
            self.cursor += 1
        elif key.name == "enter":
            self._submit()
        elif key.name in ("up", "down"):
            self._recall(-1 if key.name == "up" else 1)
        elif key.name in ("escape", "ctrl-c"):
            if self.busy:
                self.bridge.interrupt()
                self.blocks.append(Block("note", "Interrupted by user", tone="error"))
            elif key.name == "ctrl-c":
                self.input, self.cursor = "", 0

    def _recall(self, step):
        if not self.history:
            return
        at = len(self.history) if self.history_at is None else self.history_at
        at = min(max(at + step, 0), len(self.history))
        self.history_at = at
        self.input = self.history[at] if at < len(self.history) else ""
        self.cursor = len(self.input)

    def _submit(self):
        text = self.input.strip()
        self.input, self.cursor, self.history_at, self.scroll = "", 0, None, 0
        if not text:
            return
        if text == "/help":
            self.blocks.append(Block("note", HELP))
            return
        edit = re.fullmatch(r"/edit(?:\s+(\d+))?(?:\s+(box|inline))?", text)
        if edit:
            number = int(edit.group(1)) if edit.group(1) else (self.molecules[-1].number if self.molecules else 0)
            self.edit(number, edit.group(2))
            return
        editor = re.fullmatch(r"/editor(?:\s+(box|inline))?", text)
        if editor:
            self.edit_mode = editor.group(1) or self.edit_mode
            self.blocks.append(Block("note", f"Clicking a molecule edits it {'in a box' if self.edit_mode == 'box' else 'inline'}."))
            return
        notes = re.fullmatch(r"/notes(?:\s+(hide|reset))?", text)
        if notes:
            for state in self.bubbles.values():
                if notes.group(1) == "hide":
                    state["min"] = True
                else:
                    state["min"] = state["gone"] = False
                if notes.group(1) == "reset":
                    state["dx"] = state["dy"] = 0
            return
        if text == "/clear":
            self.blocks, self.molecules, self.placed, self.highlights = [Block("banner")], [], {}, []
            self.bridge.write("".join(kitty.delete(n) for n in range(1, self.images + 1)))  # the pictures go with their placeholders
            return
        self.history.append(text)
        self.blocks.append(Block("user", text))
        self.chips = [c for c in self.chips if c.tag not in text]  # sent, so no longer waiting
        for short, full in self.expansions.items():
            text = text.replace(short, full)
        self.bridge.post_message(text, self.cols - 6)

    # -- drawing ----------------------------------------------------------------------------------------------------------------------

    def _text_lines(self, block, lines, prefix, first, rest, styled=lambda line: line):
        """A block's wrapped text, each line after its prefix (`first`, then `rest`), with its highlights painted on: returns the lines
        and, for each highlight, where it was painted [(highlight, line, start col, end col)] and its first line and column."""
        out = [styled((first if i == 0 else rest) + line) for i, line in enumerate(lines)]
        painted = []
        for highlight in (h for h in self.highlights if h.block is block):
            for line, start, end in _find(lines, highlight.phrase):
                out[line] = paint(out[line], prefix + start, prefix + end, HIGHLIGHTS.get(highlight.colour, HIGHLIGHTS["yellow"]))
                painted.append((highlight, line, prefix + start, prefix + end))
        return out, painted

    def _block_lines(self, block, cols):
        """The block's lines, the highlights painted in them, and the column its markable text starts at (None if it has none)."""
        if block.kind == "banner":
            inner = cols - 4
            rows = [style("✻ ", ACCENT) + style("Welcome to the molterm agent!", BOLD), "",
                    style("  An agent, talking to you through this interface,", GREY),
                    style("  which runs as Python in your browser (Pyodide).", GREY), "",
                    style(f"  session: {self.session or 'connecting…'}", GREY)]
            border = style("╭" + "─" * (cols - 2) + "╮", ACCENT)
            return [border, *(style("│ ", ACCENT) + pad(row, inner) + style(" │", ACCENT) for row in rows),
                    style("╰" + "─" * (cols - 2) + "╯", ACCENT), "",
                    style(truncate(" Tips: type a message for the agent and press enter. /help for help.", cols), GREY), ""], [], None
        if block.kind == "user":
            lines, painted = self._text_lines(block, wrap(block.text, cols - 4), 2, "> ", "  ",
                                              lambda line: style(pad(line, cols - 2), USER_BG, GREY))
            return lines + [""], painted, 2
        if block.kind == "assistant":
            lines, painted = self._text_lines(block, wrap(block.text, cols - 2), 2, "● ", "  ")
            return lines + [""], painted, 2
        if block.kind == "note":
            tone = RED if block.tone == "error" else GREY
            lines, painted = self._text_lines(block, block.text.split("\n"), 5, "  └  ", "     ", lambda line: style(line, tone))
            return lines + [""], painted, 5
        if block.kind == "tool":
            return self._tool_lines(block, cols), [], None
        return [], [], None

    def _tool_lines(self, block, cols):
        mark = {"running": style("●", GREY) if self.frame % 2 else "●", "done": style("●", GREEN), "error": style("●", RED)}[block.state]
        name = TOOL_NAMES.get(block.name, block.name)
        given = block.input
        if block.name == "tui.mark":
            what = given.get("note") or given.get("smarts") or given.get("atoms") or ("clear" if given.get("clear") else "")
            argument = (f"mol {given.get('mol')}: {what}" if given.get("mol") is not None else f"\"{given.get('text', '')}\"")
        else:
            named = given.get("title") or given.get("smiles") or given.get("question")
            argument = named or next((v for v in given.values() if isinstance(v, str)), "")  # else the first text it was given
        head = f"{mark} {style(name, BOLD)}({truncate(str(argument), max(cols - width(name) - 6, 8))})"
        lines = [head]
        body = []
        if block.panel:
            title = given.get("title")
            molecule = block.molecule
            if molecule:
                tag = f"mol {molecule.number}" + (" · edited" if molecule.edits else "")
                title = (style(str(title), BOLD) + "  " if title else "") + style(tag, GREY)
            elif title:
                title = style(str(title), BOLD)
            block.image_line = 2 if title else 1  # after the tool's line and the title
            panel = block.panel
            e = self.editing
            if molecule and e and e["mode"] == "inline" and e["molecule"] is molecule:  # room for its drawing, and the line under it
                panel = [""] * e["rows"] + [_grip(e["cols"], "drag this line, or alt+arrows, to resize")] + panel[molecule.rows:]
            body += ([title] if title else []) + [truncate(line, cols - 5) for line in panel]
        if block.result:
            tone = (RED,) if block.state == "error" else (GREY,)
            body += [style(truncate(line, cols - 5), *tone) for line in block.result]
        if block.state == "running" and not body:
            body = [style("Waiting for your answer…" if block.name == "tui.ask" else "Running…", GREY)]
        lines += [style("  └  ", GREY) + line if i == 0 else "     " + line for i, line in enumerate(body)]
        return lines + [""]

    def _footer(self, cols):
        self._chip_regions = []
        if self.question:
            return self._question_lines(cols)
        lines = []
        if self.busy:
            seconds = int(time.monotonic() - self.busy_since)
            spinner = SPINNER[self.frame % len(SPINNER)]
            lines.append(style(f"{spinner} {self.label}…", ACCENT) + style(f" ({seconds}s · esc to interrupt)", GREY))
            lines.append("")
        inner = cols - 4
        text_lines = wrap(self.input, inner - 2) if self.input else [""]
        shown = text_lines[-INPUT_LINES_MAX:]
        box = [style("╭" + "─" * (cols - 2) + "╮", GREY)]
        chips, hits = self._chip_lines(inner)
        self._chip_regions = [(len(lines) + 1 + line, 2 + start, 2 + end, ctx, part) for line, start, end, ctx, part in hits]
        box += [style("│ ", GREY) + pad(chip, inner) + style(" │", GREY) for chip in chips]
        tags = [c.tag for c in self.chips if c.tag in self.input]
        for i, line in enumerate(shown):
            prefix = "> " if i == 0 and len(shown) == len(text_lines) else "  "
            content = line if self.input else style("Message the agent", GREY, ITALIC)
            row = style("│ ", GREY) + pad(prefix + content, inner) + style(" │", GREY)
            for tag_text in tags:  # a context's tag stands out from the typing
                for found, start, end in _find(shown, tag_text):
                    if found == i:
                        row = paint(row, 4 + start, 4 + end, TAG)
            box.append(row)
        box.append(style("╰" + "─" * (cols - 2) + "╯", GREY))
        hint = "  ? /help for shortcuts" if not self.scroll else f"  scrolled back {self.scroll} lines · pgdn to return"
        right = f"session {self.session[:8]}" if self.session else "connecting…"
        hint_line = style(pad(hint, cols - width(right) - 2) + right, GREY)
        return lines + box + [hint_line]

    def _question_lines(self, cols):
        question, inner = self.question, cols - 4
        rows = [style(line, BOLD) for line in wrap(question.question, inner)] + [""]
        for i, option in enumerate(question.options):
            chosen = i == question.selected
            text = f"{'❯' if chosen else ' '} {i + 1}. {option}"
            rows.append(style(text, ACCENT) if chosen else text)
        box = [style("╭" + "─" * (cols - 2) + "╮", ACCENT)]
        box += [style("│ ", ACCENT) + pad(row, inner) + style(" │", ACCENT) for row in rows]
        box.append(style("╰" + "─" * (cols - 2) + "╯", ACCENT))
        return box + [style("  ↑↓ to choose · enter to answer · a number picks · esc to skip", GREY)]

    def _bubbles(self, cols):
        """What is drawn over the transcript: [(content line, col, text)], for the molecules' notes and the highlights' notes. Each
        bubble is where the user left it (dragged, folded or hidden); `_bubble_hits` says where its parts are, for the mouse."""
        out, self._bubble_hits = [], []
        e = self.editing
        editing = e["molecule"] if e and e["mode"] == "inline" else None
        for molecule in self.molecules:
            base = self._content_at.get(molecule.number)
            if base is None or not molecule.notes or molecule is editing:
                continue
            x = 5 + molecule.cols  # just past the picture's right edge, where the leaders end
            room = min(cols - x - 3 - 4, BUBBLE_TEXT)
            if room < 8:
                continue
            below = None
            for note in sorted(molecule.notes, key=lambda n: int(n.get("row") or 0)):
                key = ("mol", molecule.number, str(note.get("atom")), str(note.get("bond")), str(note.get("text")))
                state = self.bubbles.setdefault(key, {})
                leader = base + int(note.get("row") or 0)
                if state.get("gone"):
                    continue
                if state.get("min"):
                    out.append((leader, x, style("──◆", NOTE_INK)))
                    self._bubble_hits.append((key, "open", leader, x, x + 3))
                    continue
                rows = _bubble(str(note.get("text") or ""), room)
                top = leader - 1 if below is None else max(leader - 1, below)
                below = top + len(rows)
                top = top + state.get("dy", 0)
                left = max(min(x + 3 + state.get("dx", 0), cols - width(strip(rows[0]))), 0)
                out += _connector(rows, leader, x, top, left)
                out += self._place_bubble(key, rows, top, left, cols)
        for highlight, line, start, end in self._painted_first:
            if not highlight.note:
                continue
            key = ("text", highlight.key)
            state = self.bubbles.setdefault(key, {})
            if state.get("gone"):
                continue
            if state.get("min"):
                out.append((line, end, style("◆", NOTE_INK)))
                self._bubble_hits.append((key, "open", line, end, end + 1))
                continue
            rows = _bubble(highlight.note, min(cols - 8, BUBBLE_TEXT))
            size = width(strip(rows[0]))
            left = max(min(start - 2, cols - 1 - size), 0) + state.get("dx", 0)
            top = line + 1 + state.get("dy", 0)
            if top > line and left < start < left + size - 6:  # under the text: a tail pointing up at it, through a line if need be
                border = strip(rows[0])
                rows[0] = style(border[:start - left] + "┴" + border[start - left + 1:], NOTE_BG, NOTE_INK)
                out += [(r, start, style("│", NOTE_INK)) for r in range(line + 1, top)]
            out += self._place_bubble(key, rows, top, left, cols)
        return out

    def _place_bubble(self, key, rows, top, left, cols):
        size = width(strip(rows[0]))
        left = max(min(left, cols - size), 0)
        inner = size - 4
        self._bubble_hits.append((key, "drag", top, left, left + size))
        for i in range(1, len(rows)):
            self._bubble_hits.append((key, "drag", top + i, left, left + size))
        self._bubble_hits.append((key, "fold", top, left + inner - 2, left + inner + 1))
        self._bubble_hits.append((key, "hide", top, left + inner + 1, left + inner + 3))
        return [(top + i, left, row) for i, row in enumerate(rows)]

    def lines(self):
        """The screen's lines, top to bottom, and where the cursor goes (row, col), or None to hide it."""
        cols = self.cols - 1  # a margin, so nothing is written in the last column
        content, self._content_at, self._owners, painted = [], {}, [], []
        for block in self.blocks:
            lines, marks, text_col = self._block_lines(block, cols)
            if block.molecule and block.image_line >= 0:
                self._content_at[block.molecule.number] = len(content) + block.image_line
            painted += [(h, len(content) + line, start, end) for h, line, start, end in marks]
            self._owners += [(block, text_col) if text_col is not None and i < len(lines) - 1 else None for i in range(len(lines))]
            content += lines
        self._painted = painted
        firsts = {}
        for entry in painted:  # a highlight's note hangs from where it was first painted
            firsts.setdefault(id(entry[0]), entry)
        self._painted_first = list(firsts.values())
        footer = self._footer(cols)
        room, cut = self.rows - len(footer), 0
        if room < 1:
            cut = len(footer) - self.rows
            footer = footer[-self.rows:]
            room = 0
        self._chip_hits = [(room + line - cut, start, end, ctx, part) for line, start, end, ctx, part in self._chip_regions
                           if line >= cut]
        self.scroll = min(self.scroll, max(len(content) - room, 0))
        end = len(content) - self.scroll
        start = max(end - room, 0)
        self._content_lines, self._start = content, start
        shown = content[start:end] if room else []
        e = self.editing
        gap = range(0)
        if e and e["mode"] == "inline" and e["molecule"].number in self._content_at:  # the drawing, its tools and its line
            first = self._content_at[e["molecule"].number]
            gap = range(first - 3, first + e["rows"] + 1)
        shown += [""] * (room - len(shown))  # the empty rows over the input too, where a bubble may be dragged
        for line, col, text in self._bubbles(cols):  # over the transcript, where they are shown
            if start <= line < start + room and line not in gap:
                shown[line - start] = truncate(overlay(shown[line - start], col, text), cols)
        if self.selecting:
            _, spans = self._selection_cells(self.selecting)
            for line, a, b in spans:
                shown[line - start] = paint(shown[line - start], a, min(b, cols), SELECTING)
        screen = shown + footer  # the input stays at the bottom, as in Claude Code
        self._room, self._content = room, len(content)
        self.placed = {}
        for molecule in self.molecules:  # where each molecule's picture is, for a click to find it
            top = self._content_at.get(molecule.number, -10**6) - start
            if top + molecule.rows > 0 and top < len(shown):
                self.placed[molecule.number] = (top, 5, molecule.rows, molecule.cols)
        cursor = None
        if not self.question and not self.editing:
            inner = cols - 4
            before = wrap(self.input[:self.cursor], inner - 2) if self.input[:self.cursor] else [""]
            if self.input[:self.cursor].endswith(" "):
                before[-1] += " "  # wrap drops a trailing space, which the cursor sits after
            text_lines = wrap(self.input, inner - 2) if self.input else [""]
            offset = max(len(text_lines) - INPUT_LINES_MAX, 0)
            box_top = len(screen) - (min(len(text_lines), INPUT_LINES_MAX) + 3)
            row = box_top + 1 + len(before) - 1 - offset
            cursor = (row, 4 + width(before[-1]))
        return screen, cursor

    def render(self):
        if self.editing and self.editing["mode"] == "inline":
            return ""  # the editor draws, and asks for the rest of the screen when it needs it (HOST_REPAINT)
        if self.editing:  # the transcript stays as it was, the box is the editor's, and the footer goes on
            top, _, rows, _ = self.editing["box"]
            frozen = self.editing["frozen"]
            footer = self._footer(self.cols - 1)[-(self.rows - len(frozen)):]
            screen = frozen + [""] * (self.rows - len(frozen) - len(footer)) + footer
            return "\x1b[?25l" + "".join(f"\x1b[{row + 1};1H" + line + RESET + "\x1b[K"
                                          for row, line in enumerate(screen) if not top <= row < top + rows)
        screen, cursor = self.lines()
        out = "\x1b[?25l\x1b[H" + "\r\n".join(line + RESET + "\x1b[K" for line in screen) + "\x1b[J"
        if cursor:
            out += f"\x1b[{cursor[0] + 1};{cursor[1] + 1}H\x1b[?25h"
        return out

    def draw(self):
        if self.started:
            out = self.render()
            if out:
                self.bridge.write(out)


def _find(lines, phrase):
    """Where a phrase is in wrapped lines, which wrapping broke at spaces: [(line, start col, end col)], one for each line it is on."""
    joined, starts = "", []
    for line in lines:
        starts.append(len(joined))
        joined += line + " "
    spans, needle, at = [], phrase.lower(), 0
    haystack = joined.lower()
    while needle:
        found = haystack.find(needle, at)
        if found < 0:
            break
        at = found + len(needle)
        for index, line in enumerate(lines):
            a, b = max(found, starts[index]), min(at, starts[index] + len(line))
            if a < b:
                spans.append((index, width(line[:a - starts[index]]), width(line[:b - starts[index]])))
    return spans


def _bubble(text, room):
    """A speech bubble's lines, the text wrapped to `room` columns at most."""
    lines = wrap(text, room) or [""]
    inner = max(max(width(line) for line in lines), 6)
    ink = NOTE_BG + NOTE_INK
    return ([style("╭" + "─" * (inner - 3) + " – × ╮", NOTE_BG, NOTE_INK)]  # its controls: fold, and hide
            + [ink + "│ " + RESET + NOTE_BG + pad(line, inner) + ink + " │" + RESET for line in lines]
            + [style("╰" + "─" * (inner + 2) + "╯", NOTE_BG, NOTE_INK)])


EDIT_LEFT = 5  # the inline drawing's first column: the picture's


def _grip(cols, label):
    """The line under an inline drawing, which resizes it."""
    label = f" {label} "
    side = max(cols - 1 - width(label), 0)
    return style("╌" * (side // 2) + label + "╌" * (side - side // 2) + "◢", GREY)


def _connector(rows, leader, x, top, left):
    """The leader carried on in text from the picture's edge (row `leader`, column `x`) to a bubble at (top, left), joining it at
    its side; nothing if the bubble was dragged to the left of the edge."""
    turn = left - 2
    if turn < x:
        return []
    joint = top + min(max(leader - top, 1), len(rows) - 2)
    rows[joint - top] = NOTE_BG + NOTE_INK + "┤" + rows[joint - top][len(NOTE_BG + NOTE_INK) + 1:]
    if joint == leader:
        return [(leader, x, style("─" * (left - x), NOTE_INK))]
    down = joint > leader
    out = [(leader, x, style("─" * (turn - x) + ("╮" if down else "╯"), NOTE_INK))]
    out += [(row, turn, style("│", NOTE_INK)) for row in range(min(leader, joint) + 1, max(leader, joint))]
    out.append((joint, turn, style(("╰" if down else "╭") + "─" * (left - turn - 1), NOTE_INK)))
    return out
