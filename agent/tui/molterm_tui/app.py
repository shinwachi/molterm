"""A Claude Code-like terminal interface for a server-side agent, written to run in Pyodide in the browser (or any Python).

The page hands this app the terminal's keys (`key`), the agent's events from the server (`event`), and the wheel (`wheel`) when the
terminal keeps it rather than reporting it as a mouse event, and gives it a `bridge` to talk back through, whose members are listed in
BRIDGE (and BRIDGE_OPTIONAL) beside App. The app redraws the whole screen after each change.

Molecules: a picture shown with a `smiles` is a molecule the app keeps (`molecules`), numbered from 1, and it knows where each is on the
screen (`placed`). Clicking one, or /edit N, edits it where it is, with molterm's editor running on the molterm server through
`bridge.edit(fields as JSON)`, whose answer comes back to `edit_answer(text)` (or `edit_failed(message)`). Inline (the default), the
drawing stays where the picture was, and the editor's tools float over the text beside it, put away and back with F2; in a box
(/edit N box, or /editor box), a compact editor opens in a box over the picture. Done puts the edited molecule, as CXSMILES with its
annotations, into the input for the user's next message.

Under each molecule's picture is its SMILES, the atoms and bonds highlighted on it painted there in their colours (molterm says
which parts, `paint` in its answer); while it's edited inline, the editor tells the SMILES of the drawing as it changes, and its
checkbox, or /smiles, hides it or shows it again.

Pointing things out: a molecule's annotations (from the editor, or the agent's tui.mark) are highlights in its picture, and notes in
speech bubbles beside it, each with a leader to its atom. The picture comes from molterm (/api/v1/callouts) through
`bridge.picture(request as JSON)`, answered by `picture_answer(request, text)` or `picture_failed(request, message)`. Text in the
transcript can be highlighted too, by the agent (tui.mark with `text`) or by the user. Any of the transcript can be selected, dragging
across it; let go, and it has handles to change it, and a menu under it offers to copy it (`bridge.copy(text)` if the page has it,
else OSC 52), to note it, or to highlight it, which also puts it above the input as a context (noting it highlights it first).

Suggestions: the agent may suggest what to type next (a `suggest` event); it shows faintly in the input while that's empty, or
finishes what's typed if it begins the same way, and Tab takes it. It goes when a message is sent.

Tutorial mode: a session started with a tutor (the page's /?tutorial), or given one later (/tutor, through `bridge.tutor()`, or
?session=<id>&tutorial), has a tutor watching it beside the agent, whatever the agent is, which the agent doesn't see. Its tips (`tutor` events) are bubbles of their own in the transcript, marked ✦ tutor, in teal, and it suggests
things to try as the agent does, its suggestions marked ✦. /tutor hides its tips and suggestions, or shows them again.

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
import base64
import heapq
import itertools
import json
import math
import re
import time
from dataclasses import dataclass, field

from . import keys as keyboard
from . import kitty
from .screen import (ACCENT, BOLD, DIM, GREEN, GREY, ITALIC, RED, RESET, USER_BG, cells, join, overlay, pad, paint, strip, style,
                     truncate, width, wrap)

SPINNER = "·✢✳✶✻✽✻✶✳✢"
FADE_FRAMES = 14  # a running tool's dot fades out and in again over this many ticks (about 1.7 s)
CHEVRON_FRAMES = 12  # the chevrons' run down the screen, at the third click elsewhere and every other one after
FLASH_FRAMES = 12  # a question's box pulses twice over this many (about 1.5 s), when something else is clicked
TOOL_NAMES = {"tui.show": "Show", "tui.ask": "Ask", "tui.mark": "Mark", "molterm.render": "Render", "molterm.properties": "Properties"}
INPUT_LINES_MAX = 6
EDIT_ROWS_MIN = 14  # a box editor: its border, the strip of tools, the drawing and two lines of status
EDIT_ROWS = 22  # what it gets when there is room: enough for an atom's menu to open inside it
EDIT_COLS_MIN = 60
SMILES_LINES = 4  # a molecule's SMILES under its picture, at the most
INLINE_ROWS_MIN, INLINE_COLS_MIN = 8, 40  # an inline editor's drawing, at the least, around the picture
STRIP_COLS = 56  # its floating strip of tools, at the least (molterm's editor.STRIP_COLS)
EDIT_INPUT = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[1;3([ABCD])")  # what the TUI keeps for itself while editing inline
HOST_NOTICE = re.compile(r"\x1b\]5523;([a-z]+)(?:=([^\x1b]*))?\x1b\\")  # what the inline editor tells its host (molterm's HOST_REPAINT)
HIGHLIGHTS = {"yellow": "\x1b[48;2;94;78;28m", "green": "\x1b[48;2;38;84;44m", "blue": "\x1b[48;2;32;62;104m", "pink": "\x1b[48;2;100;42;70m"}
SELECTING = "\x1b[48;2;70;70;84m"
NOTE_INK, NOTE_BG = "\x1b[38;2;229;192;123m", "\x1b[48;2;44;39;30m"  # an agent's bubbles, in the colour of molterm's leaders (callouts.LEADER)
USER_INK, USER_NOTE_BG = "\x1b[38;2;140;180;245m", "\x1b[48;2;34;74;140m"  # the user's own, and their text highlights (callouts.USER_LEADER)
WHITE = "\x1b[38;2;255;255;255m"
# the tutor's bubbles, in tutorial mode: teal, apart from the agent's amber and the user's blue
TUTOR_INK, TUTOR_BG, TUTOR_TEXT = "\x1b[38;2;118;204;186m", "\x1b[48;2;22;50;47m", "\x1b[38;2;226;240;236m"
TUTOR_MARK = "✦"
TUTOR_TEXT_MAX = 72  # a tip's text at its widest
# a bubble's colours: (its border and line, its paper, what it covers shown faintly, its text, its – and ×)
AGENT = (NOTE_INK, NOTE_BG, "\x1b[38;2;92;84;66m", "", NOTE_INK)
USER = (USER_INK, USER_NOTE_BG, "\x1b[38;2;96;132;192m", WHITE, WHITE)  # white letters in a blue bubble
WORDS = "\x1b[48;2;64;84;130m"  # a selection of whole words, after a double click
MENU, MENU_ON = "\x1b[48;2;40;42;50m\x1b[38;2;220;222;228m", "\x1b[48;2;56;78;120m\x1b[38;2;255;255;255m"
DOUBLE = 0.4  # seconds between two presses in the same place, for a double click
BUBBLE_TEXT = 30  # a bubble's text at its widest
NOTE_MAX, NOTE_LINES = 200, 6  # a note on text at its longest; molterm keeps a note on a molecule to 120 (annotate.MAX_NOTE)
CHIP, CHIP_ON = "\x1b[48;2;48;52;64m\x1b[38;2;200;204;214m", "\x1b[48;2;56;78;120m\x1b[38;2;240;242;248m"  # a context, put away or in the text
TAG = "\x1b[48;2;56;78;120m"  # a context's tag in the input's text
NOTE_ROOM = 3 + 4 + BUBBLE_TEXT  # beside a picture with notes: the leader's turn, a bubble's border and its text
HELP = """Shortcuts
  enter          send            esc            interrupt the agent, or skip a question
  ↑ ↓            history         pgup pgdn      scroll the transcript (or the wheel)
  ctrl+u         clear the line  ctrl+l         redraw
Molecules
  click one      edit or annotate it where it is (the right button's menus have highlights and notes; in a highlighted
                 group, a note for the atom or bond, or for the group) · your notes are blue, the agent's amber
                 F2 puts the tools away or brings them back · Done (ctrl+q) puts it in your message · Cancel (ctrl+c)
  /edit [N]      the same for molecule N, or the last one; /edit N box opens the editor in a box instead
  /editor box    clicks open the box from now on (/editor inline goes back)
  /smiles        hide each molecule's SMILES under its picture, or show it again (the editor's ☑ SMILES does too); what
                 is highlighted on the molecule is painted in it
  the line under it  drag it (or alt+arrows while editing) to make the drawing larger or smaller
Pointing things out
  drag across text   highlight it, letter by letter, across several lines and replies too; it waits above the input,
                     and a click there puts it in your message · double-click a word for it, double-click and drag
                     for whole words
  right click        on a highlight (or ctrl+click): its menu, to change it (handles ● to drag; enter keeps it, esc
                     undoes), add or edit a note, put it in your message, or remove it
  a bubble           drag it aside; – folds it to ◆ (click to open), × hides it; right click: edit its note, or put it
                     in your message; /notes shows them all again, /notes hide folds them all, /notes reset puts them back
  above the input    what you edited or highlighted: click to put it in your message (or take it out), × to drop it
