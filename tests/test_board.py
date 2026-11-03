"""The editor in a box, and the list of structures that hosts it, driven with the raw bytes a terminal would send."""
import base64
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import annotate  # noqa: E402
import board  # noqa: E402
import editor  # noqa: E402
from app import app  # noqa: E402

COLS, ROWS, CW, CH = 130, 44, 10, 20
PLACE = re.compile(r"\x1b\[(\d+);(\d+)H")


def click(col, row):
    """Mouse reports count from 1."""
    return f"\x1b[<0;{col + 1};{row + 1}M\x1b[<0;{col + 1};{row + 1}m"


def hover(col, row):
    return f"\x1b[<35;{col + 1};{row + 1}M"


class List:
    def __init__(self, items="CCO ethanol\nro:c1ccccc1 benzene\nCC(=O)O", cols=COLS, rows=ROWS, pixels=False):
        self.cols, self.rows, self.pixels = cols, rows, pixels
        self.text, self.output, self.status = "", "", None
        self.send("", items)

    def send(self, raw, items=""):
        self.status, text, self.output = board.step(self.text, items, raw, self.cols, self.rows, CW, CH, self.pixels)
        if text:
            self.text = text
        return self

    @property
    def state(self):
        return editor.decode(self.text, board._check_state)

    @property
    def drawing(self):
        return self.state["editing"]["state"]

    @property
    def ui(self):
        """Where the window is now."""
        return board._box_layout(self.state, self.cols, self.rows, (CW, CH, self.pixels))

    def tile(self, index):
        grid = board.Grid(self.cols, self.rows)
        row, col = grid.tile(index, self.state["top"])
        return col + 5, row + 3

    def open(self, index=0):
        return self.send(click(*self.tile(index)))

    def screen_cell(self, x, y):
        """Where a point of the drawing is on the screen."""
        ui = self.ui
        col, row = ui.to_cell(self.drawing, x, y)
        return int(col) + ui.origin[1], int(row) + ui.origin[0]

    def point_at_atom(self, index):
        return self.send(hover(*self.screen_cell(*self.drawing["atoms"][index][2:4])))

    def button(self, name):
        ui = self.ui
        col = next(start for label, _, start in ui.buttons() if label == name)
        return self.send(click(col + ui.origin[1] + 1, ui.rows + ui.origin[0]))


def test_the_list_shows_each_structure_with_its_own_image():
    output = List().output
    for index in range(3):
        assert f"i={board._FIRST_ID + index}," in output
    assert "ethanol" in output and "read-only" in output


def test_clicking_an_editable_structure_opens_the_box_with_it():
    session = List().open(0)
    assert session.state["editing"]["index"] == 0
    assert sorted(a[1] for a in session.drawing["atoms"]) == ["C", "C", "O"]
    assert "Edit ethanol" in session.output and "╭" in session.output and "Done" in session.output


def test_the_list_says_nothing_of_its_own_beside_the_box():
    session = List()
    session.send(hover(*session.tile(0)))
    assert "Click to edit" in session.output
    assert "Click to edit" not in session.open(0).output


def test_a_press_alone_does_not_open_it_nor_does_a_drag_to_another_tile():
    session = List()
    col, row = session.tile(0)
    session.send(f"\x1b[<0;{col + 1};{row + 1}M")
    assert session.state["editing"] is None
    other = session.tile(2)
    session.send(f"\x1b[<0;{other[0] + 1};{other[1] + 1}m")
    assert session.state["editing"] is None


def test_clicking_a_read_only_structure_says_so_and_does_not_open():
    session = List().open(1)
    assert session.state["editing"] is None and "read-only" in session.output


def test_keyboard_opens_the_box_too():
    session = List().send("\x1b[C").send("\x1b[C\x1b[C")  # the first arrow lands on the first tile
    assert session.state["cursor"] == 2
    assert session.send("\r").state["editing"]["index"] == 2


def test_hovering_marks_the_tile_under_the_pointer():
    session = List()
    output = session.send(hover(*session.tile(0))).output
    assert session.state["cursor"] == 0 and "╭" in output
    assert session.send(hover(0, 0)).state["cursor"] is None


def test_nothing_from_the_box_lands_outside_it():
    session = List().open(0)
    row0, col0, rows, cols = board.box_for(COLS, ROWS)
    for raw in ("", hover(*session.screen_cell(*session.drawing["atoms"][0][2:4])), "?", "?"):
        session.send(raw)
        for row, col in PLACE.findall(session.output):
            assert row0 < int(row) <= row0 + rows and col0 < int(col) <= col0 + cols
        assert not re.search(r"\x1b\[[012]?[JK]", session.output)


