"""The TUI, driven as the page drives it: keys and the agent's events in, frames and calls to the bridge out."""
import json
import re

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

    def picture(self, request):
        self.pictures.append(json.loads(request))


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
    assert sent.count("\x1b_Ga=d,d=I") + sent.count("a=T,") + sent.count("\x1b_Gq=2") == 1 + 2  # a delete of the id, then two chunks
    assert "a=T,f=100,U=1,i=1,c=3,r=2,q=2,m=1;" + "A" * 4096 in sent
    assert app.bridge.results == [("t9", {"shown": True, "lines": 2})]
    lines, _ = app.lines()
    cells = [line for line in lines if kitty.PLACEHOLDER in line]
    assert len(cells) == 2 and all(strip(line).count(kitty.PLACEHOLDER) == 3 for line in cells)
    assert kitty.colour(1) in cells[0] and kitty.DIACRITICS[1] + kitty.DIACRITICS[2] in cells[1]  # row 1, column 2
    assert all(width(line) <= 80 for line in lines)
    app.bridge.written.clear()
    app.draw()
    assert "i=1," not in "".join(app.bridge.written)  # frames don't send it again


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
    assert app.bridge.results == [("m1", {"shown": True, "mol": 1, "lines": 7})]


def test_clicking_a_molecule_edits_it_in_a_box_over_its_picture(app):
    show_molecule(app)
    app.key("/editor box\r")
    assert "edits it in a box" in screen(app)
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
    assert app.input == "draft" and [c.label for c in app.chips] == ["mol 1 Ethanol · edited #1"]  # waiting above the input
    request = app.bridge.pictures[0]
    assert request["smiles"] == "CCO |atomProp:2.atomNote.OH|" and request["number"] == 1 and request["cols"] == 80 - 6 - 37
    app.picture_answer(json.dumps(request), json.dumps({"png": "B" * 10, "cols": 22, "rows": 7, "smiles": request["smiles"],
                                                        "notes": [{"atom": 2, "text": "OH", "row": 1}]}))
    assert app.molecules[0].image == 2 and "mol 1 · edited" in screen(app)
    from molterm_tui.app import CONTEXT
    assert "◇ mol 1 Ethanol · edited #1" in screen(app)
    click(app, chip(app))  # into the message, short in the box, in full in what is sent
    assert app.input == "draft ⟦mol 1 Ethanol, edited #1⟧ " and "◆ mol 1 Ethanol" in screen(app)
    app.key("ok?\r")
    assert CONTEXT.search(app.bridge.messages[-1][0]).groups() == ("1", "Ethanol", "CCO |atomProp:2.atomNote.OH|")
    assert "⟦mol 1 Ethanol, edited #1⟧" in screen(app) and app.chips == []  # sent, so no longer waiting


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
    app.wheel(5)  # a page that keeps the wheel to itself scrolls the transcript by calling this
    assert app.scroll == 8
    app.wheel(-99)  # and never past the bottom of it
    assert app.scroll == 0


def test_a_click_edits_inline_with_the_drawing_over_the_picture_and_the_tools_floating_above(app):
    show_molecule(app)
    top = app.placed[1][0]
    app.key(f"\x1b[<0;10;{top + 2}M")
    fields = app.bridge.edits[0]
    assert "box" not in fields and fields["strip"] == "1" and fields["smiles"] == "CCO"
    inline = tuple(int(n) for n in fields["inline"].split(","))
    at = tuple(int(n) for n in fields["at"].split(","))
    assert at == (top, 5, 6, 20) and inline[:2] == (top, 5) and inline[2] >= 8 and inline[3] >= 40 and top >= 3  # room above
    app.bridge.written.clear()
    app.event({"type": "text", "delta": "meanwhile"})  # the app draws nothing over the editor
    assert app.bridge.written == []
    now = app.lines()[0]
    app.edit_answer("0 S1\n\x1b]5523;repaint\x1b\\\x1b[3;4H   \x1b]5523;tools=0\x1b\\")
    written = app.bridge.written[-1]
    assert written.startswith("\x1b[1;1H" + now[0]) and written.endswith("\x1b[3;4H   ")  # the screen again, then the editor
    assert "meanwhile" in written  # as it is now
    assert "5523" not in written and app.tools_shown is False
    app.key("\x11")
    app.edit_answer("1 \nOCC")
    app.edit_answer("3 \n")
    assert app.editing is None and "\x1b[?1003l\x1b[?1002h" in "".join(app.bridge.written) and app.chips[0].full.endswith("edited: OCC⟧")
    app.key("\x15/edit 1\r")
    assert app.bridge.edits[-1]["strip"] == "0"  # the tools stay put away, as the user left them


