"""The TUI, driven as the page drives it: keys and the agent's events in, frames and calls to the bridge out."""
import json

import pytest

from molterm_tui import App
from molterm_tui import keys
from molterm_tui.screen import strip, width, wrap


class Bridge:
    def __init__(self):
        self.written, self.messages, self.results, self.interrupts = [], [], [], 0

    def write(self, text):
        self.written.append(text)

    def post_message(self, text, cols):
        self.messages.append((text, cols))

    def post_tool_result(self, tool_id, result):
        self.results.append((tool_id, json.loads(result)))

    def interrupt(self):
        self.interrupts += 1


@pytest.fixture
def app():
    app = App(Bridge(), 80, 30)
    app.start()
    return app


def screen(app):
    lines, _ = app.lines()
    return "\n".join(strip(line) for line in lines)


def test_keys_are_parsed_as_terminals_send_them():
    assert [k.name for k in keys.parse("\x1b[A\x1b[B\r\x7f\x1b")] == ["up", "down", "enter", "backspace", "escape"]
    assert keys.parse("hi there") == [keys.Key("text", "hi there")]
    assert keys.parse("\x1b[200~a\r\nb\x1b[201~") == [keys.Key("paste", "a\nb")]
    assert keys.parse("\x1b[5~\x1b[6~")[1].name == "pagedown"
    assert keys.parse("\x1b\r") == [keys.Key("enter", alt=True)]


def test_wrapping_counts_columns_not_characters():
    assert wrap("one two three", 7) == ["one two", "three"]
    assert wrap("⠁" * 10, 4) == ["⠁⠁⠁⠁", "⠁⠁⠁⠁", "⠁⠁"]
    assert width("\x1b[31mO\x1b[0m漢") == 3


def test_it_starts_with_a_banner_and_an_empty_input_box(app):
    text = screen(app)
    assert "Welcome to the molterm agent!" in text
    assert "session: connecting…" in text
    assert "╭" in text and "Try a SMILES" in text
    assert app.bridge.written[0].startswith("\x1b[?1049h")  # the alternate screen


def test_typing_and_enter_send_the_message(app):
    app.event({"type": "session", "id": "abc123"})
    app.key("CCO")
    assert "> CCO" in screen(app)
    app.key("\r")
    assert app.bridge.messages == [("CCO", 74)]
    assert "> CCO" in screen(app)  # now in the transcript
    assert app.input == ""


def test_streamed_text_appears_as_one_reply(app):
    app.event({"type": "turn_start", "label": "Thinking"})
    assert "Thinking…" in screen(app)
    for word in ("Hello ", "there, ", "chemist."):
        app.event(json.dumps({"type": "text", "delta": word}))
    app.event({"type": "turn_end"})
    text = screen(app)
    assert "● Hello there, chemist." in text
    assert "Thinking…" not in text


def test_server_tools_show_their_result(app):
    app.event({"type": "tool_use", "id": "t1", "name": "molterm.render", "input": {"smiles": "CCO"}})
    assert "Render(CCO)" in screen(app) and "Running…" in screen(app)
    app.event({"type": "tool_result", "id": "t1", "output": "Drew it in 3 lines of Braille"})
    assert "└  Drew it in 3 lines of Braille" in screen(app)
    assert app.bridge.results == []  # a server tool is answered by the server


def test_tui_show_puts_a_panel_in_the_transcript_and_answers(app):
    body = "\x1b[31mO\x1b[0m⠒⠒⠒\n⠀⠀⠉⠉"
    app.event({"type": "tool_use", "id": "t2", "name": "tui.show", "input": {"title": "Ethanol", "body": body}})
    text = screen(app)
    assert "Show(Ethanol)" in text and "O⠒⠒⠒" in text and "⠀⠀⠉⠉" in text
    assert app.bridge.results == [("t2", {"shown": True, "lines": 2})]