def test_editing_through_the_box_lands_on_the_atom_under_the_pointer():
    session = List().open(0)
    carbon = next(i for i, atom in enumerate(session.drawing["atoms"]) if atom[1] == "C")
    session.point_at_atom(carbon)
    assert session.drawing["hover"] == ["a", session.drawing["atoms"][carbon][0]]
    session.send("n")
    assert session.drawing["atoms"][carbon][1] == "N"


def test_pixel_mouse_reports_land_on_the_atom_too():
    session = List(pixels=True)
    col, row = session.tile(0)
    session.send(f"\x1b[<0;{col * CW + 3};{row * CH + 3}M\x1b[<0;{col * CW + 3};{row * CH + 3}m")
    atom = session.drawing["atoms"][0]
    ui = session.ui
    x, y = ui.to_cell(session.drawing, atom[2], atom[3])
    session.send(f"\x1b[<35;{int((x + ui.origin[1]) * CW)};{int((y + ui.origin[0]) * CH)}M")
    assert session.drawing["hover"] == ["a", atom[0]]


def test_done_takes_the_edit_back_into_the_list():
    session = List().open(0)
    carbon = next(i for i, atom in enumerate(session.drawing["atoms"]) if atom[1] == "C")
    session.point_at_atom(carbon).send("n")
    session.button("done")
    state = session.state
    assert state["editing"] is None and state["items"][0][3] is True and "N" in state["items"][0][1]
    assert state["items"][0][1] != "CCO"
    assert "Saved" in session.output and "edited" in session.output
    assert "a=d,d=I,i=7001" in session.output  # the editor's images go
    assert f"i={board._FIRST_ID}," in session.output  # and the picture is new


def test_ctrl_q_is_done_as_well():
    session = List().open(0)
    session.point_at_atom(0).send("n").send("\x11")
    assert session.state["editing"] is None and session.state["items"][0][3] is True


def test_cancel_and_ctrl_c_leave_the_structure_alone():
    for leave in (lambda s: s.button("cancel"), lambda s: s.send("\x03")):
        session = List().open(0)
        session.point_at_atom(0).send("n")
        leave(session)
        assert session.state["editing"] is None and session.state["items"][0][1:4] == ["CCO", True, False]
        assert "Cancelled" in session.output and session.status == editor.CONTINUE


def test_the_file_menu_offers_done_and_cancel_in_a_box():
    session = List().open(0)
    ui = session.ui
    output = session.send(click(ui.titles[0][0] + 1 + ui.origin[1], ui.origin[0])).output
    assert "Done" in output and "Cancel" in output and "Quit and print" not in output


def test_leaving_a_drawing_as_it_was_keeps_its_smiles_exactly():
    """The editor doesn't carry isotopes, so saving this through it would lose them."""
    session = List("[13CH3]O labelled").open(0).send("\x11")
    state = session.state
    assert state["items"][0][1] == "[13CH3]O" and state["items"][0][3] is False and "No change" in session.output


def test_stereo_survives_opening_and_closing():
    morphine = "CN1CC[C@]23c4c5ccc(O)c4O[C@H]2[C@@H](O)C=C[C@H]3[C@H]1C5"
    session = List(morphine).open(0).send("\x11")
    assert session.state["items"][0][1] == morphine and session.state["items"][0][3] is False


def test_an_invalid_drawing_stays_open_and_can_still_be_cancelled():
    session = List("O water").open(0)
    atom = session.drawing["atoms"][0]
    session.send(hover(*session.screen_cell(*atom[2:4])))
    for _ in range(3):
        session.send("1")
        session.send(hover(*session.screen_cell(*atom[2:4])))
    assert session.send("\x11").state["editing"] is not None
    assert "Not saved" in session.output
    session.button("cancel")
    assert session.state["editing"] is None and session.state["items"][0][1] == "O"


def test_an_empty_drawing_is_not_saved():
    session = List("C methane").open(0)
    session.send("\x01").send("\x7f").send("\x11")
    state = session.state
    assert state["editing"] is None and state["items"][0][1] == "C" and "empty" in session.output.lower()


