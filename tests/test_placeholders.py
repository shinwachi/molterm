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
        for match in re.finditer(placeholders.PLACEHOLDER + "(.)(.)(.)", run):
            assert placeholders.DIACRITICS.index(match[3]) == placeholders.wire_id(image_id) >> 24
            cells.append((placeholders.DIACRITICS.index(match[1]), placeholders.DIACRITICS.index(match[2])))
    return cells


def test_the_table_is_kittys():
    assert len(placeholders.DIACRITICS) == 297 and placeholders.DIACRITICS[0] == "̅" and placeholders.DIACRITICS[2] == "̎"


def test_passthrough_doubles_every_escape():
    assert placeholders.wrap("\x1b_Ga=d\x1b\\") == "\x1bPtmux;\x1b\x1b_Ga=d\x1b\x1b\\\x1b\\"


def test_a_picture_is_sent_as_a_virtual_placement_in_pieces():
    sent = unwrapped(placeholders.transmit(b"x" * 10000, 7001, 20, 6))
    assert sent[1].startswith(f"\x1b_Ga=T,f=100,U=1,i={placeholders.wire_id(7001)},c=20,r=6,q=2,m=1;") and sent[-1].startswith("\x1b_Gq=2,m=0;")
    assert len(sent) == 5


def test_a_picture_sent_again_under_its_id_replaces_its_old_placement():
    # Ghostty keeps an id's placements when its picture is replaced, and sizes the placeholders by the first it finds: an old one of
    # another size would squeeze the picture off its cells. So the id goes, placements and all, before the picture comes again.
    sent = unwrapped(placeholders.transmit(b"x", 7010, 10, 22))
    assert sent[0] == f"\x1b_Ga=d,d=I,i={placeholders.wire_id(7010)},q=2\x1b" and sent[1].startswith("\x1b_Ga=T,")


def test_cells_carry_their_row_and_column_and_skip_what_is_hidden():
    text = placeholders.cells(7001, 3, 2, lambda r, c: f"<{r},{c}>", hidden=lambda r, c: (r, c) == (1, 1))
    assert placeholders.colour(7001) == f"\x1b[38;5;{7001 % 255 + 1}m"  # 256 colours, which tmux passes on as they are
    assert placeholder_cells(text, 7001) == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 2)]
    assert "<1,2>" in text  # a new run after the hidden cell starts where it should


def test_outside_tmux_nothing_changes():
    mol = molecule.parse("c1ccccc1O", "smiles").mol
    assert "Ptmux" not in graphics.render(mol)


def test_a_drawing_through_tmux_is_lines_of_placeholders():
    mol = molecule.parse("c1ccccc1O", "smiles").mol
    with placeholders.using(True):
        text = graphics.render(mol)
    commands = unwrapped(text)[1:]  # after the delete of whatever had the id
    wire = int(re.search(r"i=(\d+)", commands[0])[1])
    image = next(n for n in range(placeholders.MAX_NUMBER + 1) if placeholders.wire_id(n) == wire)
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
        # nothing changed: nothing printed; a menu opened: the menu over the cells, which stay; closed: the cells again where it was
        assert editor.step(state, "", "", 100, 30, 10, 20)[2] == ""
        _, state, output = editor.step(state, "", "\x1b[<0;2;1M\x1b[<0;2;1m", 100, 30, 10, 20)
        assert "Load" in output and placeholders.PLACEHOLDER not in output
        _, state, closed = editor.step(state, "", "\x1b", 100, 30, 10, 20)
        assert len(placeholder_cells(closed, structure)) == ui.canvas_cols * ui.canvas_rows
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


def test_ids_survive_tmux_turning_colours_into_256():
    """Each of our numbers has its own id, whose low byte is never 0 and whose high byte has a mark."""
    ids = {placeholders.wire_id(n) for n in range(placeholders.MAX_NUMBER + 1)}
    assert len(ids) == placeholders.MAX_NUMBER + 1
    assert all(i & 255 and (i >> 24) < len(placeholders.DIACRITICS) and not (i >> 8) & 0xFFFF for i in ids)


def test_through_tmux_a_changed_picture_is_never_removed_before_its_cells_move():
    """Removing the old picture would leave tmux's cells, which it redraws later, naming nothing for a moment: a flicker."""
    with placeholders.using(True):
        _, state, first = editor.step("", "CCO", "", 100, 30, 10, 20)
        _, state, hovered = editor.step(state, "", "\x1b[<35;2;5M", 100, 30, 10, 20)  # over the palette: its picture changes
        s = editor.decode(state)
        shown = placeholders.wire_id(editor._IDS["palette"] + s["flips"]["palette"])  # what the screen shows when `again` arrives
        _, state, again = editor.step(state, "", "\x1b[<35;2;9M", 100, 30, 10, 20)
        _, _, listed = board.step("", "", "\x1b[<35;5;5M", 120, 40, 10, 20, True)
    def removed_while_shown(output):
        """Ids deleted other than just before a new picture under the same id: those whose cells may still be on the screen."""
        commands = unwrapped(output)
        return [c for i, c in enumerate(commands) if c.startswith("\x1b_Ga=d")
                and not (i + 1 < len(commands) and commands[i + 1].startswith("\x1b_Ga=T") and re.search(r"i=\d+", c)[0] in commands[i + 1])]
    for output in (hovered, again):
        assert "U=1" in "".join(unwrapped(output)) and not removed_while_shown(output)
    # the picture on the screen is left alone: the next one goes under the other id, which is the only one cleared
    assert f"a=d,d=I,i={shown}," not in "".join(unwrapped(again)) and f"a=T,f=100,U=1,i={shown}," not in "".join(unwrapped(again))
    assert not removed_while_shown(listed)


def test_through_tmux_only_the_changed_picture_has_its_cells_printed():
    with placeholders.using(True):
        _, state, _ = editor.step("", "CCO", "", 100, 30, 10, 20)
        s = editor.decode(state)
        structure = editor._IDS["structure"] + s["flips"]["structure"]
        _, state, hovered = editor.step(state, "", "\x1b[<35;2;5M", 100, 30, 10, 20)  # the palette's hover changes; the drawing doesn't
        s = editor.decode(state)
        palette = editor._IDS["palette"] + s["flips"]["palette"]
    assert placeholder_cells(hovered, palette) and not placeholder_cells(hovered, structure)