def test_the_agent_marks_a_molecule_and_its_notes_hang_in_bubbles_beside_it(app):
    show_molecule(app)
    app.event({"type": "tool_use", "id": "k1", "name": "tui.mark",
               "input": {"mol": 1, "smarts": "CO", "note": "the alcohol", "highlight": "green"}})
    request = app.bridge.pictures[-1]
    assert request["mark"] == {"smarts": "CO", "note": "the alcohol", "highlight": "green"} and request["tool"] == "k1"
    assert "Mark(mol 1: the alcohol)" in screen(app) and app._tool("k1").state == "running"
    answer = {"png": "C" * 10, "cols": 20, "rows": 6, "smiles": "CCO |atomProp:1.atomNote.the alcohol|", "marked": [1, 2],
              "notes": [{"atom": 1, "text": "the alcohol", "row": 2}]}
    app.picture_answer(json.dumps(request), json.dumps(answer))
    assert app.bridge.results[-1] == ("k1", {"marked": [1, 2], "smiles": answer["smiles"]})
    assert app.molecules[0].smiles == answer["smiles"]
    from molterm_tui.screen import cells
    lines = ["".join(text[:1] for _, text in cells(line)) for line in app.lines()[0]]  # a character a column
    top = app.placed[1][0]
    assert lines[top + 2][25:29] == "───┤" and "the alcohol" in lines[top + 2]  # the leader carried on to the bubble, beside it
    assert lines[top + 1][28] == "╭" and lines[top + 3][28] == "╰"
    app.event({"type": "tool_use", "id": "k2", "name": "tui.mark", "input": {"mol": 9}})
    assert app.bridge.results[-1] == ("k2", {"error": "There is no molecule 9."})
    app.event({"type": "tool_use", "id": "k3", "name": "tui.mark", "input": {"mol": 1, "smarts": "["}})
    app.picture_failed(json.dumps(app.bridge.pictures[-1]), "That SMARTS can't be read.")
    assert app.bridge.results[-1] == ("k3", {"error": "That SMARTS can't be read."})


def test_the_agent_highlights_text_with_a_note_under_it(app):
    app.event({"type": "text", "delta": "The amide is the part to watch."})
    app.event({"type": "text_end"})
    app.event({"type": "tool_use", "id": "t1", "name": "tui.mark", "input": {"text": "the  amide", "note": "here"}})
    assert app.bridge.results[-1] == ("t1", {"ok": "Highlighted \"the amide\" with a note"})
    lines, _ = app.lines()
    row = next(i for i, line in enumerate(lines) if "The amide is" in strip(line))
    assert "\x1b[48;2;94;78;28m" in lines[row]  # yellow behind it
    top = next(i for i, line in enumerate(lines) if re.search("[│┤├] here   │", strip(line))) - 1  # where it covers no text
    anchor = strip(lines[row]).index("amide") - 4
    plain = [strip(line) for line in lines]
    assert top > row and plain[row + 1][anchor] in "│╯╰"  # a line leaves the text from below its first word
    between = "".join(plain[r][:12] for r in range(row + 1, top))
    assert "┴" in plain[top] and not re.search("[A-Za-z]", "".join(c for c in between if c in "│─╭╮╯╰"))  # and reaches the bubble's top
    assert "Mark(" in plain[row + 2] and "Highlighted" in plain[row + 3]  # through the blank column beside them, not over them
    app.event({"type": "tool_use", "id": "t2", "name": "tui.mark", "input": {"text": "nowhere to be found"}})
    assert app.bridge.results[-1][1] == {"error": "\"nowhere to be found\" isn't in the transcript."}
    app.event({"type": "tool_use", "id": "t3", "name": "tui.mark", "input": {"text": "", "clear": True}})
    assert app.highlights == []


def test_dragging_across_text_highlights_it_and_puts_it_in_the_message(app):
    app.event({"type": "text", "delta": "Imatinib binds the kinase in its inactive form."})
    app.event({"type": "text_end"})
    lines, _ = app.lines()
    row = next(i for i, line in enumerate(lines) if "Imatinib binds" in strip(line))
    col = strip(lines[row]).index("the kinase")
    app.key(f"\x1b[<0;{col + 1};{row + 1}M")
    app.key(f"\x1b[<32;{col + 10};{row + 1}M")
    assert "\x1b[48;2;70;70;84m" in app.lines()[0][row]  # the selection, while it is dragged
    app.key(f"\x1b[<0;{col + 10};{row + 1}m")
    assert app.input == "" and app.highlights[0].phrase == "the kinase"  # it waits above the input
    assert [c.tag for c in app.chips] == ["⟦highlighted: \"the kinase\"⟧"] and "◇ “the kinase”" in screen(app)
    click(app, chip(app))
    assert app.input == "⟦highlighted: \"the kinase\"⟧ "
    app.key("\x7f\x7f")  # the space, then the whole tag
    assert app.input == "" and len(app.chips) == 1
    click(app, chip(app))
    click(app, chip(app, "dismiss"))  # × drops it, from the text too
    assert app.input == "" and app.chips == []
    assert "\x1b[48;2;32;62;104m" in app.lines()[0][row]  # blue, the user's
    app.key(f"\x1b[<0;{col + 3};{row + 1}M\x1b[<0;{col + 3};{row + 1}m")  # a click on it takes it away
    assert app.highlights == []