def test_a_click_outside_the_box_does_nothing():
    """Not on the menus or the palette either, which are nearest the top left corner."""
    session = List().open(0)
    before = session.drawing
    row, col, _, _ = board.box_for(COLS, ROWS)
    for place in ((0, 0), (col - 1, row), (1, row - 1), (COLS - 1, ROWS - 1), (col + 3, row)):
        session.send(click(*place))
        after = session.drawing
        assert session.state["editing"] and after["atoms"] == before["atoms"] and after["menu"] is None and after["tool"] == before["tool"]


def test_the_box_opens_in_a_small_window_by_using_all_of_it():
    assert board.box_for(60, 20) == (0, 0, 20, 60)
    assert List(cols=60, rows=20).open(0).state["editing"] is not None


def test_resizing_with_the_box_open_repaints_both():
    session = List().open(0)
    session.cols, session.rows = 110, 40
    output = session.send("").output
    assert "\x1b[2J" in output and f"i={board._FIRST_ID}," in output and "i=7020" in output and "Edit ethanol" in output
    assert session.state["editing"] is not None


def test_a_quiet_request_prints_nothing():
    session = List().send(hover(0, 0))
    assert session.send("").output == ""
    session.open(0).send("")
    assert session.send("").output == ""


def test_quitting_prints_the_list_as_it_stands():
    session = List().open(0)
    session.point_at_atom(0).send("n").send("\x11")
    session.send("q")
    lines = session.output.split("\n")
    assert session.status == editor.QUIT and len(lines) == 3
    assert lines[0].endswith(" ethanol") and lines[1] == "c1ccccc1 benzene" and lines[2] == "CC(=O)O"


def test_no_list_gives_samples_with_a_read_only_one():
    items = List("").state["items"]
    assert len(items) > 3 and not items[0][2] and all(item[2] for item in items[1:])


def test_a_bad_structure_is_named():
    session = List("not_a_molecule")
    assert session.status == editor.FAILED and "not_a_molecule" in session.output


def test_too_many_structures_is_refused():
    assert List("\n".join(["C"] * (board.MAX_ITEMS + 1))).status == editor.FAILED


def test_a_name_cannot_send_escape_sequences():
    session = List("CCO \x1b]0;evil\x07name")
    assert "\x1b]0" not in session.output and "\x07" not in session.output
    assert "\x1b]0" not in session.open(0).output


def test_a_title_cannot_send_escape_sequences():
    _, _, output = editor.step("", "CCO", "", COLS, ROWS, CW, CH, box=(2, 2, 30, 80), title="\x1b]0;evil\x07")
    assert "\x1b]0" not in output and "\x07" not in output


def test_the_wheel_scrolls_and_pictures_follow():
    session = List("\n".join(f"{'C' * (n + 1)} n{n}" for n in range(30)))
    grid = board.Grid(COLS, ROWS)
    first = session.output
    assert f"i={board._FIRST_ID}," in first and f"i={board._FIRST_ID + grid.per_row * grid.visible_rows}," not in first
    output = session.send("\x1b[<65;5;5M").output
    assert session.state["top"] == 1
    assert f"a=d,d=I,i={board._FIRST_ID}," in output
    assert f"i={board._FIRST_ID + grid.per_row * grid.visible_rows}," in output
    for _ in range(30):
        session.send("\x1b[<65;5;5M")
    assert session.state["top"] == grid.last_top(30)
    assert session.send("\x1b[<64;5;5M").state["top"] == grid.last_top(30) - 1


def test_the_wheel_marks_what_it_brings_under_the_pointer():
    session = List("\n".join(f"{'C' * (n + 1)} n{n}" for n in range(30)))
    grid = board.Grid(COLS, ROWS)
    col, row = session.tile(0)
    session.send(hover(col, row)).send(f"\x1b[<65;{col + 1};{row + 1}M")
    assert session.state["cursor"] == grid.per_row


def test_arrows_scroll_to_keep_the_cursor_in_sight():
    session = List("\n".join(f"{'C' * (n + 1)}" for n in range(30)))
    grid = board.Grid(COLS, ROWS)
    session.send("\x1b[B")
    for _ in range(grid.visible_rows):
        session.send("\x1b[B")
    assert session.state["top"] == 1
    assert grid.tile(session.state["cursor"], session.state["top"]) is not None


def test_a_window_too_small_for_the_list_says_so_and_recovers():
    session = List(rows=8)
    assert "too small" in session.output and session.status == editor.CONTINUE
    session.rows = ROWS
    assert "ethanol" in session.send("").output


