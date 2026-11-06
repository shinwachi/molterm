"""A Claude Code-like terminal interface for a server-side agent, written to run in Pyodide in the browser (or any Python).

The page hands this app the terminal's keys (`key`) and the agent's events from the server (`event`), and gives it a `bridge` to talk
back through: `write(text)` prints to the terminal, `post_message(text, cols)` sends what the user typed, `post_tool_result(id, json)`
answers one of the agent's TUI tools, and `interrupt()` stops the agent's turn. The app redraws the whole screen after each change.

The agent can use the interface as tools:
  tui.show {title, body}           puts a panel in the transcript (body may carry ANSI colours, such as a molterm drawing)
  tui.ask  {question, options}     asks the user to pick one; the answer goes back to the agent as the tool's result
"""
import json
import time
from dataclasses import dataclass, field

from . import keys as keyboard
from .screen import (ACCENT, BOLD, DIM, GREEN, GREY, ITALIC, RED, RESET, USER_BG, pad, style, truncate, width, wrap)

SPINNER = "·✢✳✶✻✽✻✶✳✢"
TOOL_NAMES = {"tui.show": "Show", "tui.ask": "Ask", "molterm.render": "Render", "molterm.properties": "Properties"}
INPUT_LINES_MAX = 6
HELP = """Shortcuts
  enter          send            esc            interrupt the agent, or skip a question
  ↑ ↓            history         pgup pgdn      scroll the transcript
  ctrl+u         clear the line  ctrl+l         redraw
Commands
  /help          this help       /clear         clear the transcript"""


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

    # -- from the page ----------------------------------------------------------------------------------------------------------------

    def start(self):
        self.started = True
        self.bridge.write("\x1b[?1049h\x1b[?2004h")  # the alternate screen, and pastes marked as pastes
        self.draw()

    def resize(self, cols, rows):
        self.cols, self.rows = max(int(cols), 20), max(int(rows), 8)
        self.draw()

    def tick(self):
        """Called a few times a second, to turn the spinner."""
        if self.busy:
            self.frame += 1
            self.draw()

    def key(self, data):
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
        body = str(block.input.get("body", ""))
        block.panel = body.split("\n")
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

    def _close_text(self):
        for block in self.blocks:
            if block.kind == "assistant":
                block.open = False

    # -- keys -------------------------------------------------------------------------------------------------------------------------

    def _key(self, key):
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
        if text == "/clear":
            self.blocks = [Block("banner")]
            return
        self.history.append(text)
        self.blocks.append(Block("user", text))
        self.bridge.post_message(text, self.cols - 6)

    # -- drawing ----------------------------------------------------------------------------------------------------------------------

    def _block_lines(self, block, cols):
        if block.kind == "banner":
            inner = cols - 4
            rows = [style("✻ ", ACCENT) + style("Welcome to the molterm agent!", BOLD), "",
                    style("  An emulated agent on the server, talking to this interface,", GREY),
                    style("  which runs as Python in your browser (Pyodide).", GREY), "",
                    style(f"  session: {self.session or 'connecting…'}", GREY)]
            border = style("╭" + "─" * (cols - 2) + "╮", ACCENT)
            return [border, *(style("│ ", ACCENT) + pad(row, inner) + style(" │", ACCENT) for row in rows),
                    style("╰" + "─" * (cols - 2) + "╯", ACCENT), "",
                    style(" Tips: type a SMILES, such as CC(=O)Oc1ccccc1C(=O)O, or ask for one. /help for help.", GREY), ""]
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
        argument = block.input.get("title") or block.input.get("smiles") or block.input.get("question") or ""
        head = f"{mark} {style(name, BOLD)}({truncate(str(argument), max(cols - width(name) - 6, 8))})"
        lines = [head]
        body = []
        if block.panel:
            title = block.input.get("title")
            body += ([style(str(title), BOLD)] if title else []) + [truncate(line, cols - 5) for line in block.panel]
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
            content = line if self.input else style("Try a SMILES, or \"show me a drug\"", GREY, ITALIC)
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
        content = [line for block in self.blocks for line in self._block_lines(block, cols)]
        footer = self._footer(cols)
        room = self.rows - len(footer)
        if room < 1:
            footer = footer[-self.rows:]
            room = 0
        self.scroll = min(self.scroll, max(len(content) - room, 0))
        end = len(content) - self.scroll
        shown = content[max(end - room, 0):end] if room else []
        screen = shown + footer
        cursor = None
        if not self.question:
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
        screen, cursor = self.lines()
        out = "\x1b[?25l\x1b[H" + "\r\n".join(line + RESET + "\x1b[K" for line in screen) + "\x1b[J"
        if cursor:
            out += f"\x1b[{cursor[0] + 1};{cursor[1] + 1}H\x1b[?25h"
        return out

    def draw(self):
        if self.started:
            self.bridge.write(self.render())