def test_overlays_keep_the_styles_around_them():
    from molterm_tui.screen import overlay, paint
    line = "\x1b[31mred text here\x1b[0m"
    assert strip(overlay(line, 4, "XX")) == "red XXxt here"
    assert overlay(line, 4, "XX").endswith("\x1b[0m\x1b[31mxt here\x1b[0m")
    assert strip(overlay("ab", 5, "Z")) == "ab   Z"
    assert paint("abcdef", 1, 3, "\x1b[44m") == "a\x1b[0m\x1b[44mbc\x1b[0mdef"


def click(app, at):
    row, col = at
    app.key(f"\x1b[<0;{col + 1};{row + 1}M\x1b[<0;{col + 1};{row + 1}m")


def chip(app, part="toggle"):
    app.lines()
    row, start, _, _, _ = next(h for h in app._chip_hits if h[4] == part)
    return row, start + 1


def test_the_inline_drawing_is_resized_and_the_transcript_makes_room():
    app = App(Bridge(), 100, 50)
    app.start()
    show_molecule(app)
    app.event({"type": "text", "delta": "after the picture"})
    app.event({"type": "text_end"})
    app.key("/edit 1\r")
    app.edit_answer("0 S1\n")
    top, left, rows, cols = app.editing["inline"]

    def below():
        return next(i for i, line in enumerate(app.lines()[0]) if "after the picture" in strip(line))
    before = below()
    assert "drag this line" in strip(app.lines()[0][top + rows])  # the line under the drawing
    app.key("\x1b[1;3B")  # alt+down: two rows more, and what is below moves down to make room
    fields = app.bridge.edits[-1]
    assert fields["inline"] == f"{top},{left},{rows + 2},{cols}" and fields["state"] == "S1" and fields["events"] == ""
    assert below() == before + 2 and app.editing["inline"][0] == top  # the drawing stays where it is
    app.edit_answer("0 S2\n\x1b]5523;repaint\x1b\\")
    assert "after the picture" in app.bridge.written[-1]
    grip = top + rows + 2
    app.key(f"\x1b[<0;10;{grip + 1}M")
    app.key(f"\x1b[<32;18;{grip + 4}M")  # dragged: where the line would go
    assert "◢" in app.bridge.written[-1] and f"{rows + 5} × {cols + 8}" in strip(app.bridge.written[-1])
    app.key(f"\x1b[<0;18;{grip + 4}m")
    assert app.editing["inline"][2:] == (rows + 5, cols + 8) and app.bridge.edits[-1]["events"] == ""  # none of it went to the editor
    app.edit_answer("0 S3\n")
    app.key("x")
    assert app.bridge.edits[-1]["events"] == "78"
    app.edit_answer("0 S4\n")
    app.key("\x03")  # Cancel: the picture where the drawing was, and the transcript closes up
    app.edit_answer("3 \n")
    app.edit_answer("3 \n")
    assert app.editing is None and app.placed[1][0] == top and below() == before - (8 + 1 - 6)  # the gap and its line, less the picture


def test_bubbles_fold_hide_and_move_out_of_the_way(app):
    app.event({"type": "text", "delta": "The amide is the part to watch."})
    app.event({"type": "text_end"})
    app.event({"type": "tool_use", "id": "t1", "name": "tui.mark", "input": {"text": "amide", "note": "here"}})
    lines, _ = app.lines()
    row = next(i for i, line in enumerate(lines) if "The amide is" in strip(line))
    top = next(i for i, line in enumerate(lines) if re.search("[│┤├] here", strip(line))) - 1
    border = strip(lines[top])
    left = border.index("╭")
    click(app, (top, border.index("–")))  # folded: a mark after the text, which opens it again
    lines, _ = app.lines()
    assert not any("│ here" in strip(line) for line in lines) and "◆" in strip(lines[row])
    click(app, (row, strip(lines[row]).index("◆")))
    assert re.search("[│┤├] here", strip(app.lines()[0][top + 1]))
    app.key(f"\x1b[<0;{left + 3};{top + 2}M")  # dragged by its body, three rows down and ten across
    app.key(f"\x1b[<32;{left + 13};{top + 5}M")
    app.key(f"\x1b[<0;{left + 13};{top + 5}m")
    lines = [strip(line) for line in app.lines()[0]]
    assert lines[top + 4].index(" here") == left + 11  # its side, where the line from the text may join it
    assert "┤" in lines[top + 4] or "┴" in lines[top + 3]  # the line from the text follows it, into a side or its top
    click(app, (top + 3, lines[top + 3].index("×")))  # hidden, until /notes
    assert not any("here" in line for line in (strip(x) for x in app.lines()[0]))
    app.key("/notes\r")
    assert any(re.search("[│┤├] here", strip(line)) for line in app.lines()[0])
    app.key("/notes hide\r")
    assert not any(re.search("[│┤├] here", strip(line)) for line in app.lines()[0])