def test_a_large_drawing_gives_up_old_undo_steps_rather_than_the_session():
    big = "".join(chr(ord("a") + i % 26) for i in range(4000))
    state = board.new_state([["n", "C", True, False, annotate.empty()]])
    drawing = editor.new_state()
    drawing["undo"] = [[[[i, "C", float(i), float(i * j) / 7.0, 0] for i in range(1, 200)], [], 200, annotate.empty()] for j in range(30)]
    state["editing"] = {"index": 0, "state": drawing, "start": "C", "box": [0, 0, 30, 100], "restore": None, "minimized": False, "drag": None}
    state["message"] = big
    text = board._encode(state)
    assert len(text) <= editor.MAX_STATE
    assert editor.decode(text, board._check_state)["editing"] is not None


def test_a_tampered_state_is_refused():
    state = board.new_state([["n", "C", True, False, annotate.empty()]])
    bad = [dict(state, cursor=5), dict(state, top=-1), dict(state, items=[]), dict(state, shown={"x": "y"}),
           dict(state, editing={"index": 0, "state": {}, "start": None}), dict(state, items=[["n", "C", False, False]], editing={"index": 0, "state": editor.new_state(), "start": None})]
    for tampered in bad:
        response = app.test_client().post("/api/v1/tui", data={"cols": COLS, "rows": ROWS, "state": editor.encode(tampered)})
        assert response.status_code == 400


def test_the_routes():
    client = app.test_client()
    response = client.post("/api/v1/tui", data={"cols": COLS, "rows": ROWS, "items": "CCO one"})
    assert response.status_code == 200 and response.text.startswith("0 ") and "one" in response.text
    response = client.post("/api/v1/edit", data={"cols": COLS, "rows": ROWS, "smiles": "CCO", "box": "2,3,30,90", "title": "Mine"})
    assert response.status_code == 200 and "Mine" in response.text and "\x1b[3;4H" in response.text
    assert client.post("/api/v1/edit", data={"cols": COLS, "rows": ROWS, "box": "nonsense"}).status_code == 400
    response = client.post("/api/v1/edit", data={"cols": COLS, "rows": ROWS, "box": "40,0,30,90"})
    assert response.text.startswith("2 ") and "fit" in response.text
    response = client.post("/api/v1/edit", data={"cols": COLS, "rows": ROWS, "box": "0,0,6,30"})
    assert response.text.startswith("2 ") and "too small" in response.text


def test_a_box_reports_cancel_to_any_host():
    status, text, _ = editor.step("", "CCO", "", COLS, ROWS, CW, CH, box=(2, 3, 30, 90))
    assert status == editor.CONTINUE
    status, _, output = editor.step(text, "", "\x03", COLS, ROWS, CW, CH, box=(2, 3, 30, 90))
    assert status == editor.CANCELLED and output == ""


def test_ctrl_c_without_a_box_still_prints_the_smiles():
    status, text, _ = editor.step("", "CCO", "", COLS, ROWS, CW, CH)
    assert editor.step(text, "", "\x03", COLS, ROWS, CW, CH)[::2] == (editor.QUIT, "CCO")


def test_moving_to_another_tile_clears_the_message():
    b = board.new_state(board.parse_items("CCO one\nCCC two"))
    b["message"], b["cursor"] = "Something happened.", 0
    grid = board.Grid(120, 38)
    assert board._handle_list(b, grid, ("mouse", 35, 45.5, 5.5, False), 120, 38, (10, 20, False), False, []) is False
    assert b["cursor"] == 1 and b["message"] == ""


def _copies(output):
    return [base64.b64decode(text).decode() for text in re.findall(r"\x1b\]52;c;([A-Za-z0-9+/=]*)\x1b\\", output)]


def _hover_report(col, row):
    return f"\x1b[<35;{col + 1};{row + 1}M"


def test_c_copies_the_structure_under_the_pointer_even_read_only():
    _, state, _ = board.step("", "ro:CCO ethanol\nCCN", "", 100, 30)
    _, state, _ = board.step(state, "", _hover_report(5, 3), 100, 30)
    _, state, output = board.step(state, "", "c", 100, 30)
    assert _copies(output) == ["CCO"]
    assert "ethanol went to the clipboard" in output
    assert "]52;" not in zlib.decompress(base64.urlsafe_b64decode(state)).decode()


def test_c_without_a_structure_asks_for_one():
    _, state, _ = board.step("", "CCO", "", 100, 30)
    _, state, output = board.step(state, "", "c", 100, 30)
    assert _copies(output) == []
    assert "Point at a structure" in output