Commands
  /help          this help       /clear         clear the transcript
  tab            take the suggestion faint in the input (✦: the tutor's) · /tutor  start a tutor, or hide it or show it"""


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
    paint: dict = field(default_factory=dict)  # its SMILES, shown under it, and the parts highlighted: {"smiles", "marks"}


_KEYS = itertools.count(1)


@dataclass(eq=False)
class Highlight:
    block: Block
    phrase: str
    colour: str = "yellow"
    note: str = ""
    by: str = "agent"  # or "user"
    key: int = field(default_factory=lambda: next(_KEYS))
    group: int = 0  # one selection across several blocks is a highlight in each, sharing a group (0: its own key)
    note_by: str = ""  # who wrote its note, if not who highlighted it (the user may note the agent's highlight)

    @property
    def gid(self):
        return self.group or self.key


@dataclass
class Question:
    tool_id: str
    question: str
    options: list
    selected: int = 0
    answered: object = None  # for the app's own question, such as a confirmation: called with the option's index, or None


# What the page gives App to talk back through: the bridge's members, and what each is for. A host of the TUI provides these (molterm's
# page makes them in agent/server/static/bridge.js); App refuses a bridge without one of BRIDGE's, and the tests keep both lists to
# the members the app calls.
BRIDGE = {
    "write": "write(text): to the terminal, as a program's output",
    "post_message": "post_message(text, cols): what the user typed, for the agent",
    "post_tool_result": "post_tool_result(id, json): the answer to one of the agent's TUI tools",
    "interrupt": "interrupt(): stop the agent's turn",
    "edit": "edit(json): molterm's editor, for a molecule edited where it is; answered by edit_answer(text) or edit_failed(message)",
    "picture": "picture(request): a molecule's picture with its annotations; answered by picture_answer(request, text) or "
               "picture_failed(request, message)",
}
BRIDGE_OPTIONAL = {
    "copy": "copy(text): to the clipboard, for the selection menu's Copy; without it, the app writes OSC 52 for the terminal",
    "tutor": "tutor(): tutorial mode for this session, which has no tutor yet (/tutor); without it, /tutor says how to have one",
}


class App:
    def __init__(self, bridge, cols=80, rows=24):
        missing = [name for name in BRIDGE if not hasattr(bridge, name)]
        if missing:
            raise TypeError("the bridge has no " + ", ".join(missing) + "; App needs: " + "; ".join(BRIDGE.values()))
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
        self.smiles_shown = True  # each molecule's SMILES under its picture, painted where it's highlighted; the editor's checkbox, or /smiles
        self.tutorial = False  # the session has a tutor, whose tips are shown unless /tutor hid them
        self.tutor_shown = True
        self.suggestion = None  # what the agent or the tutor suggests typing next: {"text", "by"}; Tab takes it
        self.highlights = []  # text marked in the transcript
        self.selecting = None  # a drag across the transcript: [row, col, row, col] on the screen
        self.expansions = {}  # a context's tag in the input -> what the message carries instead, see tag()
        self.chips = []  # the contexts waiting above the input, see Context
        self.bubbles = {}  # a bubble's key -> how the user left it: {"at" (rows, cols from its anchor), "min", "gone"}
        self._bubble_places = {}  # a bubble's key -> (top, left, anchor row, anchor col), as last drawn
        self._routes, self._gap = {}, None  # the lines found to bubbles, kept while what they cross doesn't change
        self.dragging = None  # a bubble being dragged
        self._bubble_hits, self._chip_hits = [], []
        self.flash = 0  # ticks left of a question's pulse, see _refuse
        self.chevron = 0  # ticks left of the chevrons pointing down at it, see _refuse
        self._refusals = 0  # clicks elsewhere while it has waited
        self.clock = time.monotonic  # what pauses and holds are timed by
        self.menu = None  # a context menu: {"row", "col", "items": [(label, action)], "hover"}
        self.writing = None  # a note being written in its bubble: {"key" (the bubble's), "text", "save"}
        self.handles = None  # a highlight's selection being changed: {"from", "to" (content line, col), "old", "drag"}
        self._pressed = (0.0, None)  # the last left press, (when, where), for a double click
        self.notice = None  # a word on the line under the input for a moment, (text, until): what was copied
        self._line_blocks = []  # the block each content line belongs to
        self._knobs, self._menu_at = {}, None
        self._option_rows, self._option_hits = {}, {}  # a question's options: footer line -> option, and screen row -> option

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
        """Called a few times a second, to turn the spinner, fade a running tool's dot and pulse a question's box."""
        if self.flash:
            self.flash -= 1
        if self.chevron:
            self.chevron -= 1
            self.draw()
        if self.notice and self.clock() >= self.notice[1]:
            self.notice = None
            self.draw()
        if self.busy or self.question or self.flash or any(b.kind == "tool" and b.state == "running" for b in self.blocks):
            self.frame += 1
            self.draw()

    def _refuse(self, click=False):
        """Something other than the question (or a note being written) was clicked or typed while it waits: its box pulses, and the
        line where it was asked, to say where the answer goes. From the third click on, every other one also sends large chevrons
        down the middle of the screen, towards it."""
        self.flash = FLASH_FRAMES
        if click:
            self._refusals += 1
            if self._refusals >= 3 and self._refusals % 2 == 1:
                self.chevron = CHEVRON_FRAMES

    def _flash_level(self):
        return abs(math.sin(2 * math.pi * (FLASH_FRAMES - self.flash + 1) / FLASH_FRAMES)) if self.flash else 0.0

    def wheel(self, lines):
        """The transcript scrolled by `lines`, positive back through it.

        A terminal that reports the mouse sends the wheel through `key` like any other input, and `_mouse` handles it. A page whose
        terminal is one panel among others may keep the wheel for itself instead, and scroll the transcript by calling this. While a
        molecule is being edited the editor has the screen, and its drawing stays put, so the transcript doesn't move under it.
        """
        if self.editing:
            return
        self._scroll_by(lines)
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
        self.tutorial = bool(event.get("tutor"))

    def _on_tutor(self, event):
        text = str(event.get("text") or "").strip()
        if text:
            self._close_text()
            self.blocks.append(Block("tutor", text))
            self.scroll = 0

    def _on_suggest(self, event):
        text = " ".join(str(event.get("text") or "").split())[:500]
        self.suggestion = {"text": text, "by": "tutor" if event.get("by") == "tutor" else "agent"} if text else None

    def _suggested(self):
        """The suggestion, if there is one to show: the tutor's only while its tips are, and only while the input is empty or is
        how the suggestion begins."""
        s = self.suggestion
        if not s or (s["by"] == "tutor" and not self.tutor_shown) or "\n" in self.input:
            return None
        if self.input and (not s["text"].startswith(self.input) or s["text"] == self.input):
            return None
        return s

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
                molecule.paint = {"smiles": (str(smiles).split() or [""])[0], "marks": []}  # molterm paints its highlights
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
        self.question, self._refusals = Question(block.tool_id, str(block.input.get("question", "")), options), 0

    def _answer(self, index):
        question, self.question = self.question, None
        self._refusals = 0
        if question.answered:
            question.answered(index)
            return
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
        if isinstance(answer.get("paint"), dict):
            molecule.paint = answer["paint"]
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
        rows_wanted = (max(molecule.rows, INLINE_ROWS_MIN) + 1 + len(self._smiles_lines(molecule.paint, self.cols - 5)) if mode == "inline"
                       else molecule.rows + 3)
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
        below = 1 + len(self._smiles_lines(e.get("paint") or e["molecule"].paint, self.cols - 5))  # the line that resizes it, and the SMILES
        room = self.rows - len(self._footer(self.cols - 1))
        top = min(max(top if top is not None else above, above), max(room - 4 - below, 0))
        e["rows"] = max(min(e["rows"], room - top - below), 4)
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
                      "events": e["pending"].encode().hex(), "show_smiles": "1" if self.smiles_shown else "0"}
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
            if match.group(1) == "notes":  # the notes made while editing, for bubbles beside the drawing now
                try:
                    told = json.loads(base64.b64decode(match.group(2) or ""))
                    self.editing["notes"], self.editing["edge"] = list(told["notes"]), int(told["edge"])
                except (ValueError, KeyError, TypeError):
                    pass
                return ""
            if match.group(1) == "tools":
                self.tools_shown = match.group(2) == "1"
                return ""
            if match.group(1) == "smiles":  # shown or not, which is remembered; inline, the drawing's SMILES as it's edited too
                try:
                    told = json.loads(base64.b64decode(match.group(2) or ""))
                    self.smiles_shown = bool(told["shown"])
                    if "smiles" in told:
                        self.editing["paint"] = {"smiles": str(told["smiles"]), "marks": list(told.get("marks") or [])}
                except (ValueError, KeyError, TypeError):
                    pass
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

    def _scroll_by(self, lines):
        """Back through the transcript by `lines`, forward again if negative. draw() clamps it to the transcript there is."""
        self.scroll = max(self.scroll + int(lines), 0)

    def _key(self, key):
        if key.name == "mouse":
            self._mouse(*key.mouse)
            return
        if key.name in ("pageup", "pagedown"):
            step = max(self.rows - 6, 1)
            self._scroll_by(step if key.name == "pageup" else -step)
            return
        if key.name == "ctrl-l":
            self.bridge.write("\x1b[2J")
            return
        if self.menu:
            self._menu_key(key)
        elif self.writing:
            self._writing_key(key)
        elif self.question:
            self._question_key(key)
        elif self.handles and key.name in ("enter", "escape"):
            self._end_handles(keep=key.name == "enter")
        else:
            self._input_key(key)

    def _mouse(self, button, col, row, pressed):
        if button in (64, 65):  # the wheel
            self._scroll_by(3 if button == 64 else -3)
            return
        if self.dragging:  # a bubble, moved with the pointer until let go
            d = self.dragging
            state = self.bubbles.setdefault(d["key"], {})
            state["at"] = [d["at"][0] + row - d["row"], d["at"][1] + col - d["col"]]
            if not pressed and not button & 32:
                self.dragging = None
            return
        if self.menu:  # it has the mouse until one of its items is chosen, or a press elsewhere closes it
            if pressed and button == 0:
                menu = self.menu
                knob = next((name for name, (r, c) in self._knobs.items() if r == row and abs(c - col) <= 1), None)
                if menu.get("handles") and knob:  # a selection's knob: the menu waits until it is let go
                    self.menu, self.handles["drag"] = None, knob
                    return
                chosen, self.menu = self._menu_item_at(row, col), None
                if chosen is not None:
                    menu["items"][chosen][1]()
                elif menu.get("handles"):
                    self.handles = None  # a press elsewhere: the selection goes with its menu
            return
        if self.writing and pressed and button in (0, 2, 16):  # a note being written: a press anywhere but on it keeps it, then acts
            if self._bubble_at(row, col) == self.writing["key"]:
                return
            self._end_writing(keep=True)
        if self.question:  # it has the keys and the mouse until answered: a click on an option answers, any other pulses
            if pressed and button in (0, 2):
                if self.question and button == 0 and row in self._option_hits:
                    self._answer(self._option_hits[row])
                else:
                    self._refuse(click=True)
            return
        if self.handles:
            self._handles_mouse(button, col, row, pressed)
            return
        if button in (2, 16):  # the right button, or ctrl+click (a Mac's right click)
            if pressed:
                self._right_click(row, col)
            return
        if button == 32 and self.selecting:  # dragging, the left button held
            self.selecting["to"] = (self._start + row, col)
            return
        if button != 0:
            return
        if pressed and self._press_controls(row, col):
            return
        if pressed:
            for number, (top, left, rows, cols) in self.placed.items():
                if top <= row < top + rows and left <= col < left + cols:
                    self.edit(number)
                    return
            at, now = (self._start + row, col), self.clock()
            last, where = self._pressed
            double = where is not None and now - last <= DOUBLE and where[0] == at[0] and abs(where[1] - at[1]) <= 1
            self._pressed = (0.0, None) if double else (now, at)
            self.selecting = {"from": at, "to": at, "words": double} if row < self._room else None
            return
        selection, self.selecting = self.selecting, None
        if not selection:
            return
        selection["to"] = (self._start + row, col)
        if selection["from"] == selection["to"] and not selection["words"]:  # a click: nothing selected
            return
        self._selection_menu(selection)

    def _selection_menu(self, selection):
        """Let go of a selection: it gets handles at once, a knob above its first letter and one below its last, and under it a menu
        asks what it is for: to copy it, note it (which highlights it first), or highlight it (which also puts it above the input as
        a context). Dragging a knob puts the menu away until it is let go; esc, or a press elsewhere, lets the selection go."""
        found = self._selection_spans(selection)
        copied = self._selection_text(found)
        letters = [(index, i) for _, spans in found for index, a, b in spans for i in range(a, b) if self._plain(index)[i] != " "]
        if not copied.strip() or not letters:
            self.handles = None
            return
        self.handles = {"from": letters[0], "to": letters[-1], "old": [], "drag": None, "selection": True}

        def highlight():
            return self._mark_spans(found, text=_normal(copied))

        def note():
            made = highlight()
            if made:
                self._note_text(made[0])

        def done(action):
            def run():
                self.handles = None
                action()
            return run
        line, last = letters[-1]
        items = [("Copy", done(lambda: self._copy(copied))), ("Add a note…", done(note)), ("Highlight", done(highlight))]
        self.menu = {"row": line - self._start + 1, "col": last, "items": items, "hover": 0, "handles": True}  # under the lower knob

    def _selection_text(self, found):
        """What a selection covers, as text to copy: a paragraph's wrapped lines joined again, other lines (a tool's, a panel's) kept
        as lines, and pictures left out."""
        parts = []
        for block, spans in found:
            rows = []
            for index, a, b in spans:
                row = "".join(t for _, t in cells(self._content_lines[index])[a:b] if not t.startswith(kitty.PLACEHOLDER))
                rows.append(row.rstrip())
            prose = getattr(block, "kind", "") in ("assistant", "user")
            parts.append((" " if prose else "\n").join(r.strip() if prose else r for r in rows))
        return "\n".join(parts).strip("\n")

    def _copy(self, text):
        """To the clipboard: through the page (`bridge.copy`), or else OSC 52, which a terminal that allows it takes for itself."""
        copy = getattr(self.bridge, "copy", None)
        if copy:
            copy(text)
        else:
            self.bridge.write("\x1b]52;c;" + base64.b64encode(text.encode()).decode() + "\x07")
        lines = text.count("\n") + 1
        self.notice = (f"copied {len(text)} characters" + (f", {lines} lines" if lines > 1 else ""), self.clock() + 2.5)

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
                    top, left, anchor_row, anchor_col = self._bubble_places[key]
                    self.dragging = {"key": key, "row": row, "col": col, "at": [top - anchor_row, left - anchor_col]}
                return True
        return False

    def _bubble_at(self, row, col):
        index = self._start + row
        for key, part, line, start, end in reversed(self._bubble_hits):
            if line == index and start <= col < end:
                return key
        return None

    def _selection_spans(self, selection):
        """The text a selection covers, block by block, in reading order: [(block, [(content line, start col, end col)])]. Letter by
        letter; with `words` (a double click, and a drag after it), its ends take in the whole words they are in."""
        (i1, c1), (i2, c2) = sorted([tuple(selection["from"]), tuple(selection["to"])])
        found = []
        for index in range(max(i1, 0), min(i2, len(self._owners) - 1) + 1):
            owner = self._owners[index]  # text that can be highlighted starts after its "● " or "> "; any other line, from its edge
            block, text_col = owner if owner else (self._line_blocks[index], 0)
            line = self._plain(index)
            used = len(line.rstrip())
            if not owner and line.strip() and set(line.strip()) <= set("╭╮╰╯─"):  # a box's top or bottom: nothing to select
                continue
            if not owner and line.startswith("│ ") and line.rstrip().endswith(" │"):  # inside a box (the banner): not its sides
                text_col, used = 2, len(line.rstrip()[:-1].rstrip())
            a = max(c1 if index == i1 else 0, text_col)
            b = min(c2 + 1 if index == i2 else used, used)
            if selection.get("words"):
                if index == i1:
                    if a < used and line[a] != " ":
                        while a > text_col and line[a - 1] != " ":
                            a -= 1
                    else:
                        while a < b and line[a] == " ":
                            a += 1
                if index == i2:
                    if b > a and line[b - 1] != " ":
                        while b < used and line[b] != " ":
                            b += 1
                    else:
                        while b > a and line[b - 1] == " ":
                            b -= 1
            if b > a:
                if found and found[-1][0] is block:
                    found[-1][1].append((index, a, b))
                else:
                    found.append((block, [(index, a, b)]))
        return found

    def _mark_spans(self, found, old=None, text=None):
        """Highlight what a selection covered, a highlight in each block whose text can be highlighted, one group, and put it above the
        input as a context. With `old`, the highlights it changes: their colour, note and group are kept. With `text`, the context is
        that (all the selection, a tool's lines too), and goes above the input even if nothing in it could be highlighted."""
        content, like = self._content_lines, old[0] if old else None
        gid = like.gid if like else next(_KEYS)
        made = []
        for block, spans in found:
            phrase = _normal(" ".join("".join(t for _, t in cells(content[i])[a:b] if not t.startswith(kitty.PLACEHOLDER))
                                      for i, a, b in spans))
            if len(phrase) >= 2:
                made.append(Highlight(block, phrase, like.colour if like else "blue", by=like.by if like else "user", group=gid))
        if not made and not text:
            return []
        noted = next((h for h in old or () if h.note), None)
        if noted and made:
            made[0].note, made[0].note_by, made[0].key = noted.note, noted.note_by, noted.key  # its bubble keeps its place
        self.highlights += made
        text = text or " … ".join(h.phrase for h in made)
        self._add_context(Context(quoted(text).strip(), quoted(text).strip(), f"“{truncate(text, 36)}”", ("text", gid)))
        return made

    def _group(self, highlight):
        return [h for h in self.highlights if h.gid == highlight.gid]

    def _remove_group(self, highlight):
        for h in self._group(highlight):
            self.highlights.remove(h)
        for ctx in [c for c in self.chips if c.group == ("text", highlight.gid)]:
            self._drop_context(ctx)

    def _confirm_remove_mine(self):
        """More than one: asked first, in the input's place, as the agent's questions are."""
        count = len({h.gid for h in self.highlights if h.by == "user"})
        self.question = Question("", f"Remove all {count} of your highlights?", [f"Remove {count} highlights", "Keep them"], 1,
                                 lambda index: self._remove_mine() if index == 0 else None)

    def _remove_mine(self):
        for h in [h for h in self.highlights if h.by == "user"]:
            if h in self.highlights:
                self._remove_group(h)

    def _owner_at(self, row):
        """The block a screen row shows, if it is text that can be marked: (block, the column its text starts at)."""
        index = self._start + row
        if not 0 <= row < self._room or not 0 <= index < len(self._owners):
            return None
        return self._owners[index]

    def _highlight_at(self, row, col, anyone=False):
        index = self._start + row
        for highlight, line, start, end in self._painted:
            if line == index and start <= col < end and (anyone or highlight.by == "user"):
                return highlight
        return None

    # -- the right button: a highlight's menu (and its handles), a bubble's menu -------------------------------------------------------------

    def _right_click(self, row, col):
        """A right click on a bubble or a highlight opens its menu."""
        key = self._bubble_at(row, col)
        if key:
            self._bubble_menu(key, row, col)
            return
        found = self._highlight_at(row, col, anyone=True)
        if found:
            self._highlight_menu(found, row, col)

    def _highlight_menu(self, highlight, row, col):
        group = self._group(highlight)
        noted = next((h for h in group if h.note), None)
        items = []
        if highlight.by == "user":
            items.append(("Change the selection", lambda: self._start_handles(highlight)))
        items.append(("Edit the note…" if noted else "Add a note…", lambda: self._note_text(noted or group[0])))
        items.append(("Put it in my message", lambda: self._quote_highlight(group)))
        items.append(("Remove the highlight", lambda: self._remove_group(highlight)))
        if len({h.gid for h in self.highlights if h.by == "user"}) > 1:
            items.append(("Remove all my highlights", self._confirm_remove_mine))
        self.menu = {"row": row, "col": col, "items": items, "hover": 0}

    def _bubble_menu(self, key, row, col):
        if key[0] == "text":
            highlight = next((h for h in self.highlights if h.key == key[1]), None)
            if highlight is None:
                return
            items = [("Edit the note…", lambda: self._note_text(highlight)), ("Put it in my message", lambda: self._quote_note(highlight))]
        else:
            molecule = self._molecule(key[1])
            if molecule is None or (self.editing and self.editing["molecule"] is molecule):
                return  # being edited: its notes are the editor's
            note = next((n for n in molecule.notes if (str(n.get("atom")), str(n.get("bond")), str(n.get("text"))) == key[2:]), None)
            if note is None:
                return
            items = [("Edit the note…", lambda: self._note_molecule(molecule, note)),
                     ("Put it in my message", lambda: self._quote_molecule_note(molecule, note))]
        self.menu = {"row": row, "col": col, "items": items, "hover": 0}

    def _menu_box(self):
        """Where the open menu is drawn: (top row, left col, its width inside)."""
        items = self.menu["items"]
        inner = max(width(label) for label, _ in items) + 2
        top = min(self.menu["row"] + 1, max(self.rows - len(items) - 2, 0))
        left = min(self.menu["col"], max(self.cols - 1 - inner - 2, 0))
        return top, left, inner

    def _menu_item_at(self, row, col):
        top, left, inner = self._menu_box()
        if top < row <= top + len(self.menu["items"]) and left <= col < left + inner + 2:
            return row - top - 1
        return None

    def _menu_key(self, key):
        menu = self.menu
        if key.name in ("up", "down", "tab"):
            menu["hover"] = (menu["hover"] + (-1 if key.name == "up" else 1)) % len(menu["items"])
        elif key.name == "enter":
            self.menu = None
            menu["items"][menu["hover"]][1]()
        elif key.name in ("escape", "ctrl-c"):
            self.menu = None
            if menu.get("handles"):
                self.handles = None

    # -- writing a note --------------------------------------------------------------------------------------------------------------

    def _note_text(self, highlight):
        """Write the highlight's note where it hangs, in its bubble (an empty one, for a new note)."""
        def save(text):
            highlight.note, highlight.note_by = text, "user"
        self._write(("text", highlight.key), highlight.note, save)

    def _note_molecule(self, molecule, note):
        """Rewrite a note on a molecule in its bubble, beside the picture; molterm puts it in the picture's CXSMILES."""
        target = {"atom": note["atom"]} if note.get("atom") is not None else {"bond": note.get("bond")}

        def save(text):
            self._repicture(molecule, {"edit": True, **target, "note": text, "by": "user"})
        key = ("mol", molecule.number, str(note.get("atom")), str(note.get("bond")), str(note.get("text")))
        self._write(key, str(note.get("text") or ""), save)

    def _write(self, key, text, save):
        state = self.bubbles.setdefault(key, {})
        state.pop("gone", None), state.pop("min", None)  # shown, if it was folded or hidden
        self.writing = {"key": key, "text": text or "", "save": save}

    def _writing_key(self, key):
        """Typing in the bubble: enter keeps the note (an empty one takes it off), shift+enter starts a new line, esc cancels."""
        writing = self.writing
        text = writing["text"]
        if key.name in ("text", "paste") and not key.alt:
            text += key.text if key.name == "paste" else key.text.replace("\n", " ")
        elif key.name == "shift-enter" or (key.name == "enter" and key.alt):  # alt+enter too, for a terminal that can't tell shift
            text += "\n"
        elif key.name == "backspace":
            text = text[:-1]
        elif key.name == "ctrl-u":
            text = ""
        elif key.name == "enter":
            self._end_writing(keep=True)
            return
        elif key.name in ("escape", "ctrl-c"):
            self._end_writing(keep=False)
            return
        lines = text.split("\n")
        writing["text"] = "\n".join(lines[:NOTE_LINES])[:NOTE_MAX]

    def _end_writing(self, keep):
        writing, self.writing = self.writing, None
        if keep:
            writing["save"](_clean_note(writing["text"]))

    def _flash_colours(self):
        """A modal box's border and the tint inside it: pulsing, brightening towards white and warming, then settled."""
        level = self._flash_level()
        if not level:
            return ACCENT, ""
        return _rgb(_mix((215, 119, 87), (255, 244, 230), level)), _rgb(_mix((0, 0, 0), (70, 40, 30), level), background=True)

    # -- putting a highlight or a note into the message ------------------------------------------------------------------------------

    def _put_in_message(self, ctx):
        self._add_context(ctx)
        if ctx.tag not in self.input:
            self._toggle_context(ctx)

    def _quote_highlight(self, group):
        text = " … ".join(h.phrase for h in group)
        note = next((h.note for h in group if h.note), "")
        full = f"⟦highlighted: \"{text}\"" + (f", with the note \"{note}\"" if note else "") + "⟧"
        self._put_in_message(Context(quoted(text).strip(), full, f"“{truncate(text, 36)}”", ("text", group[0].gid)))

    def _quote_note(self, highlight):
        note = _normal(highlight.note)  # its lines as one, for the input's text and the chip
        tag = f"⟦note: \"{truncate(note, 40)}\"⟧"
        self._put_in_message(Context(tag, f"⟦the note \"{highlight.note}\" on \"{highlight.phrase}\"⟧", f"note “{truncate(note, 30)}”"))

    def _quote_molecule_note(self, molecule, note):
        text = str(note.get("text") or "")
        where = f"atom {note['atom']}" if note.get("atom") is not None else f"the bond between atoms {note.get('bond')}"
        tag = f"⟦note on mol {molecule.number}: \"{_normal(text)}\"⟧"
        full = f"⟦the note \"{text}\" on {where} of mol {molecule.number} {molecule.name}: {molecule.smiles}⟧"
        self._put_in_message(Context(tag, full, f"mol {molecule.number} note “{truncate(_normal(text), 24)}”"))

    # -- a highlight's handles ---------------------------------------------------------------------------------------------------------

    def _start_handles(self, highlight):
        """Show the user's highlight as a selection with a handle above its start and below its end, to be dragged."""
        group = self._group(highlight)
        spots = [(line, start, end) for h, line, start, end in self._painted if any(h is g for g in group)]
        if not spots:
            return
        first, last = min(spots), max(spots, key=lambda s: (s[0], s[2]))
        self.highlights = [h for h in self.highlights if not any(h is g for g in group)]
        self.handles = {"from": (first[0], first[1]), "to": (last[0], last[2] - 1), "old": group, "drag": None}

    def _handles_mouse(self, button, col, row, pressed):
        handles = self.handles
        if button == 32 and handles["drag"]:  # the knob is a row off the text: above the first letter, below the last
            index = self._start + row
            if handles["drag"] == "from":  # the start stays at or before the end, the selection a letter at the least
                handles["from"] = min((index + 1, col), tuple(handles["to"]))
            else:
                handles["to"] = max((index - 1, col), tuple(handles["from"]))
            return
        if button != 0:
            return
        if pressed:
            for name, (r, c) in self._knobs.items():
                if r == row and abs(c - col) <= 1:
                    handles["drag"] = name
                    return
            self._end_handles(keep=True)  # a press elsewhere is done with them
            return
        if handles["drag"]:  # let go: the knobs snap back to the selection's first and last letters, wherever the pointer was
            letters = [(index, i) for _, spans in self._selection_spans(handles) for index, a, b in spans
                       for i in range(a, b) if self._plain(index)[i] != " "]
            if letters:  # spaces at either end aren't letters to snap to
                handles["from"], handles["to"] = letters[0], letters[-1]
            if handles.get("selection"):  # and a selection's menu is back, under it
                self._selection_menu(handles)
                return
        handles["drag"] = None

    def _end_handles(self, keep):
        handles, self.handles = self.handles, None
        if handles.get("selection"):  # a selection, not yet a highlight: its menu again
            if keep:
                self._selection_menu(handles)
            return
        old = handles["old"]
        if keep and self._mark_spans(self._selection_spans({"from": handles["from"], "to": handles["to"]}), old):
            return
        self.highlights += old  # undone, or it covers nothing now

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
        elif key.name in ("text", "paste", "backspace", "delete"):  # typing, which has nowhere to go
            self._refuse()

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
        elif (key.name == "enter" and key.alt) or key.name == "shift-enter":  # a new line, as in a note
            self.input = self.input[:self.cursor] + "\n" + self.input[self.cursor:]
            self.cursor += 1
        elif key.name == "enter":
            self._submit()
        elif key.name == "tab" and self._suggested():
            self.input = self._suggested()["text"]
            self.cursor, self.history_at = len(self.input), None
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
        if text == "/tutor":
            start = getattr(self.bridge, "tutor", None)
            if self.tutorial:
                self.tutor_shown = not self.tutor_shown
                self.blocks.append(Block("note", "The tutor's tips are shown again." if self.tutor_shown
                                         else "The tutor's tips are hidden; /tutor shows them again."))
            elif start:  # tutorial mode from here on, whatever the agent: the server starts its tutor, and says so (_on_session)
                self.tutor_shown = True
                start()
                self.blocks.append(Block("note", "Starting the tutor: it watches from here on. /tutor hides it."))
            else:
                self.blocks.append(Block("note", "There's no tutor in this session: open the page as /?tutorial for one."))
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
        shown = re.fullmatch(r"/smiles(?:\s+(on|off))?", text)
        if shown:
            self.smiles_shown = {"on": True, "off": False}.get(shown.group(1), not self.smiles_shown)
            return
        notes = re.fullmatch(r"/notes(?:\s+(hide|reset))?", text)
        if notes:
            for state in self.bubbles.values():
                if notes.group(1) == "hide":
                    state["min"] = True
                else:
                    state["min"] = state["gone"] = False
                if notes.group(1) == "reset":
                    state.pop("at", None)
            return
        if text == "/clear":
            self.blocks, self.molecules, self.placed, self.highlights = [Block("banner")], [], {}, []
            self.bridge.write("".join(kitty.delete(n) for n in range(1, self.images + 1)))  # the pictures go with their placeholders
            return
        self.suggestion = None  # a message sent: the suggestion was taken, or passed over (a command of the app's own keeps it)
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
                shade = HIGHLIGHTS.get(highlight.colour, HIGHLIGHTS["yellow"]) + (USER_INK if highlight.by == "user" else "")
                out[line] = paint(out[line], prefix + start, prefix + end, shade)  # the user's own in their blue, as their notes are
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
            if self.tutorial:
                rows[4:4] = ["", style("  Tutorial mode: a tutor watches and chimes in, ", GREY) + style(f"{TUTOR_MARK} tutor", TUTOR_INK)]
            border = style("╭" + "─" * (cols - 2) + "╮", ACCENT)
            lines = [border, *(style("│ ", ACCENT) + pad(row, inner) + style(" │", ACCENT) for row in rows),
                     style("╰" + "─" * (cols - 2) + "╯", ACCENT), "",
                     style(truncate(" Tips: type a message for the agent and press enter. /help for help.", cols), GREY), ""]
            return (*self._paint_shown(block, lines), None)
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
            return (*self._paint_shown(block, self._tool_lines(block, cols)), None)
        if block.kind == "tutor":
            return (*self._paint_shown(block, _tutor_lines(block.text, cols) if self.tutor_shown else []), None)
        return [], [], None

    def _paint_shown(self, block, lines):
        """Highlights on lines drawn as they are (a tool's, the banner), found in what they show: the lines, and where each was painted."""
        shown, painted = [strip(line) for line in lines], []
        for highlight in (h for h in self.highlights if h.block is block):
            for line, start, end in _find(shown, highlight.phrase):
                shade = HIGHLIGHTS.get(highlight.colour, HIGHLIGHTS["yellow"]) + (USER_INK if highlight.by == "user" else "")
                lines[line] = paint(lines[line], start, end, shade)
                painted.append((highlight, line, start, end))
        return lines, painted

    def _tool_lines(self, block, cols):
        mark = {"running": style("●", _rgb(_mix((80, 80, 80), (236, 236, 236), _fade(self.frame)))),
                "done": style("●", GREEN), "error": style("●", RED)}[block.state]
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
            if molecule and e and e["mode"] == "inline" and e["molecule"] is molecule:  # room for its drawing, the line under it, and
                # the SMILES of the drawing as it's edited
                panel = ([""] * e["rows"] + [_grip(e["cols"], "drag this line, or alt+arrows, to resize")]
                         + self._smiles_lines(e.get("paint") or molecule.paint, cols - 5) + panel[molecule.rows:])
            elif molecule:
                panel = panel[:molecule.rows] + self._smiles_lines(molecule.paint, cols - 5) + panel[molecule.rows:]
            body += ([title] if title else []) + [truncate(line, cols - 5) for line in panel]
        if block.result:
            tone = (RED,) if block.state == "error" else (GREY,)
            body += [style(truncate(line, cols - 5), *tone) for line in block.result]
        if block.state == "running" and not body:
            body = [style("Waiting for your answer…" if block.name == "tui.ask" else "Running…", GREY)]
        lines += [style("  └  ", GREY) + line if i == 0 else "     " + line for i, line in enumerate(body)]
        if self.flash and self.question and block.tool_id == self.question.tool_id:  # where it was asked pulses with its box
            _, paper = self._flash_colours()
            lines = [paint(line, 0, cols, paper) for line in lines]
        return lines + [""]

    def _smiles_lines(self, painted, cols):
        """A molecule's SMILES, for under its picture: on as many lines as it takes (four at most), the parts highlighted on the
        molecule painted in their colours. None if it's hidden."""
        smiles = str((painted or {}).get("smiles") or "")
        if not self.smiles_shown or not smiles:
            return []
        cols = max(cols, 10)
        chunks = [smiles[at:at + cols] for at in range(0, len(smiles), cols)]
        if len(chunks) > SMILES_LINES:
            chunks = chunks[:SMILES_LINES]
            chunks[-1] = chunks[-1][:-1] + "…"
        lines = []
        for index, chunk in enumerate(chunks):
            line, offset = style(chunk, GREY), index * cols
            for start, end, colour in (painted.get("marks") or []):
                start, end = max(int(start) - offset, 0), min(int(end) - offset, len(chunk))
                if start < end:
                    line = paint(line, start, end, HIGHLIGHTS.get(colour, HIGHLIGHTS["yellow"]) + WHITE)
            lines.append(line)
        return lines

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
        suggested = self._suggested()
        for i, line in enumerate(shown):
            prefix = "> " if i == 0 and len(shown) == len(text_lines) else "  "
            content = line if self.input else style("Message the agent", GREY, ITALIC)
            if suggested and i == len(shown) - 1:  # faint, after what's typed: the rest of it, which Tab takes
                rest = suggested["text"][len(self.input):]
                content = (line if self.input else "") + style(rest, GREY)
                if suggested["by"] == "tutor":
                    content += style(f"  {TUTOR_MARK} tutor", TUTOR_INK)
            row = style("│ ", GREY) + pad(prefix + content, inner) + style(" │", GREY)
            for tag_text in tags:  # a context's tag stands out from the typing
                for found, start, end in _find(shown, tag_text):
                    if found == i:
                        row = paint(row, 4 + start, 4 + end, TAG)
            box.append(row)
        box.append(style("╰" + "─" * (cols - 2) + "╯", GREY))
        hint = "  ? /help for shortcuts" if not self.scroll else f"  scrolled back {self.scroll} lines · pgdn to return"
        if self.selecting:
            hint = "  whole words · let go for its menu" if self.selecting["words"] else "  let go for its menu · double-click and drag: whole words"
        elif self.notice and self.clock() < self.notice[1]:
            hint = "  " + self.notice[0]
        elif self.writing:
            hint = "  enter keeps the note · shift+enter a new line · esc cancels"
        elif self.handles:
            hint = ("  drag ● to change it · choose from its menu · esc lets it go" if self.handles.get("selection")
                    else "  drag ● to change it · enter or a click keeps it · esc undoes")
        elif suggested and not self.scroll:
            hint = "  tab takes the tutor's suggestion" if suggested["by"] == "tutor" else "  tab takes the suggestion"
        right = f"session {self.session[:8]}" if self.session else "connecting…"
        hint_line = style(pad(hint, cols - width(right) - 2) + right, GREY)
        return lines + box + [hint_line]

    def _question_lines(self, cols):
        question, inner = self.question, cols - 4
        rows = [style(line, BOLD) for line in wrap(question.question, inner)] + [""]
        self._option_rows = {}
        for i, option in enumerate(question.options):
            chosen = i == question.selected
            text = f"{'❯' if chosen else ' '} {i + 1}. {option}"
            self._option_rows[1 + len(rows)] = i
            rows.append(style(text, ACCENT) if chosen else text)
        edge, paper = self._flash_colours()  # pulsing: its border brightens towards white and its inside warms, twice, then settles
        box = [style("╭" + "─" * (cols - 2) + "╮", edge)]
        box += [style("│ ", edge) + (paint(pad(row, inner), 0, inner, paper) if paper else pad(row, inner)) + style(" │", edge)
                for row in rows]
        box.append(style("╰" + "─" * (cols - 2) + "╯", edge))
        return box + [style("  ↑↓ to choose · enter or a click answers · a number picks · esc to skip", GREY)]

    def _bubbles(self, cols):
        """What is drawn over the transcript: [(content line, col, text, interior)], for the molecules' notes and the highlights'
        notes, where `interior` is (its colours, the span of a bubble's row that may show faintly what it covers). A bubble goes
        where it covers the least text, near what it points at, unless the user dragged it; a line in text joins it to what it
        points at, wherever
        it is. `_bubble_hits` says where each one's parts are, for the mouse."""
        out, self._bubble_hits, self._bubble_places, taken = [], [], {}, []
        e = self.editing
        editing = e["molecule"] if e and e["mode"] == "inline" else None
        groups = []  # (molecule, its notes, the content line their rows count from, the column their leaders end at)
        for molecule in self.molecules:
            base = self._content_at.get(molecule.number)
            if base is None:
                continue
            if molecule is editing:  # the notes as they are being made, from the editor, at the drawing's right edge
                notes = [{"text": n.get("text"), "row": int(n.get("row") or 0) - e["inline"][0], "atom": "edit", "by": n.get("by"),
                          "draft": bool(n.get("draft"))} for n in e.get("notes") or ()]
                groups.append((molecule, notes, base, e.get("edge", EDIT_LEFT + e["cols"])))
            elif molecule.notes:
                groups.append((molecule, molecule.notes, base, 5 + molecule.cols))  # just past the picture, where the leaders end
        for molecule, notes, base, x in groups:
            room = min(max(cols - x - 3 - 4, 12), BUBBLE_TEXT)
            for note in sorted(notes, key=lambda n: int(n.get("row") or 0)):
                draft = bool(note.get("draft"))  # being written in the editor, which has the keys
                key = ("mol", molecule.number, str(note.get("atom")), str(note.get("bond")), "\x00draft" if draft else str(note.get("text")))
                colours = USER if note.get("by") == "user" or draft else AGENT
                state = self.bubbles.setdefault(key, {})
                leader = base + int(note.get("row") or 0)
                writing = self.writing and self.writing["key"] == key
                if not (draft or writing) and state.get("gone"):
                    continue
                if not (draft or writing) and state.get("min"):
                    out.append((leader, x, style("──◆", colours[0]), None))
                    self._bubble_hits.append((key, "open", leader, x, x + 3))
                    continue
                text = self.writing["text"] if writing else str(note.get("text") or "")
                rows = _bubble(text, room, colours, cursor=draft or writing)
                size = width(strip(rows[0]))
                if "at" in state:
                    top, left = leader + state["at"][0], max(min(x + state["at"][1], cols - size), 0)
                else:
                    tops = range(leader - len(rows) + 2, leader + 5)
                    lefts = range(x + 3, max(min(cols - size, x + 15), x + 3) + 1, 3)
                    top, left = self._free_place(tops, lefts, len(rows), size, taken, (leader - 1, x + 3), cols)
                taken.append((top, left, len(rows), size))
                out += self._place_bubble(key, rows, top, left, (leader, x), "left", cols, taken=taken, colours=colours)
        for highlight, line, start, end in self._painted_first:
            key = ("text", highlight.key)
            writing = self.writing and self.writing["key"] == key
            if not (highlight.note or writing):
                continue
            colours = USER if highlight.by == "user" or writing else AGENT
            state = self.bubbles.setdefault(key, {})
            if not writing and state.get("gone"):
                continue
            if not writing and state.get("min"):
                out.append((line, end, style("◆", colours[0]), None))
                self._bubble_hits.append((key, "open", line, end, end + 1))
                continue
            rows = _bubble(self.writing["text"] if writing else highlight.note, min(cols - 8, BUBBLE_TEXT), colours, cursor=writing)
            size = width(strip(rows[0]))
            if "at" in state:
                top, left = line + state["at"][0], max(min(start + state["at"][1], cols - size), 0)
            else:
                tops = [*range(line + 1, line + 7), *range(line - len(rows) - 3, line - len(rows) + 1)]
                lefts = range(max(start - 22, 0), max(min(start + 30, cols - size), 0) + 1, 2)
                top, left = self._free_place(tops, lefts, len(rows), size, taken, (line + 1, max(start - 2, 0)), cols)
            taken.append((top, left, len(rows), size))
            out += self._place_bubble(key, rows, top, left, (line, start), "text", cols, end, taken, colours)
        return out

    def _free_place(self, tops, lefts, height, size, taken, near, cols):
        """Of the places given, the one where a bubble covers the least text and no other bubble, nearest `near` (top, left)."""
        best, best_cost = near, None
        for top in tops:
            for left in lefts:
                if left < 0 or left + size > cols:
                    continue
                cost = abs(top - near[0]) + abs(left - near[1]) / 4
                for other in taken:
                    if top < other[0] + other[2] and other[0] < top + height and left < other[1] + other[3] and other[1] < left + size:
                        cost += 1000
                for row in range(top, top + height):
                    cost += 10 * sum(1 for c in self._plain(row)[left:left + size] if c != " ")
                if best_cost is None or cost < best_cost:
                    best, best_cost = (top, left), cost
        return best

    def _plain(self, index):
        """A content line as a character a column, for finding where text is ("" past the transcript)."""
        cache = self._plain_lines
        if index not in cache:
            line = self._content_lines[index] if 0 <= index < len(self._content_lines) else ""
            cache[index] = "".join((text[:1] or " ") if text[:1] != kitty.PLACEHOLDER else "▒" for _, text in cells(line))
        return cache[index]

    def _place_bubble(self, key, rows, top, left, anchor, kind, cols, end=None, taken=(), colours=AGENT):
        """The bubble's rows, its parts for the mouse, and the line joining it to its anchor: a molecule's leader at the picture's
        edge, coming from the left, or highlighted text, which it leaves from below, above, or its end."""
        size, height = width(strip(rows[0])), len(rows)
        inner = size - 4
        self._bubble_places[key] = (top, left, *anchor)
        for i in range(height):
            self._bubble_hits.append((key, "drag", top + i, left, left + size))
        self._bubble_hits.append((key, "fold", top, left + inner - 2, left + inner + 1))
        self._bubble_hits.append((key, "hide", top, left + inner + 1, left + inner + 3))
        out = []
        for i, line in enumerate(rows):  # its margins and the room after its text may show faintly what it covers, not between words
            text = strip(line)[2:size - 2].rstrip()
            used = width(text) + (1 if "\x1b[7m" in line else 0)  # a note being written: its cursor isn't room to show through
            out.append((top + i, left, line, (colours, [1, *range(2 + used, size - 1)]) if 0 < i < height - 1 else None))
        row, col = anchor
        tail = min(max(col, left + 1), max(left + size - 7, left + 1))
        if kind == "text" and top == row + 1 and left < col < left + size - 1:  # right under the text: a tail up at it
            return out + [(top, tail, style("┴", colours[1], colours[0]), None)]
        if kind == "text" and top + height == row and left < col < left + size - 1:  # right above it: a tail down
            return out + [(top + height - 1, col, style("┬", colours[1], colours[0]), None)]
        if kind == "left":
            start, entry = (row, col), "left"
        elif top > row + 1:
            start, entry = (row + 1, col), "up"
        elif top + height < row:
            start, entry = (row - 1, col), "down"
        elif left > end:
            start, entry = (row, end), "left"
        else:
            start, entry = (row, max(col - 1, 0)), "right"
        route = self._find_route(start, entry, top, left, height, size, taken, cols)
        if route is None:
            route = _route(start, entry, top, left, height, size)
        return out + [(r, c, style(char, *((colours[1],) if joint else ()), colours[0]), None) for r, c, char, joint in route]

    def _find_route(self, start, entry, top, left, height, size, taken, cols):
        """The line from `start` to the bubble that goes through the least text: blank cells cost least, then punctuation and
        lines, then letters and digits; it doesn't cross bubbles, and crosses a picture or the drawing being edited only if it must.
        [(row, col, character, on the bubble's edge)], or None if there is no way within reach."""
        bottom, right = top + height - 1, left + size - 1
        if top <= start[0] <= bottom and left <= start[1] <= right:
            return []
        r0, r1 = min(start[0], top) - 4, max(start[0], bottom) + 4
        c0, c1 = max(min(start[1], left) - 12, 0), min(max(start[1], right) + 12, cols - 1)
        rows = tuple(self._plain(r)[c0:c1 + 1] for r in range(r0, r1 + 1))
        memo = (start, entry, top, left, height, size, tuple(taken), rows, self._gap)
        if memo in self._routes:
            return self._routes[memo]

        def price(r, c):
            if any(t <= r < t + h and l <= c < l + w for t, l, h, w in taken):
                return None
            if self._gap and r in self._gap[0] and self._gap[1] <= c < self._gap[2]:
                return 600
            text = rows[r - r0]
            char = text[c - c0] if c - c0 < len(text) else " "
            return 1 if char == " " else 600 if char == "▒" else 25 if char.isalnum() else 3
        goals = {}
        for r in range(top + 1, bottom):
            goals[(r, left - 1)], goals[(r, right + 1)] = ("right", (r, left, "┤")), ("left", (r, right, "├"))
        for c in range(left + 1, max(right - 6, left + 1) + 1):
            goals[(top - 1, c)] = ("down", (top - 1 + 1, c, "┴"))
        for c in range(left + 1, right):
            goals[(bottom + 1, c)] = ("up", (bottom, c, "┬"))
        first = price(*start)
        if first is None:
            return None
        ticket = itertools.count()
        heap, best, parent = [(first, next(ticket), start, entry)], {(start, entry): first}, {}
        found = None
        while heap:
            spent, _, cell, came = heapq.heappop(heap)
            if cell == "done":
                found = came
                break
            if spent > best.get((cell, came), 1e18):
                continue
            if cell in goals:
                going, _ = goals[cell]
                total = spent + (0 if going == _OPPOSITE[came] else 4)
                if total < best.get(("done", cell), 1e18):
                    best[("done", cell)] = total
                    parent[("done", cell)] = (cell, came)
                    heapq.heappush(heap, (total, next(ticket), "done", cell))
            for going, (dr, dc) in _SIDES.items():
                if going == came:
                    continue
                r, c = cell[0] + dr, cell[1] + dc
                if not (r0 <= r <= r1 and c0 <= c <= c1):
                    continue
                step = price(r, c)
                if step is None:
                    continue
                total = spent + step + (0 if going == _OPPOSITE[came] else 4)
                state = ((r, c), _OPPOSITE[going])
                if total < best.get(state, 1e18):
                    best[state], parent[state] = total, (cell, came)
                    heapq.heappush(heap, (total, next(ticket), (r, c), _OPPOSITE[going]))
        if found is None:
            self._routes[memo] = None
            return None
        walk, state = [], parent[("done", found)]
        while state:
            walk.append(state)
            state = parent.get(state)
        walk.reverse()
        going, edge = goals[found]
        route = []
        for i, (cell, came) in enumerate(walk):
            out_side = going if i == len(walk) - 1 else _step(cell, walk[i + 1][0])
            route.append((*cell, _LINES.get(frozenset((came, out_side)), "─" if came in ("left", "right") else "│"), False))
        route.append((*edge, True))
        if len(self._routes) > 500:
            self._routes.clear()
        self._routes[memo] = route
        return route

    def lines(self):
        """The screen's lines, top to bottom, and where the cursor goes (row, col), or None to hide it."""
        cols = self.cols - 1  # a margin, so nothing is written in the last column
        content, self._content_at, self._owners, self._line_blocks, painted = [], {}, [], [], []
        for block in self.blocks:
            lines, marks, text_col = self._block_lines(block, cols)
            if block.molecule and block.image_line >= 0:
                self._content_at[block.molecule.number] = len(content) + block.image_line
            painted += [(h, len(content) + line, start, end) for h, line, start, end in marks]
            self._owners += [(block, text_col) if text_col is not None and i < len(lines) - 1 else None for i in range(len(lines))]
            self._line_blocks += [block] * len(lines)
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
        self._option_hits = {room + line - cut: option for line, option in self._option_rows.items() if line >= cut} if self.question else {}
        self.scroll = min(self.scroll, max(len(content) - room, 0))
        end = len(content) - self.scroll
        start = max(end - room, 0)
        self._content_lines, self._start, self._plain_lines = content, start, {}
        shown = content[start:end] if room else []
        e = self.editing
        gap, self._gap = range(0), None
        if e and e["mode"] == "inline" and e["molecule"].number in self._content_at:  # the drawing, its tools and its line
            first = self._content_at[e["molecule"].number]
            gap = range(first - 3, first + e["rows"] + 1)
            self._gap = (gap, EDIT_LEFT, EDIT_LEFT + e["cols"])
        shown += [""] * (room - len(shown))  # the empty rows over the input too, where a bubble may be dragged
        for line, col, text, interior in self._bubbles(cols):  # over the transcript, where they are shown, and beside the drawing
            if start <= line < start + room and col >= 0 and not (line in gap and col < EDIT_LEFT + e["cols"]):
                shown[line - start] = truncate(_cover(shown[line - start], col, text, interior), cols)
        chosen = self.selecting or self.handles
        if chosen:  # a selection being dragged, letter by letter or (double-clicked) whole words, or a highlight's between its handles
            for _, spans in self._selection_spans(chosen):
                for line, a, b in spans:
                    if start <= line < start + room:
                        shown[line - start] = paint(shown[line - start], a, min(b, cols), WORDS if chosen.get("words") else SELECTING)
        self._knobs = {}
        if self.handles:  # as iOS has them: a knob just above the selection's first letter, and one just below its last
            ends = [(tuple(self.handles["from"]), "from"), (tuple(self.handles["to"]), "to")]  # never crossed: see _handles_mouse
            for ((line, col), name), step in zip(ends, (-1, 1)):
                row = line + step - start
                if 0 <= row < room and 0 <= col < cols:
                    shown[row] = overlay(shown[row], col, style("●", USER_INK))
                    self._knobs[name] = (row, col)
        if self.chevron and self.question:  # pointing down at what waits, sliding down as they fade in and out
            art = _chevrons()
            step = CHEVRON_FRAMES - self.chevron
            level = math.sin(math.pi * (step + 1) / (CHEVRON_FRAMES + 1))
            ink = _rgb(_mix((60, 40, 34), (255, 170, 130), level)) + BOLD
            top = max(room - len(art) - 4, 0) // 2 + step // 2
            left = max((cols - width(art[0])) // 2, 0)
            for i, line in enumerate(art):
                if 0 <= top + i < room:
                    for j, char in enumerate(line):
                        if char != " ":
                            shown[top + i] = overlay(shown[top + i], left + j, style(char, ink))
        screen = shown + footer  # the input stays at the bottom, as in Claude Code
        if self.menu:  # over everything
            top, left, inner = self._menu_box()
            items = self.menu["items"]
            box = [style("╭" + "─" * inner + "╮", MENU)]
            box += [style("│", MENU) + style(" " + pad(label, inner - 1), MENU_ON if i == self.menu["hover"] else MENU) + style("│", MENU)
                    for i, (label, _) in enumerate(items)]
            box.append(style("╰" + "─" * inner + "╯", MENU))
            for i, line in enumerate(box):
                if 0 <= top + i < len(screen):
                    screen[top + i] = truncate(overlay(screen[top + i], left, line), cols)
        self._room, self._content = room, len(content)
        self.placed = {}
        for molecule in self.molecules:  # where each molecule's picture is, for a click to find it
            top = self._content_at.get(molecule.number, -10**6) - start
            if top + molecule.rows > 0 and top < len(shown):
                self.placed[molecule.number] = (top, 5, molecule.rows, molecule.cols)
        cursor = None
        if not self.question and not self.editing and not self.menu and not self.writing:  # (a note is written with its own cursor)
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
        # the terminal's cursor where typing goes: solid while the terminal has the keyboard, hollow when it hasn't (restty does that)
        if cursor:
            out += f"\x1b[{cursor[0] + 1};{cursor[1] + 1}H\x1b[?25h"
        return out

    def draw(self):
        if self.started:
            out = self.render()
            if out:
                self.bridge.write(out)


def _find(lines, phrase):
    """Where a phrase is in wrapped lines: [(line, start col, end col)], one for each line it is on. Any run of spaces, line
    breaks, blank lines and indents counts as one space, as in the phrase (see _normal), so a phrase across paragraphs is found."""
    text, places = [], []  # the lines' text with its spaces collapsed, and where each character of it is: (line, index in it)
    for index, line in enumerate(lines):
        for i, char in enumerate(line + " "):
            if char.isspace():
                if text and text[-1] != " ":
                    text.append(" ")
                    places.append((index, i))
            else:
                text.append(char)
                places.append((index, i))
    haystack, needle, spans, at = "".join(text).lower(), _normal(phrase).lower(), [], 0
    while needle:
        found = haystack.find(needle, at)
        if found < 0:
            break
        at = found + len(needle)
        covered = {}
        for k in range(found, at):
            index, i = places[k]
            if i < len(lines[index]):
                first, last = covered.get(index, (i, i))
                covered[index] = (min(first, i), max(last, i))
        for index, (first, last) in sorted(covered.items()):
            line = lines[index]
            spans.append((index, width(line[:first]), width(line[:last + 1])))
    return spans


def _chevrons(size=5):
    """Two large chevrons, one above the other, pointing down: solid strokes two blocks wide, a step in each row."""
    span = 4 * size
    one = []
    for i in range(size):
        row = [" "] * span
        for at in (2 * i, span - 2 - 2 * i):
            row[at:at + 2] = "██"
        one.append("".join(row))
    return one + [" " * span] + one


def _fade(frame):
    """How bright a running tool's dot is at this tick, 0 to 1: it fades out and in again, smoothly, rather than blinking."""
    return (1 + math.cos(2 * math.pi * frame / FADE_FRAMES)) / 2


def _mix(a, b, level):
    return tuple(round(x + (y - x) * level) for x, y in zip(a, b))


def _rgb(colour, background=False):
    return f"\x1b[{48 if background else 38};2;{colour[0]};{colour[1]};{colour[2]}m"


def _bubble(text, room, colours=AGENT, cursor=False):
    """A speech bubble's lines, the text wrapped to `room` columns at most, its line breaks kept: an agent's, or with USER the user's
    own, white letters and controls in a blue bubble. With `cursor`, it is being written: a block after the last letter."""
    ink, paper, _, letters, controls = colours
    lines = wrap(text, room) or [""]
    if cursor and (text.endswith("\n") or text == ""):
        lines = lines if text == "" else [*lines, ""]  # wrap leaves out a line break at the very end, where the cursor is
    if cursor and width(lines[-1]) >= room:
        lines.append("")  # no room left on the line: the cursor starts the next
    inner = max(max(width(line) for line in lines) + (1 if cursor else 0), 6)

    def row(i, line):
        body = pad(line, inner)
        if cursor and i == len(lines) - 1:  # reversed: the paper's colour on the letters' (lighter than the paper)
            body = line + "\x1b[7m \x1b[27m" + " " * (inner - width(line) - 1)
        return paper + ink + "│ " + RESET + paper + letters + body + RESET + paper + ink + " │" + RESET
    return ([style("╭" + "─" * (inner - 3) + " ", paper, ink) + style("– ×", paper, controls) + style(" ╮", paper, ink)]  # fold, hide
            + [row(i, line) for i, line in enumerate(lines)]
            + [style("╰" + "─" * (inner + 2) + "╯", paper, ink)])


def _tutor_lines(text, cols):
    """A tutor's tip, in a teal bubble of its own across the transcript, ✦ tutor on its top edge, and a blank line after it."""
    room = max(min(cols - 8, TUTOR_TEXT_MAX), 20)
    lines = wrap(text, room) or [""]
    head = f"─ {TUTOR_MARK} tutor "
    inner = max(max(width(line) for line in lines), width(head))

    def edge(left, middle, right):
        return "  " + style(left + middle + right, TUTOR_BG, TUTOR_INK)
    return ([edge("╭", head + "─" * (inner + 2 - width(head)), "╮")]
            + ["  " + style("│ ", TUTOR_BG, TUTOR_INK) + style(pad(line, inner), TUTOR_BG, TUTOR_TEXT) + style(" │", TUTOR_BG, TUTOR_INK)
               for line in lines]
            + [edge("╰", "─" * (inner + 2), "╯"), ""])


def _clean_note(text):
    """A note as it is kept: its lines without trailing spaces, no blank lines before or after, and NOTE_LINES at most."""
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[:NOTE_LINES])[:NOTE_MAX].strip()


