"""The plugin, against a stand-in for the molterm server: how cells are drawn, where pictures go, and the commands."""
import json
import os
import struct
import sys
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import visidata  # noqa: E402
from visidata import ColumnItem, Sheet, vd  # noqa: E402

import molterm_visidata as plugin  # noqa: E402

TEXT = "⠀O⠀\nH⠒⠒"
COLOURS = [[None, 31, None], [34, None, None]]
PROPS = {"mw": 46.07, "logp": -0.0014, "tpsa": 20.23, "hbd": 1, "hba": 1, "rotb": 0, "qed": 0.41, "ro5": 0, "pains": 0, "brenk": 0}


def png(width, height):
    """A real PNG header of this size, which is all the plugin reads."""
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + header + struct.pack(">I", zlib.crc32(b"IHDR" + header))


class Server:
    """Answers as the molterm server would, and remembers what it was asked."""

    def __init__(self):
        self.asked, self.old = [], False

    def __call__(self, path, params=None, body=None):
        self.asked.append((path, params, body))
        smiles = (body or params or {}).get("input") if "inputs" not in (body or {}) else None
        if smiles == "bad":
            raise plugin.MoltermError("Not valid SMILES")
        if path == "/api/v1/render":
            return json.dumps({"formula": "C2H6O", "render": {"text": TEXT, "colors": COLOURS}, "properties": PROPS}).encode(), {}
        if path == "/api/v1/properties":
            if self.old:
                raise plugin.MoltermError("has no /api/v1/properties")
            return json.dumps({"results": [{"error": {"code": "bad", "message": "Not valid SMILES"}} if s == "bad" else
                                           {"formula": "C2H6O", "properties": PROPS} for s in body["inputs"]]}).encode(), {}
        if path == "/api/v1/render.png":
            return png(80, 75), {"X-Molterm-Cols": "8"}
        raise AssertionError(path)


@pytest.fixture
def server(monkeypatch):
    stand_in = Server()
    monkeypatch.setattr(plugin, "_request", stand_in)
    plugin._store.clear()
    plugin._pending.clear()
    plugin.pictures.placed, plugin.pictures.sent = [], {}
    return stand_in


@pytest.fixture
def sheet():
    vs = Sheet("drugs", rows=[["ethanol", "CCO"], ["broken", "bad"], ["nothing", ""]],
               columns=[ColumnItem("name", 0), ColumnItem("smiles", 1)])
    vs.cursorVisibleColIndex = 1
    return vs


def wait_for(key):
    for _ in range(200):
        if key in plugin._store:
            return plugin._store[key]
        __import__("time").sleep(0.01)
    raise AssertionError(f"never fetched: {key}")


# the colours and the lines of a drawing


def test_braille_keeps_element_colours_within_a_line():
    lines = plugin.text_lines({"render": {"text": TEXT, "colors": COLOURS}})
    assert lines == [[["", "⠀"], ["red", "O"], ["", "⠀"]], [["blue", "H"], ["", "⠒⠒"]]]


def test_a_drawing_keeps_its_lines_where_visidata_would_wrap_each_chunk():
    drawing = plugin.Drawing([["", "CCO"]], [[["red", "O"], ["", "⠒"]], [["", "⠒⠒"]]])
    assert visidata.sheets._splitcell(Sheet("cells"), drawing, width=10, maxheight=5) == drawing.lines
    assert visidata.sheets._splitcell(Sheet("cells"), drawing, width=10, maxheight=1) == drawing.lines[:1]
    # anything else still wraps as VisiData wraps it
    plain = (("", "plain text"),)  # VisiData's own displays are hashable, as its drawing cache needs
    assert visidata.sheets._splitcell(Sheet("cells"), plain, width=0, maxheight=1) == [plain]


@pytest.mark.parametrize("environment, expected", [
    ({"TERM": "xterm-kitty"}, "kitty"), ({"TERM": "xterm-ghostty"}, "kitty"), ({"TERM": "xterm-256color", "TERM_PROGRAM": "ghostty"}, "kitty"),
    ({"TERM": "xterm-256color"}, "text"), ({"TERM": "xterm-kitty", "TMUX": "/tmp/tmux"}, "text")])