def test_clicking_the_copy_label_copies_and_does_not_open_the_editor():
    grid = board.Grid(100, 30)
    row, col = grid.copy_label(0, 0)
    _, state, _ = board.step("", "CCO", "", 100, 30)
    _, state, output = board.step(state, "", _hover_report(col + 2, row), 100, 30)
    assert board.COPY_LABEL in output
    _, state, output = board.step(state, "", f"\x1b[<0;{col + 3};{row + 1}M\x1b[<0;{col + 3};{row + 1}m", 100, 30)
    assert _copies(output) == ["CCO"]
    assert editor.decode(state, board._check_state)["editing"] is None


def test_the_window_copy_button_copies_the_drawing_and_keeps_it_open():
    _, state, _ = board.step("", "CCO", "", 100, 30)
    _, state, _ = board.step(state, "", "\x1b[C\r", 100, 30)
    b = editor.decode(state, board._check_state)
    assert b["editing"]
    ui = board._box_layout(b, 100, 30, (10, 20, False))
    _, label, start = next(button for button in ui.buttons() if button[0] == "copy")
    col, row = start + ui.origin[1] + 2, ui.rows + ui.origin[0]
    _, state, output = board.step(state, "", f"\x1b[<0;{col + 1};{row + 1}M\x1b[<0;{col + 1};{row + 1}m", 100, 30)
    assert _copies(output) == ["CCO"]
    assert "sent to the clipboard" in output
    assert editor.decode(state, board._check_state)["editing"]


def test_the_editor_cannot_copy_an_empty_drawing():
    _, state, _ = editor.step("", "", "", 100, 30)
    s = editor.decode(state)
    editor._copy(s)
    assert "Nothing to copy" in s["message"]
    assert editor.take_clipboard(s) == ""


def test_copy_is_in_the_file_menu():
    assert ("Copy SMILES", "", ("copy", ("smiles", False))) in editor.MENUS[0][1]


# The window: moved, resized, minimized and maximized by the list, as a window manager would


def press(col, row):
    return f"\x1b[<0;{col + 1};{row + 1}M"


def drag_to(col, row):
    return f"\x1b[<32;{col + 1};{row + 1}M"


def let_go(col, row):
    return f"\x1b[<0;{col + 1};{row + 1}m"


def window(session):
    return list(session.state["editing"]["box"])


def control(session, name):
    """Where a button on the window's top edge is on the screen."""
    b = session.state
    ui = board._box_layout(b, session.cols, session.rows, (CW, CH, session.pixels))
    _, label, col = next(c for c in ui.controls() if c[0] == name)
    return col + ui.origin[1] + 1, ui.origin[0] - 1


def test_the_window_has_buttons_to_minimize_and_maximize_and_a_grip():
    output = List().open(0).output
    assert " _ " in output and " □ " in output and editor.RESIZE_GRIP in output


def test_dragging_the_title_bar_moves_the_window_and_the_drawing_with_it():
    session = List(cols=200, rows=60).open(0)
    top, left, height, width = window(session)
    session.send(press(left + 20, top) + drag_to(left + 17, top + 2))
    assert window(session) == [top + 2, left - 3, height, width]
    output = session.send(drag_to(left + 15, top + 1) + let_go(left + 15, top + 1)).output
    assert window(session) == [top + 1, left - 5, height, width]
    assert session.state["editing"]["drag"] is None
    assert "i=7020" in output and f"\x1b[{top + 2};{left - 5 + 1}H" in output  # its backing, and its corner, at the new place
    # the drawing still answers where it is shown now
    session.point_at_atom(0)
    assert session.drawing["hover"] == ["a", session.drawing["atoms"][0][0]]
    session.send("n")
    assert sorted(a[1] for a in session.drawing["atoms"]) == ["C", "N", "O"]


def test_the_window_stays_on_the_screen():
    session = List(cols=200, rows=60).open(0)
    top, left, height, width = window(session)
    session.send(press(left + 20, top) + drag_to(0, 0) + let_go(0, 0))
    assert window(session) == [0, 0, height, width]
    session.send(press(20, 0) + drag_to(199, 59) + let_go(199, 59))
    assert window(session) == [60 - height, 200 - width, height, width]


