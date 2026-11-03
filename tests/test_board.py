"""The editor in a box, and the list of structures that hosts it, driven with the raw bytes a terminal would send."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
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
        box = board.box_for(self.cols, self.rows)
        return editor.Layout(self.cols, self.rows, CW, CH, self.pixels, box, "")

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
        assert session.state["editing"] is None and session.state["items"][0][1:] == ["CCO", True, False]
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
    state = board.new_state([["n", "C", True, False]])
    drawing = editor.new_state()
    drawing["undo"] = [[[[i, "C", float(i), float(i * j) / 7.0, 0] for i in range(1, 200)], [], 200] for j in range(30)]
    state["editing"] = {"index": 0, "state": drawing, "start": "C"}
    state["message"] = big
    text = board._encode(state)
    assert len(text) <= editor.MAX_STATE
    assert editor.decode(text, board._check_state)["editing"] is not None


def test_a_tampered_state_is_refused():
    state = board.new_state([["n", "C", True, False]])
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
    assert board._handle_list(b, grid, ("mouse", 35, 45.5, 5.5, False), 120, 38, (10, 20, False), False) is False
    assert b["cursor"] == 1 and b["message"] == ""