def test_pictures_only_where_the_terminal_shows_them(monkeypatch, environment, expected):
    for name in ("TERM", "TERM_PROGRAM", "TMUX", "STY", "KITTY_WINDOW_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    vd.options.molterm_mode = "auto"
    assert plugin.mode() == expected
    vd.options.molterm_mode = "text"
    assert plugin.mode() == "text"


# the structure column


def test_structure_column_draws_braille_once_the_server_answers(server, sheet):
    vd.options.molterm_mode = "text"
    col = sheet.addcolMolecule()
    assert col.source.name == "smiles" and col.height == vd.options.molterm_rows
    assert sheet.columns.index(col) == 2
    assert vd.options._get("disp_multiline_focus", sheet).value is False
    shown = col.display(col.getCell(sheet.rows[0]), 20)
    assert list(shown) == [["", "CCO"]] and shown.lines == plugin._note("drawing…")
    wait_for(("text", "CCO", 18, col.height))
    shown = col.display(col.getCell(sheet.rows[0]), 20)
    assert shown.lines[0] == [["", "⠀"], ["red", "O"], ["", "⠀"]]
    assert server.asked[0][2] == {"input": "CCO", "cols": 18, "rows": col.height, "mode": "braille", "properties": True}


def test_a_smiles_the_server_refuses_shows_its_message(server, sheet):
    vd.options.molterm_mode = "text"
    col = sheet.addcolMolecule()
    col.display(col.getCell(sheet.rows[1]), 20)
    wait_for(("text", "bad", 18, col.height))
    shown = col.display(col.getCell(sheet.rows[1]), 20)
    assert shown.lines == [[["color_error", "Not valid SMILES"]]]
    assert col.display(col.getCell(sheet.rows[2]), 20).lines == []


def test_in_kitty_mode_the_cell_is_left_blank_for_the_picture(server, sheet):
    vd.options.molterm_mode = "kitty"
    col = sheet.addcolMolecule()
    assert col.display(col.getCell(sheet.rows[0]), 20).lines == plugin._note("drawing…")
    picture = wait_for(("png", "CCO", 18, col.height, vd.options.molterm_theme))
    assert picture[1:] == (8, 4, 75)  # 75 pixels tall at 20 a row
    assert col.display(col.getCell(sheet.rows[0]), 20).lines == [[["", " "]]]


def test_from_another_column_the_smiles_column_is_found_by_its_name(server, sheet):
    sheet.cursorVisibleColIndex = 0  # on the name
    assert sheet.addcolMolecule().source.name == "smiles"


def test_with_molterm_auto_a_loaded_sheet_shows_its_structures(server, sheet):
    sheet.source = visidata.Path("drugs.csv")  # loaded from a file, as the option asks
    vd.options.molterm_auto = True
    try:
        sheet.afterLoad()
        sheet.afterLoad()  # once only
        assert [c.name for c in sheet.columns] == ["name", "smiles", "smiles_structure"]
        assert vd.options._get("disp_multiline_focus", sheet).value is False
    finally:
        vd.options.molterm_auto = False
    other = Sheet("plain", rows=[["x"]], columns=[ColumnItem("name", 0)])
    other.afterLoad()
    assert [c.name for c in other.columns] == ["name"]


def test_a_second_structure_column_reads_the_first_ones_smiles(server, sheet):
    first = sheet.addcolMolecule()
    sheet.cursorVisibleColIndex = sheet.visibleCols.index(first)
    assert sheet.addcolMolecule().source is first.source


# pictures over the cells


class Window:
    def getbegyx(self):
        return (1, 0)

    def getmaxyx(self):
        return (34, 116)


def laid_out(sheet, col, rows):
    """As VisiData leaves a sheet after drawing it: where each column and row went."""
    sheet._scr = Window()
    sheet._visibleColLayout = {sheet.visibleCols.index(col): (40, 20)}
    sheet._rowLayout = rows
    vd.sheets.insert(0, sheet)


def test_pictures_go_in_the_middle_of_their_cells_and_move_only_when_the_layout_does(server, sheet, monkeypatch):
    vd.options.molterm_mode = "kitty"
    col = sheet.addcolMolecule()
    written = []
    monkeypatch.setattr(plugin.pictures, "write", written.append)
    try:
        laid_out(sheet, col, {0: (2, col.height)})
        plugin.pictures.update(vd)  # asks for the picture; nothing to place yet
        assert written == []
        wait_for(("png", "CCO", 18, col.height, vd.options.molterm_theme))
        plugin.pictures.update(vd)
        image = plugin.pictures.image_id(("png", "CCO", 18, col.height, vd.options.molterm_theme))
        assert written[-1].count("a=t,") == 1 and f"i={image}" in written[-1]
        # 8 of 18 columns and 4 of 7 rows, so 5 in and 1 down: row 1 + 2 + 1 and column 40 + 1 + 5, counted from 1
        assert f"\x1b[5;47H\x1b_Ga=p,i={image},p=1,c=8,r=4,C=1,z=-1,q=2" in written[-1]
        count = len(written)
        plugin.pictures.update(vd)
        assert len(written) == count  # nothing moved, nothing sent
        laid_out(sheet, col, {0: (20, 3)})  # cut off by the bottom: its top two rows show
        plugin.pictures.update(vd)
        assert f"a=d,d=i,i={image},p=1" in written[-1] and "a=t," not in written[-1]
        assert f"c=8,r=2,h={round(75 * 2 / 4)},C=1" in written[-1]
        vd.options.molterm_mode = "text"
        plugin.pictures.update(vd)
        assert written[-1] == f"\x1b7\x1b_Ga=d,d=i,i={image},p=1,q=2\x1b\\\x1b8"
    finally:
        vd.sheets.remove(sheet)


# properties and a molecule's own sheet


def test_properties_become_typed_columns(server, sheet):
    sheet.addcolMolecule()
    vd.sync(sheet.addcolMolprops())
    names = [c.name for c in sheet.visibleCols]
    assert names[3:] == ["formula", "MW", "cLogP", "TPSA", "HBD", "HBA", "RotB", "QED", "Ro5", "PAINS", "Brenk"]
    mw, hbd = sheet.colsByName["MW"], sheet.colsByName["HBD"]
    assert mw.type is float and mw.getTypedValue(sheet.rows[0]) == pytest.approx(46.07) and hbd.getTypedValue(sheet.rows[0]) == 1
    assert sheet.colsByName["formula"].getValue(sheet.rows[0]) == "C2H6O"
    assert sheet.colsByName["MW"].getValue(sheet.rows[1]) is None  # refused by the server


def test_a_molecule_opens_named_after_its_row_from_any_column(server, sheet):
    vd.options.molterm_mode = "text"
    sheet.addcolMolecule()
    sheet.cursorVisibleColIndex = 0  # the name column, not the SMILES
    vs = sheet.openMolecule()
    vd.sync()
    assert vs.name == "ethanol" and vs.source == "CCO"
    lines = [str(row) for row in vs.rows]
    assert lines[:2] == ["⠀O⠀", "H⠒⠒"] and vs.rows[0].chunks[1] == ["red", "O"]
    assert "SMILES   CCO" in lines and any(line.startswith("MW 46.1") for line in lines)
    vd.sheets.remove(vs)


def test_the_mode_toggles(server):
    vd.options.molterm_mode = "kitty"
    vd.toggleMoltermMode()
    assert plugin.mode() == "text"
    vd.toggleMoltermMode()
    assert plugin.mode() == "kitty"


def test_an_older_server_says_it_needs_a_newer_one(monkeypatch):
    def not_found(request, timeout):
        raise plugin.urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(plugin.urllib.request, "urlopen", not_found)
    with pytest.raises(plugin.MoltermError, match="has no /api/v1/render.png: it needs a newer molterm server"):
        plugin._request("/api/v1/render.png", {"input": "CCO"})


def test_an_unreachable_server_says_where_it_looked():
    before = vd.options.molterm_url
    vd.options.molterm_url = "http://127.0.0.1:9"
    try:
        with pytest.raises(plugin.MoltermError, match="can't reach http://127.0.0.1:9"):
            plugin._request("/api/v1/render", body={"input": "CCO"})
    finally:
        vd.options.molterm_url = before


# a chart of the molecules


class Screen:
    """Enough of a curses window for the card: its size, and what was written where."""

    def __init__(self, rows=40, cols=120):
        self.size, self.cells = (rows, cols), {}

    def getmaxyx(self):
        return self.size

    def addstr(self, y, x, text, attr=0):
        for offset, char in enumerate(text):
            self.cells[(y, x + offset)] = char

    def line(self, y):
        return "".join(self.cells.get((y, x), " ") for x in range(self.size[1])).rstrip()


@pytest.fixture
def library(server):
    rows = [[f"MT-{n}", series, smiles] for n, (series, smiles) in
            enumerate([("aryl", "c1ccccc1O"), ("alkyl", "CCO"), ("aryl", "c1ccccc1N"), ("alkyl", "CCN")])]
    vs = Sheet("library", rows=rows, columns=[ColumnItem("id", 0), ColumnItem("series", 1), ColumnItem("smiles", 2)])
    vs.cursorVisibleColIndex = 2
    vs.addcolMolecule()
    return vs


def test_the_chart_adds_properties_and_colours_by_category(library):
    vd.options.molterm_mode = "text"
    library.plotMolecules()
    graph = vd.activeSheet
    vd.sync()
    try:
        assert isinstance(graph, visidata.GraphSheet) and graph.source is library
        assert [c.name for c in graph.xcols] == ["series", "MW"] and [c.name for c in graph.ycols] == ["cLogP"]
        assert "MW" in [c.name for c in library.visibleCols]
        assert len([p for p in graph.polylines if p[2] is not None]) == 4
    finally:
        vd.sheets.remove(graph)


def test_the_category_is_shared_by_rows_and_never_a_property(library):
    vd.options.molterm_mode = "text"
    plugin._add_properties(library, library.colsByName["smiles"])
    assert plugin._category(library).name == "series"
    library.colsByName["series"].hide()
    vd.clearCaches()
    assert plugin._category(library) is None  # not formula, which every row has its own of


def test_stepping_puts_the_cursor_on_each_dot_and_the_card_shows_it(library):
    vd.options.molterm_mode = "text"
    library.plotMolecules()
    graph = vd.activeSheet
    vd.sync()
    try:
        graph.render(40, 120)
        seen = []
        for _ in range(4):
            graph.moltermStep(1)
            rows = plugin._under_cursor(graph)
            assert rows and rows[0] is graph._molterm_row
            seen.append(rows[0][0])
        assert sorted(seen) == ["MT-0", "MT-1", "MT-2", "MT-3"]
        graph.moltermStep(1)  # round again
        assert plugin._under_cursor(graph)[0][0] == seen[0]
        screen = Screen()
        plugin._card(graph, screen)
        wait_for(("text", graph._molterm_row[2], plugin.CARD_COLS - 2, plugin.CARD_ROWS))
        plugin._card(graph, screen)
        top = graph._molterm_card[0] - 2
        assert f" {seen[0]} " in screen.line(top) and "MW 46.1" in screen.line(top + 1) and "cLogP" in screen.line(top + 1)
        assert "O" in screen.line(top + 2)  # the stand-in drawing
    finally:
        vd.sheets.remove(graph)


def test_a_redraw_sends_the_pictures_again(server, sheet, monkeypatch):
    vd.options.molterm_mode = "kitty"
    col = sheet.addcolMolecule()
    written = []
    monkeypatch.setattr(plugin.pictures, "write", written.append)
    try:
        laid_out(sheet, col, {0: (2, col.height)})
        plugin.pictures.update(vd)
        wait_for(("png", "CCO", 18, col.height, vd.options.molterm_theme))
        plugin.pictures.update(vd)
        assert written[-1].count("a=t,") == 1
        assert hasattr(visidata.VisiData.redraw, "__wrapped__")  # the hook on the redraw a resize and Ctrl+L run
        old = plugin.pictures.image_id(("png", "CCO", 18, col.height, vd.options.molterm_theme))
        plugin.pictures.forget()  # what it does
        count = len(written)
        plugin.pictures.update(vd)
        assert len(written) == count  # nothing while the screen may still be clearing
        plugin.pictures.redrawn_at -= plugin.SETTLE
        plugin.pictures.update(vd)
        new = plugin.pictures.image_id(("png", "CCO", 18, col.height, vd.options.molterm_theme))
        # sent and placed again under a new id, though nothing moved, and only then the old one removed
        assert new != old and written[-1].count("a=t,") == 1
        assert written[-1].index(f"a=p,i={new}") < written[-1].index(f"a=d,d=I,i={old}")
    finally:
        vd.sheets.remove(sheet)


def test_the_chart_and_its_sheet_follow_each_others_cursor(library):
    vd.options.molterm_mode = "text"
    library.plotMolecules()
    graph = vd.activeSheet
    vd.sync()
    try:
        graph.render(40, 120)
        assert vd.options.disp_splitwin_pct == vd.options.molterm_chart_split  # below the sheet, which stays in view
        # in the chart: the sheet's cursor goes to the row of the dot under the chart's
        graph.moltermStep(1)
        graph.moltermStep(1)
        plugin._card(graph, Screen())
        assert library.cursorRow is graph._molterm_row
        # in the sheet: the chart's cursor goes to the dot of its cursor row
        vd.push(library)
        library.cursorRowIndex = 3
        plugin._card(graph, Screen())
        assert plugin._under_cursor(graph)[0] is library.rows[3]
    finally:
        vd.options.disp_splitwin_pct = 0
        for vs in (graph,):
            if vs in vd.sheets:
                vd.sheets.remove(vs)