EDIT_LEFT = 5  # the inline drawing's first column: the picture's


def _grip(cols, label):
    """The line under an inline drawing, which resizes it."""
    label = f" {label} "
    side = max(cols - 1 - width(label), 0)
    return style("╌" * (side // 2) + label + "╌" * (side - side // 2) + "◢", GREY)


_SIDES = {"left": (0, -1), "right": (0, 1), "up": (-1, 0), "down": (1, 0)}
_LINES = {frozenset(("left", "right")): "─", frozenset(("up", "down")): "│", frozenset(("left", "down")): "╮",
          frozenset(("right", "down")): "╭", frozenset(("left", "up")): "╯", frozenset(("right", "up")): "╰"}
_OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}


def _route(start, entry, top, left, height, size):
    """A line in text from `start` (row, col), which it enters from its `entry` side, to a bubble at (top, left), joining one of
    its sides: [(row, col, character, on the bubble's edge)]. Along a row, then a column, then a row, as box drawing goes; nothing
    if the bubble covers the start."""
    row, col = start
    bottom, right = top + height - 1, left + size - 1
    if left > col:  # the bubble to the right: into its left side
        joint = min(max(row, top + 1), bottom - 1)
        turn = max(col, left - 2)
        points, last, edge = [(row, col), (row, turn), (joint, turn), (joint, left - 1)], "right", (joint, left, "┤")
    elif right < col:  # to the left: into its right side
        joint = min(max(row, top + 1), bottom - 1)
        # a picture's leader, which comes from the left, turns at once, beside the picture rather than through it
        turn = col if entry == "left" else min(col, right + 2)
        points, last, edge = [(row, col), (row, turn), (joint, turn), (joint, right + 1)], "left", (joint, right, "├")
    elif top > row:  # below: into its top, clear of its controls
        at = min(max(col, left + 1), max(right - 6, left + 1))
        points, last, edge = [(row, col), (top - 1, col), (top - 1, at)], "down", (top, at, "┴")
    elif bottom < row:  # above: into its bottom
        at = min(max(col, left + 1), right - 1)
        points, last, edge = [(row, col), (bottom + 1, col), (bottom + 1, at)], "up", (bottom, at, "┬")
    else:
        return []
    walk = [points[0]]
    for target in points[1:]:
        while walk[-1] != target:
            r, c = walk[-1]
            walk.append((r + (target[0] > r) - (target[0] < r), c + (target[1] > c) - (target[1] < c)) if r == target[0] or c == target[1]
                        else (r + (target[0] > r) - (target[0] < r), c))
    out = []
    for i, (r, c) in enumerate(walk):
        came = entry if i == 0 else _OPPOSITE[_step(walk[i - 1], (r, c))]
        going = last if i == len(walk) - 1 else _step((r, c), walk[i + 1])
        out.append((r, c, _LINES.get(frozenset((came, going)), "─" if came in ("left", "right") else "│"), False))
    return out + [(*edge, True)]


def _step(a, b):
    return next(side for side, (dr, dc) in _SIDES.items() if (b[0] - a[0], b[1] - a[1]) == (dr, dc))


def _cover(line, col, text, interior):
    """The line with `text` drawn over it from `col`; in the `interior` span of a bubble's row, a blank cell over text shows that
    text faintly, so that the user knows there is something under the bubble."""
    if interior is None:
        return overlay(line, col, text)
    (_, paper, faint, _, _), span = interior
    under, new = cells(line), cells(text)
    for i in span:
        below = under[col + i][1] if col + i < len(under) else " "
        if new[i][1] == " " and below.strip() and below[:1] != kitty.PLACEHOLDER:
            new[i] = [paper + faint, below]
    under += [["", " "] for _ in range(col + len(new) - len(under))]
    under[col:col + len(new)] = new
    return join(under)
