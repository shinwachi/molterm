"""The TUI, driven as the page drives it: keys and the agent's events in, frames and calls to the bridge out."""
import json

import pytest

from molterm_tui import App
from molterm_tui import keys
from molterm_tui.screen import strip, width, wrap


class Bridge:
    def __init__(self):
        self.written, self.messages, self.results, self.interrupts = [], [], [], 0
        self.edits, self.pictures = [], []

    def write(self, text):
        self.written.append(text)

    def post_message(self, text, cols):
        self.messages.append((text, cols))

    def post_tool_result(self, tool_id, result):
        self.results.append((tool_id, json.loads(result)))

    def interrupt(self):
        self.interrupts += 1

    def edit(self, fields):
        self.edits.append(json.loads(fields))

    def picture(self, smiles, cols, number):
        self.pictures.append((smiles, number))


@pytest.fixture
def app():
    app = App(Bridge(), 80, 30)
    app.start()
    return app


def screen(app):
    lines, _ = app.lines()
    return "\n".join(strip(line) for line in lines)


def test_keys_are_parsed_as_terminals_send_them():
    assert keys.parse("\x1b[<0;12;5M")[0].mouse == (0, 11, 4, True) and keys.parse("\x1b[<65;1;1m")[0].mouse == (65, 0, 0, False)
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
    assert "╭" in text and "Message the agent" in text
    assert app.bridge.written[0].startswith("\x1b[?1049h")  # the alternate screen


def test_the_input_box_sits_at_the_bottom_of_the_screen(app):
    lines, (row, _) = app.lines()
    assert len(lines) == 30
    assert "/help for shortcuts" in strip(lines[-1]) and "Message the agent" in strip(lines[row])
    assert row == 30 - 3  # the box's middle line, above its bottom edge and the hints


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
    app.event({"type": "tool_use", "id": "t1b", "name": "text.count", "input": {"limit": 3, "text": "two words"}})
    assert "text.count(two words)" in screen(app)  # an agent's own tool shows the first text it was given


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
    assert "Message the agent" in screen(app)  # the input is back


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


def show_molecule(app, smiles="CCO", title="Ethanol", tool_id="m1"):
    image = {"png": "A" * 10, "cols": 20, "rows": 6}
    app.event({"type": "tool_use", "id": tool_id, "name": "tui.show", "input": {"title": title, "image": image, "smiles": smiles}})


def test_a_molecule_shown_is_kept_and_its_place_known(app):
    show_molecule(app)
    assert [(m.number, m.name, m.smiles) for m in app.molecules] == [(1, "Ethanol", "CCO")]
    assert "Ethanol  mol 1" in screen(app) and "/edit 1" in screen(app)
    lines, _ = app.lines()
    top, left, rows, cols = app.placed[1]
    from molterm_tui import kitty
    assert kitty.PLACEHOLDER in lines[top] and kitty.PLACEHOLDER not in lines[top - 1] and (left, rows, cols) == (5, 6, 20)
    assert app.bridge.results == [("m1", {"shown": True, "lines": 7})]


def test_clicking_a_molecule_edits_it_in_a_box_over_its_picture(app):
    show_molecule(app)
    app.key("draft")
    top = app.placed[1][0]
    app.bridge.written.clear()
    app.key(f"\x1b[<0;10;{top + 2}M")  # a click on its second row
    assert app.editing and app.bridge.edits[0]["smiles"] == "CCO" and app.bridge.edits[0]["events"] == ""
    box = tuple(int(n) for n in app.bridge.edits[0]["box"].split(","))
    assert box[1:] == (0, 22, 79) and app.bridge.edits[0]["title"] == "mol 1 · Ethanol"
    assert box[0] <= top - 3 and top + 6 <= box[0] + 22 <= app._room  # over the picture, above the input
    assert "\x1b[?1003h" in "".join(app.bridge.written)  # every movement now, for the editor
    drawn = "".join(app.bridge.written)
    assert f"\x1b[{top + 1};1H" not in drawn and "\x1b[1;1H" in drawn  # the app leaves the box to the editor
    app.key("x")  # waits for the editor's first answer
    assert len(app.bridge.edits) == 1
    app.edit_answer("0 S1\n\x1b[5;1Hthe editor")
    assert app.bridge.written[-1] == "\x1b[5;1Hthe editor" and app.bridge.edits[1] == {**app.bridge.edits[1], "state": "S1", "events": "78"}
    app.edit_answer("0 S2\n")
    app.key("\x11")  # Ctrl-Q
    app.edit_answer("1 \nCCO |atomProp:2.atomNote.OH|")
    assert app.bridge.edits[-1] == {"close": "1"}  # first the editor's pictures go
    app.edit_answer("3 \n\x1b_Ga=d\x1b\\")
    assert app.editing is None and "\x1b[?1003l" in "".join(app.bridge.written)
    assert app.input == "draft⟦mol 1 Ethanol, edited: CCO |atomProp:2.atomNote.OH|⟧ "
    assert app.bridge.pictures == [("CCO |atomProp:2.atomNote.OH|", 1)]
    app.picture_answer(1, json.dumps({"png": "B" * 10, "cols": 22, "rows": 7}))
    assert app.molecules[0].image == 2 and "mol 1 · edited" in screen(app)
    from molterm_tui.app import CONTEXT
    assert CONTEXT.search(app.input).groups() == ("1", "Ethanol", "CCO |atomProp:2.atomNote.OH|")


def test_cancelling_an_edit_leaves_the_input_alone_and_edit_finds_the_last_molecule(app):
    show_molecule(app)
    show_molecule(app, "c1ccccc1", "Benzene", "m2")
    app.key("/edit\r")
    assert app.editing["molecule"].number == 2
    app.edit_answer("3 \n")
    app.edit_answer("3 \n")
    assert app.editing is None and app.input == "" and app.bridge.pictures == []
    app.key("/edit 7\r")
    assert "There is no molecule 7." in screen(app)


def test_an_edit_that_fails_says_why_and_the_wheel_scrolls(app):
    show_molecule(app)
    app.key("/edit 1\r")
    app.edit_failed("molterm can't be reached")
    app.edit_answer("3 \n")
    assert app.editing is None and "The editor stopped: molterm can't be reached" in screen(app)
    for n in range(40):
        app.event({"type": "text", "delta": f"line {n} "})
        app.event({"type": "text_end"})
    app.key("\x1b[<64;5;5M")
    assert app.scroll == 3
