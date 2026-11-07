"""A Claude Code-like terminal interface for a server-side agent, written to run in Pyodide in the browser (or any Python).

The page hands this app the terminal's keys (`key`) and the agent's events from the server (`event`), and gives it a `bridge` to talk
back through: `write(text)` prints to the terminal, `post_message(text, cols)` sends what the user typed, `post_tool_result(id, json)`
answers one of the agent's TUI tools, and `interrupt()` stops the agent's turn. The app redraws the whole screen after each change.

Molecules: a picture shown with a `smiles` is a molecule the app keeps (`molecules`), numbered from 1, and it knows where each is on the
screen (`placed`). Clicking one, or /edit N, edits it where it is: molterm's editor, compact (a strip of tools over the drawing), in a box
over the picture. The editor runs on the molterm server, through `bridge.edit(fields as JSON)`, whose answer comes back to
`edit_answer(text)` (or `edit_failed(message)`). Done puts the edited molecule, as CXSMILES with its annotations, into the input for the
user's next message, and asks for its new picture with `bridge.picture(smiles, cols, number)`, answered by `picture_answer`.

The agent can use the interface as tools:
  tui.show {title, image, body,    puts a panel in the transcript: a picture (`image`: {png (base64), cols, rows}), drawn with the
            smiles}                Kitty graphics protocol, and/or text (`body`, which may carry ANSI colours, such as a Braille drawing);
                                   with `smiles`, the picture is that molecule, which the user can edit and annotate
  tui.ask  {question, options}     asks the user to pick one; the answer goes back to the agent as the tool's result
"""
import json
import re
import time
from dataclasses import dataclass, field

from . import keys as keyboard
from . import kitty
from .screen import (ACCENT, BOLD, DIM, GREEN, GREY, ITALIC, RED, RESET, USER_BG, pad, style, truncate, width, wrap)

SPINNER = "·✢✳✶✻✽✻✶✳✢"
TOOL_NAMES = {"tui.show": "Show", "tui.ask": "Ask", "molterm.render": "Render", "molterm.properties": "Properties"}
INPUT_LINES_MAX = 6
EDIT_ROWS_MIN = 14  # the editor's box: its border, the strip of tools, the drawing and two lines of status
EDIT_ROWS = 22  # what it gets when there is room: enough for an atom's menu to open inside it
EDIT_COLS_MIN = 60
HELP = """Shortcuts
  enter          send            esc            interrupt the agent, or skip a question
  ↑ ↓            history         pgup pgdn      scroll the transcript
  ctrl+u         clear the line  ctrl+l         redraw
Molecules
  click one      edit or annotate it where it is: Done (ctrl+q) puts it in your message, Cancel (ctrl+c) leaves it
  /edit [N]      the same for molecule N, or the last one
Commands
  /help          this help       /clear         clear the transcript"""


def context(molecule):
    """How an edited molecule goes into the user's message: ⟦mol N name, edited: CXSMILES⟧. SMILES never holds ⟦ or ⟧."""
    return f"⟦mol {molecule.number} {molecule.name}, edited: {molecule.smiles}⟧ "


