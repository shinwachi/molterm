"""Kitty graphics through tmux, as Unicode placeholders: the sequences, and the drawings, editor and list that use them."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

import board  # noqa: E402
import editor  # noqa: E402
import graphics  # noqa: E402
import molecule  # noqa: E402
import placeholders  # noqa: E402
from app import app  # noqa: E402

PASSTHROUGH = re.compile(r"\x1bPtmux;(.*?)\x1b\\", re.S)


def unwrapped(text):
    """The Kitty commands tmux would pass on, as the terminal gets them."""
    return [part.replace("\x1b\x1b", "\x1b") for part in PASSTHROUGH.findall(text)]


def placeholder_cells(text, image_id):
    """(row, col) of every placeholder cell printed in the colour of `image_id`."""
    cells = []
    for run in re.findall(re.escape(placeholders.colour(image_id)) + r"([^\x1b]*)", text):
        for match in re.finditer(placeholders.PLACEHOLDER + "(.)(.)", run):
            cells.append((placeholders.DIACRITICS.index(match[1]), placeholders.DIACRITICS.index(match[2])))
    return cells


def test_the_table_is_kittys():
    assert len(placeholders.DIACRITICS) == 297 and placeholders.DIACRITICS[0] == "̅" and placeholders.DIACRITICS[2] == "̎"


def test_passthrough_doubles_every_escape():
    assert placeholders.wrap("\x1b_Ga=d\x1b\\") == "\x1bPtmux;\x1b\x1b_Ga=d\x1b\x1b\\\x1b\\"


def test_a_picture_is_sent_as_a_virtual_placement_in_pieces():
    sent = unwrapped(placeholders.transmit(b"x" * 10000, 7001, 20, 6))
    assert sent[0].startswith("\x1b_Ga=T,f=100,U=1,i=7001,c=20,r=6,q=2,m=1;") and sent[-1].startswith("\x1b_Gq=2,m=0;")
    assert len(sent) == 4


def test_cells_carry_their_row_and_column_and_skip_what_is_hidden():
    text = placeholders.cells(0x123456, 3, 2, lambda r, c: f"<{r},{c}>", hidden=lambda r, c: (r, c) == (1, 1))
    assert placeholders.colour(0x123456) == "\x1b[38;2;18;52;86m"
    assert placeholder_cells(text, 0x123456) == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 2)]
    assert "<1,2>" in text  # a new run after the hidden cell starts where it should


def test_outside_tmux_nothing_changes():
    mol = molecule.parse("c1ccccc1O", "smiles").mol
    assert "Ptmux" not in graphics.render(mol)


def test_a_drawing_through_tmux_is_lines_of_placeholders():
    mol = molecule.parse("c1ccccc1O", "smiles").mol
    with placeholders.using(True):
        text = graphics.render(mol)
    commands = unwrapped(text)
    image = int(re.search(r"i=(\d+)", commands[0])[1])
    cols, rows = int(re.search(r"c=(\d+)", commands[0])[1]), int(re.search(r"r=(\d+)", commands[0])[1])
    cells = placeholder_cells(text, image)
    assert len(cells) == cols * rows and cells[-1] == (rows - 1, cols - 1)
    assert text.count("\n") == rows  # a line each, as text that scrolls


def test_the_endpoint_takes_placeholders():
    client = app.test_client()
    text = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "placeholders": "1"}).get_data(as_text=True)
    assert text.startswith("\x1bPtmux;") and placeholders.PLACEHOLDER in text
    assert client.post("/api/v1/edit", data={"cols": 100, "rows": 30, "placeholders": "1"}).get_data(as_text=True).count("\x1bPtmux;") > 0


def test_the_editor_through_tmux_draws_its_pictures_as_cells_under_its_menus():
    with placeholders.using(True):
        status, state, output = editor.step("", "CCO", "", 100, 30, 10, 20)
        s = editor.decode(state)
        ui = editor.Layout(100, 30, 10, 20)
        structure, palette = editor._IDS["structure"] + s["flips"]["structure"], editor._IDS["palette"] + s["flips"]["palette"]
        assert "U=1" in "".join(unwrapped(output)) and "\x1b_G" not in PASSTHROUGH.sub("", output)
        assert len(placeholder_cells(output, structure)) == ui.canvas_cols * ui.canvas_rows
        assert len(placeholder_cells(output, palette)) == ui.per_row * ui.button_cols * ui.palette_rows
        # nothing changed: nothing printed; a menu opened: the cells again, then the menu over them
        assert editor.step(state, "", "", 100, 30, 10, 20)[2] == ""
        _, state, output = editor.step(state, "", "\x1b[<0;2;1M\x1b[<0;2;1m", 100, 30, 10, 20)
        assert output.index(placeholders.PLACEHOLDER) < output.index("Load")
        # no second picture laid over the drawing: the selection marks go into it
        assert "7005" not in "".join(unwrapped(output)) and "7020" not in "".join(unwrapped(output))


def test_the_list_through_tmux_prints_tile_cells_before_its_labels():
    with placeholders.using(True):
        _, state, output = board.step("", "", "", 120, 40, 10, 20, True)
    sent = "".join(unwrapped(output))
    assert sent.count("U=1") >= 3 and "Ptmux" not in sent
    first = board._FIRST_ID
    assert placeholder_cells(output, first)
    assert output.index(placeholders.colour(first)) < output.index("Aspirin")