def test_dragging_the_grip_or_an_edge_resizes_it_but_not_below_what_the_editor_needs():
    session = List().open(0)
    top, left, height, width = window(session)
    corner = (left + width - 1, top + height - 1)
    session.send(press(*corner) + drag_to(corner[0] - 10, corner[1] - 4) + let_go(corner[0] - 10, corner[1] - 4))
    assert window(session) == [top, left, height - 4, width - 10]
    right = left + width - 11
    session.send(press(right, top + 5) + drag_to(right + 6, top + 9) + let_go(right + 6, top + 9))
    assert window(session) == [top, left, height - 4, width - 4]
    bottom = top + height - 5
    session.send(press(left + 3, bottom) + drag_to(left + 9, bottom + 2) + let_go(left + 9, bottom + 2))
    assert window(session) == [top, left, height - 2, width - 4]
    session.send(press(left + width - 5, top + height - 3) + drag_to(0, 0) + let_go(0, 0))
    smallest = window(session)
    assert smallest[2] < height and smallest[3] < width
    assert editor.fits(board._box_layout(session.state, COLS, ROWS, (CW, CH, False)))
    assert session.send("e").state["editing"]  # still an editor


def test_the_editors_own_buttons_on_the_bottom_edge_still_work():
    session = List().open(0)
    session.button("cancel")
    assert session.state["editing"] is None


def test_maximize_fills_the_screen_and_a_second_press_puts_it_back():
    session = List().open(0)
    before = window(session)
    session.send(click(*control(session, "maximize")))
    assert window(session) == [0, 0, ROWS, COLS]
    session.send(click(*control(session, "maximize")))
    assert window(session) == before


def test_minimizing_shows_the_list_with_the_window_in_the_top_bar():
    session = List().open(0)
    session.point_at_atom(0).send("n")
    output = session.send(click(*control(session, "minimize"))).output
    state = session.state
    assert state["editing"]["minimized"] and "□ Edit ethanol" in output and "minimized" in output
    assert "i=7020" in output and "a=d" in output  # the editor's images went
    # the list is usable meanwhile, but quitting or opening another would lose the edit
    session.send(hover(*session.tile(2)))
    assert session.state["cursor"] == 2
    assert session.send("c").output.count("\x1b]52;c;") == 1
    session.send(click(*session.tile(2)))
    assert session.state["editing"]["index"] == 0 and "still open" in session.output
    session.send("q")
    assert session.status == editor.CONTINUE and "still open" in session.output
    # clicking it in the top bar brings it back as it was
    chip_col, _ = board._chip(session.state, board.Grid(COLS, ROWS))
    output = session.send(click(chip_col + 2, 0)).output
    assert not session.state["editing"]["minimized"] and "i=7020" in output
    assert sorted(a[1] for a in session.drawing["atoms"]) == ["C", "N", "O"]


def test_opening_the_minimized_structure_again_brings_it_back():
    session = List().open(0)
    session.send(click(*control(session, "minimize")))
    session.send(click(*session.tile(0)))
    assert session.state["editing"] and not session.state["editing"]["minimized"]


def test_hovering_a_window_button_marks_it():
    session = List().open(0)
    col, row = control(session, "minimize")
    session.send(hover(col, row))
    assert session.drawing["button"] == "minimize"


def test_a_smaller_terminal_keeps_the_window_inside_it():
    session = List().open(0)
    top, left, height, width = window(session)
    session.send(press(left + 20, top) + drag_to(COLS, ROWS) + let_go(COLS, ROWS))
    session.cols, session.rows = 100, 36
    session.send("")
    top, left, height, width = window(session)
    assert top + height <= 36 and left + width <= 100
    assert editor.fits(board._box_layout(session.state, 100, 36, (CW, CH, False)))


def test_a_maximized_window_follows_the_terminals_size():
    session = List().open(0)
    session.send(click(*control(session, "maximize")))
    session.cols, session.rows = 110, 40
    session.send("")
    assert window(session) == [0, 0, 40, 110]


def test_a_tampered_window_is_refused():
    session = List().open(0)
    for change in ({"box": [0, 0, -1, 5]}, {"minimized": "yes"}, {"drag": {"mode": "fly", "from": [0, 0], "box": [0, 0, 30, 90]}},
                   {"drag": {"mode": "move", "from": ["x", 0], "box": [0, 0, 30, 90]}}, {"restore": [1, 2]}):
        b = session.state
        b["editing"].update(change)
        try:
            editor.decode(editor.encode(b), board._check_state)
        except editor.StateError:
            continue
        raise AssertionError(change)


def test_a_box_for_another_host_has_no_window_buttons():
    _, _, output = editor.step("", "CCO", "", COLS, ROWS, CW, CH, box=(2, 3, 30, 90))
    assert " _ " not in output and editor.RESIZE_GRIP not in output