def test_tui_show_sends_a_picture_once_and_prints_its_placeholders(app):
    from molterm_tui import kitty
    image = {"png": "A" * 5000, "cols": 3, "rows": 2}
    app.bridge.written.clear()
    app.event({"type": "tool_use", "id": "t9", "name": "tui.show", "input": {"title": "Ethanol", "image": image}})
    sent = "".join(app.bridge.written)
    assert sent.count("\x1b_G") == 1 + 2  # a delete of the id, then the picture in two chunks
    assert "a=T,f=100,U=1,i=1,c=3,r=2,q=2,m=1;" + "A" * 4096 in sent
    assert app.bridge.results == [("t9", {"shown": True, "lines": 2})]
    lines, _ = app.lines()
    cells = [line for line in lines if kitty.PLACEHOLDER in line]
    assert len(cells) == 2 and all(strip(line).count(kitty.PLACEHOLDER) == 3 for line in cells)
    assert kitty.colour(1) in cells[0] and kitty.DIACRITICS[1] + kitty.DIACRITICS[2] in cells[1]  # row 1, column 2
    assert all(width(line) <= 80 for line in lines)
    app.bridge.written.clear()
    app.draw()
    assert "\x1b_G" not in "".join(app.bridge.written)  # frames don't send it again


def test_tui_ask_takes_over_the_input_until_answered(app):
    app.event({"type": "tool_use", "id": "t3", "name": "tui.ask", "input": {"question": "Which drug?", "options": ["Aspirin", "Caffeine"]}})
    text = screen(app)
    assert "Which drug?" in text and "❯ 1. Aspirin" in text and "2. Caffeine" in text
    _, cursor = app.lines()
    assert cursor is None  # no text cursor while choosing
    app.key("\x1b[B")
    assert "❯ 2. Caffeine" in screen(app)
    app.key("\r")
    assert app.bridge.results == [("t3", {"choice": "Caffeine", "index": 1})]
    assert "└  Caffeine" in screen(app)
    assert "Try a SMILES" in screen(app)  # the input is back


def test_a_number_picks_and_escape_skips(app):
    app.event({"type": "tool_use", "id": "a", "name": "tui.ask", "input": {"question": "Q?", "options": ["Yes", "No"]}})
    app.key("2")
    app.event({"type": "tool_use", "id": "b", "name": "tui.ask", "input": {"question": "Q?", "options": ["Yes", "No"]}})
    app.key("\x1b")
    assert app.bridge.results == [("a", {"choice": "No", "index": 1}), ("b", {"choice": None, "skipped": True})]


def test_escape_interrupts_a_busy_agent(app):
    app.event({"type": "turn_start"})
    app.key("\x1b")
    assert app.bridge.interrupts == 1
    assert "Interrupted by user" in screen(app)


def test_slash_commands_stay_local(app):
    app.key("/help\r")
    assert "Shortcuts" in screen(app)
    app.key("/clear\r")
    assert "Shortcuts" not in screen(app)
    assert app.bridge.messages == []


def test_history_and_editing_keys(app):
    app.key("first\r")
    app.key("second\r")
    app.key("\x1b[A")
    assert app.input == "second"
    app.key("\x1b[A")
    assert app.input == "first"
    app.key("\x1b[D\x1b[D\x7f")  # left twice, then delete the character before the cursor
    assert app.input == "fist"


def test_the_screen_never_outgrows_the_terminal(app):
    for n in range(60):
        app.event({"type": "text", "delta": f"line {n} "})
        app.event({"type": "text_end"})
    app.resize(40, 12)
    lines, cursor = app.lines()
    assert len(lines) <= 12
    assert all(width(line) <= 40 for line in lines)
    assert cursor is not None and 0 <= cursor[0] < 12
    assert "line 59" in "\n".join(strip(line) for line in lines)
    app.key("\x1b[5~")  # page up
    assert "line 59" not in screen(app)


def test_the_cursor_follows_the_typing(app):
    app.key("abc")
    lines, (row, col) = app.lines()
    assert strip(lines[row]).startswith("│ > abc")
    assert col == 4 + 3