CONTEXT = re.compile(r"⟦mol (\d+) (.*?), edited: ([^⟧]+)⟧")


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
    smiles: str
    block: Block
    image: int  # its Kitty picture's id
    cols: int
    rows: int
    edits: int = 0


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

    # -- from the page ----------------------------------------------------------------------------------------------------------------

    def start(self):
        self.started = True
        # the alternate screen, pastes marked as pastes, and clicks and the wheel reported (SGR), to pick a molecule
        self.bridge.write("\x1b[?1049h\x1b[?2004h\x1b[?1000h\x1b[?1006h")
        self.draw()

    def resize(self, cols, rows):
        self.cols, self.rows = max(int(cols), 20), max(int(rows), 8)
        if self.editing:  # the box was placed for the old size: the edit is cancelled
            self._edit_end(3, "")
        self.draw()

    def tick(self):
        """Called a few times a second, to turn the spinner."""
        if self.busy:
            self.frame += 1
            self.draw()

    def key(self, data):
        if self.editing:  # everything goes to the editor, which answers with what to draw
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
        if body:
            block.panel += str(body).split("\n")
        block.state = "done"
        self.bridge.post_tool_result(block.tool_id, json.dumps({"shown": True, "lines": len(block.panel)}))

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

    def _finish(self, block, text, error=False):
        block.state, block.result = ("error" if error else "done"), [text]
        self.bridge.post_tool_result(block.tool_id, json.dumps({"error": text} if error else {"ok": text}))

    def _tool(self, tool_id):
        return next((b for b in reversed(self.blocks) if b.kind == "tool" and b.tool_id == tool_id), None)

    # -- molecules, edited where they are shown --------------------------------------------------------------------------------------

    def edit(self, number):
        """Open the compact editor over molecule `number`'s picture, scrolling to it if need be."""
        molecule = next((m for m in self.molecules if m.number == number), None)
        if molecule is None or self.editing:
            if molecule is None:
                self.blocks.append(Block("note", f"There is no molecule {number}." if self.molecules else "No molecule is shown yet."))
            return
        screen, _ = self.lines()
        room = self._room
        top = self.placed.get(number, (None,))[0]
        if top is None or top < 3 or top + molecule.rows + 3 > room:  # bring it into view, with room for the strip of tools above
            self.scroll = max(self._content - (self._content_at[number] - 3 + room), 0)
            screen, _ = self.lines()
            top = self.placed.get(number, (0,))[0]
        rows = min(max(molecule.rows + 7, EDIT_ROWS), room)
        if rows < EDIT_ROWS_MIN or self.cols - 1 < EDIT_COLS_MIN:
            self.blocks.append(Block("note", "The window is too small to edit here; make it larger and try again.", tone="error"))
            return
        box_top = min(max(top - 3, 0), room - rows)  # border and tools above the picture, so the drawing stays about where it was
        self.editing = {"molecule": molecule, "box": (box_top, 0, rows, self.cols - 1), "frozen": screen[:room], "state": "",
                        "pending": "", "first": True, "waiting": False, "closing": False, "result": None}
        self.bridge.write("\x1b[?1003h")  # every movement, for the editor's hover and drags
        self.draw()
        self._edit_pump()

    def _edit_pump(self):
        """Send the editor what has been typed or clicked, one request at a time; what comes meanwhile waits for the next."""
        e = self.editing
        if not e or e["waiting"] or not (e["first"] or e["pending"] or e["closing"]):
            return
        molecule = e["molecule"]
        if e["closing"]:
            fields = {"close": "1"}
        else:
            fields = {"cols": self.cols, "rows": self.rows, "box": ",".join(str(n) for n in e["box"]),
                      "title": f"mol {molecule.number} · {molecule.name}", "events": e["pending"].encode().hex()}
            fields.update({"smiles": molecule.smiles} if e["first"] else {"state": e["state"]})
        e["pending"], e["first"], e["waiting"] = "", False, True
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
            self.bridge.write(output)
            self._edit_pump()
        else:
            self._edit_end(int(status) if status.isdigit() else 2, output.strip())

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
        self.bridge.write("\x1b[?1003l")
        status, text = e["result"]
        molecule = e["molecule"]
        if status == 1 and text and text != molecule.smiles:
            molecule.smiles, molecule.edits = text, molecule.edits + 1
            insert = context(molecule)
            self.input = self.input[:self.cursor] + insert + self.input[self.cursor:]
            self.cursor += len(insert)
            self.bridge.picture(text, self.cols - 6, molecule.number)
        elif status == 2:
            self.blocks.append(Block("note", text or "The editor stopped.", tone="error"))
        self.draw()

    def picture_answer(self, number, data):
        """A molecule's new picture, after an edit: {png, cols, rows}, as JSON text or a dict."""
        molecule = next((m for m in self.molecules if m.number == int(number)), None)
        image = json.loads(data) if isinstance(data, str) else data
        if molecule is None or not isinstance(image, dict) or not image.get("png"):
            return
        old_rows = molecule.rows
        self.images += 1
        molecule.cols, molecule.rows = int(image.get("cols") or 1), int(image.get("rows") or 1)
        self.bridge.write(kitty.transmit(str(image["png"]), self.images, molecule.cols, molecule.rows) + kitty.delete(molecule.image))
        molecule.image = self.images
        block = molecule.block
        block.panel = kitty.lines(molecule.image, molecule.cols, molecule.rows) + block.panel[old_rows:]
        self.draw()

    def _close_text(self):
        for block in self.blocks:
            if block.kind == "assistant":
                block.open = False

    # -- keys -------------------------------------------------------------------------------------------------------------------------

    def _key(self, key):
        if key.name == "mouse":
            button, col, row, pressed = key.mouse
            if button in (64, 65):  # the wheel
                self.scroll = max(self.scroll + (3 if button == 64 else -3), 0)
            elif button == 0 and pressed and not self.question:
                for number, (top, left, rows, cols) in self.placed.items():
                    if top <= row < top + rows and left <= col < left + cols:
                        self.edit(number)
                        break
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
            self.input = self.input[:self.cursor - 1] + self.input[self.cursor:]
            self.cursor -= 1
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
        edit = re.fullmatch(r"/edit(?:\s+(\d+))?", text)
        if edit:
            self.edit(int(edit.group(1)) if edit.group(1) else (self.molecules[-1].number if self.molecules else 0))
            return
        if text == "/clear":
            self.blocks, self.molecules, self.placed = [Block("banner")], [], {}
            self.bridge.write("".join(kitty.delete(n) for n in range(1, self.images + 1)))  # the pictures go with their placeholders
            return
        self.history.append(text)
        self.blocks.append(Block("user", text))
        self.bridge.post_message(text, self.cols - 6)

    # -- drawing ----------------------------------------------------------------------------------------------------------------------

    def _block_lines(self, block, cols):
        if block.kind == "banner":
            inner = cols - 4
            rows = [style("✻ ", ACCENT) + style("Welcome to the molterm agent!", BOLD), "",
                    style("  An agent, talking to you through this interface,", GREY),
                    style("  which runs as Python in your browser (Pyodide).", GREY), "",
                    style(f"  session: {self.session or 'connecting…'}", GREY)]
            border = style("╭" + "─" * (cols - 2) + "╮", ACCENT)
            return [border, *(style("│ ", ACCENT) + pad(row, inner) + style(" │", ACCENT) for row in rows),
                    style("╰" + "─" * (cols - 2) + "╯", ACCENT), "",
                    style(truncate(" Tips: type a message for the agent and press enter. /help for help.", cols), GREY), ""]
        if block.kind == "user":
            lines = wrap(block.text, cols - 4)
            return [style(pad(("> " if i == 0 else "  ") + line, cols - 2), USER_BG, GREY) for i, line in enumerate(lines)] + [""]
        if block.kind == "assistant":
            lines = wrap(block.text, cols - 2)
            return [("● " if i == 0 else "  ") + line for i, line in enumerate(lines)] + [""]
        if block.kind == "note":
            tone = (RED,) if block.tone == "error" else (GREY,)
            return [style("  └  " + line if i == 0 else "     " + line, *tone) for i, line in enumerate(block.text.split("\n"))] + [""]
        if block.kind == "tool":
            return self._tool_lines(block, cols)
        return []

    def _tool_lines(self, block, cols):
        mark = {"running": style("●", GREY) if self.frame % 2 else "●", "done": style("●", GREEN), "error": style("●", RED)}[block.state]
        name = TOOL_NAMES.get(block.name, block.name)
        named = block.input.get("title") or block.input.get("smiles") or block.input.get("question")
        argument = named or next((v for v in block.input.values() if isinstance(v, str)), "")  # else the first text it was given
        head = f"{mark} {style(name, BOLD)}({truncate(str(argument), max(cols - width(name) - 6, 8))})"
        lines = [head]
        body = []
        if block.panel:
            title = block.input.get("title")
            molecule = block.molecule
            if molecule:
                tag = f"mol {molecule.number}" + (" · edited" if molecule.edits else "")
                title = (style(str(title), BOLD) + "  " if title else "") + style(tag, GREY)
            elif title:
                title = style(str(title), BOLD)
            block.image_line = 2 if title else 1  # after the tool's line and the title
            body += ([title] if title else []) + [truncate(line, cols - 5) for line in block.panel]
        if block.result:
            tone = (RED,) if block.state == "error" else (GREY,)
            body += [style(truncate(line, cols - 5), *tone) for line in block.result]
        if block.state == "running" and not body:
            body = [style("Waiting for your answer…" if block.name == "tui.ask" else "Running…", GREY)]
        lines += [style("  └  ", GREY) + line if i == 0 else "     " + line for i, line in enumerate(body)]
        return lines + [""]

    def _footer(self, cols):
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
        for i, line in enumerate(shown):
            prefix = "> " if i == 0 and len(shown) == len(text_lines) else "  "
            content = line if self.input else style("Message the agent", GREY, ITALIC)
            box.append(style("│ ", GREY) + pad(prefix + content, inner) + style(" │", GREY))
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

    def lines(self):
        """The screen's lines, top to bottom, and where the cursor goes (row, col), or None to hide it."""
        cols = self.cols - 1  # a margin, so nothing is written in the last column
        content, self._content_at = [], {}
        for block in self.blocks:
            lines = self._block_lines(block, cols)
            if block.molecule and block.image_line >= 0:
                self._content_at[block.molecule.number] = len(content) + block.image_line
            content += lines
        footer = self._footer(cols)
        room = self.rows - len(footer)
        if room < 1:
            footer = footer[-self.rows:]
            room = 0
        self.scroll = min(self.scroll, max(len(content) - room, 0))
        end = len(content) - self.scroll
        start = max(end - room, 0)
        shown = content[start:end] if room else []
        screen = shown + [""] * (room - len(shown)) + footer  # the input stays at the bottom, as in Claude Code
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
            self.bridge.write(self.render())