def test_a_bubble_goes_where_there_is_no_text_and_any_text_under_it_shows_faintly(app):
    for n in range(6):
        app.event({"type": "text", "delta": f"line {n} " + "word " * (14 if n % 2 else 3)})
        app.event({"type": "text_end"})
    app.event({"type": "tool_use", "id": "t1", "name": "tui.mark", "input": {"text": "line 2", "note": "a note"}})
    lines = [strip(line) for line in app.lines()[0]]
    row = next(i for i, line in enumerate(lines) if "line 2" in line)
    top = next(i for i, line in enumerate(lines) if " a note" in line and "╭" not in line) - 1
    left = lines[top].index("╭")
    covered = [app._plain(app._start + r)[left:left + 14] for r in range(top, top + 3)]
    assert not any(c.strip() for c in covered)  # the blank line between the replies, or beside them: no words covered
    assert (top, left) != (row + 1, 0)
    from molterm_tui.app import FAINT, _cover, _route
    under = "abcdefghij"
    covered = _cover(under, 2, "\x1b[48;2;44;39;30m│ x    │\x1b[0m", [1, 3, 4, 5, 6])
    assert strip(covered) == "ab│dxfghi│" and FAINT in covered  # the blank cells show what is under them, faintly
    route = _route((5, 10), "left", 2, 20, 3, 8)  # a bubble up and to the right: along, up, along, into its left side
    assert [c for _, _, c, _ in route] == ["─"] * 8 + ["╯", "│", "╭", "─", "┤"] and route[-1][:2] == (3, 20)
    route = _route((5, 10), "left", 9, 2, 3, 12)  # below and across: down, along, into its top
    assert route[0][2] == "╮" and route[-1][2] == "┴" and route[-1][0] == 9
    assert _route((5, 10), "left", 4, 8, 3, 6) == []  # it covers the start
    route = _route((5, 10), "left", 0, 0, 3, 6)  # up and to the left of a picture's edge: up beside the picture, then along
    assert [(r, c) for r, c, _, _ in route[:4]] == [(5, 10), (4, 10), (3, 10), (2, 10)] and route[-1][2] == "├"



def test_the_cursor_is_the_terminals_own_where_typing_goes(app):
    app.key("hi")
    _, (row, col) = app.lines()
    assert app.bridge.written[-1].endswith(f"\x1b[{row + 1};{col + 1}H\x1b[?25h")  # restty draws it solid while focused


def test_notes_made_while_editing_inline_hang_in_bubbles_beside_the_drawing_at_once(app):
    import base64 as b64
    show_molecule(app)
    app.key("/edit 1\r")
    top, left, rows, cols = app.editing["inline"]
    told = {"edge": left + cols, "notes": [{"text": "the OH", "row": top + 2}]}
    notice = "\x1b]5523;notes=" + b64.b64encode(json.dumps(told).encode()).decode() + "\x1b\\"
    app.edit_answer("0 S1\n" + notice + "\x1b]5523;repaint\x1b\\\x1b[1;1Heditor")
    written = app.bridge.written[-1]
    assert "the OH" in strip(written) and "5523" not in written and written.endswith("\x1b[1;1Heditor")  # in the repaint
    lines = [strip(line) for line in app.lines()[0]]
    row = next(i for i, line in enumerate(lines) if "the OH" in line)
    assert lines[row].index("the OH") > left + cols and lines[top + 2][left + cols] in "─╮╯"  # beside it, the leader carried on
    app.edit_answer("0 S2\n\x1b]5523;notes=" + b64.b64encode(json.dumps({"edge": left + cols, "notes": []}).encode()).decode()
                    + "\x1b\\\x1b]5523;repaint\x1b\\")
    assert "the OH" not in strip(app.bridge.written[-1])  # gone with the note


def test_the_pages_wheel_leaves_the_transcript_still_while_a_molecule_is_edited(app):
    show_molecule(app)
    for n in range(30):
        app.event({"type": "text", "delta": f"line {n} "})
        app.event({"type": "text_end"})
    app.key("/edit 1\r")
    app.edit_answer("0 S1\n")
    top = app.editing["inline"][0]
    scroll = app.scroll
    app.wheel(5)
    assert app.scroll == scroll and app.lines() and app.placed[1][0] == top  # the drawing's gap stays under the drawing
