"""molterm for VisiData: molecules drawn in the cells of a spreadsheet.

A column of SMILES gains a structure column beside it, whose rows are tall enough to show each molecule. In Kitty and Ghostty the
structures are pictures; elsewhere they are coloured Braille text, which any terminal shows. A molecule's own sheet shows it large
with its properties, and properties can be added as columns, to sort and filter by. A molterm server draws everything, so this
needs only the standard library: set its address with `options.molterm_url` or $MOLTERM_URL.

Load it from ~/.visidatarc:  import molterm_visidata

Commands (also in the Column menu, under molterm):
    Alt+m  addcol-molecule      add a structure column for the current column's SMILES
    Alt+o  open-molecule        open the current cell's molecule on its own sheet, large, with its properties
    Alt+E  edit-molecule        draw the current row's molecule in molterm's editor, in a box over the sheet (Kitty graphics, or
                                tmux passing them on); Done puts the new SMILES in its cell, which U undoes
    Alt+y  addcol-molprops      add columns of properties: formula, MW, cLogP, TPSA, HBD, HBA, RotB, QED, Ro5, PAINS, Brenk
    Alt+t  toggle-molterm-mode  switch between pictures and Braille text
    Alt+D  addcol-molecule-3d   add a column of the molecules in 3D, drawn by Mol* on a molterm-3d server (Kitty graphics)
    Alt+V  view-molecule-3d     turn the current row's molecule in 3D with the mouse, in a box over the sheet (Kitty graphics)
"""
import atexit
import base64
import curses
import hashlib
import json
import os
import re
import select
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import visidata
import visidata.sheets as _sheets
from visidata import Box, Column, GraphSheet, Progress, Sheet, VisiData, asyncthread, colors, vd

__version__ = "0.1.0"
__author__ = "Shinichiro Wachi"

vd.option("molterm_url", os.environ.get("MOLTERM_URL", "http://molterm.localhost"), "molterm server that draws the molecules")
vd.option("molterm_mode", "auto", "how structures are drawn: auto, kitty (pictures, in Kitty and Ghostty) or text (coloured Braille)")
vd.option("molterm_rows", 7, "height of a structure column's rows")
vd.option("molterm_cols", 26, "width of a structure column")
vd.option("molterm_theme", "dark", "colour theme for pictures, as molterm's -t: dark, light, mono, nord, ...")
vd.option("molterm_timeout", 15, "seconds to wait for the molterm server")
vd.option("molterm_auto", False, "add a structure column as soon as a sheet loads with a column named like smiles")
vd.option("molterm_auto_props", False, "with molterm_auto, add the property columns too")
vd.option("molterm_chart_split", 55, "how much of the screen, in percent, a chart takes below its sheet; 0 for the whole screen")
vd.option("molterm3d_url", os.environ.get("MOLTERM3D_URL", "http://molterm-3d.localhost"), "molterm-3d server that draws molecules in 3D")
vd.option("molterm3d_style", "auto", "3D style: auto, ball-and-stick, spacefill, licorice, cartoon or surface")
vd.option("molterm3d_lighting", "illustrative", "3D lighting: illustrative, standard, occlusion or outline")

MODES = ("auto", "kitty", "text")
CELL_PIXELS = 10  # a picture's pixels per column; the terminal scales it to the cells
_COLOURS = {31: "red", 32: "green", 33: "yellow", 34: "blue", 35: "magenta", 36: "cyan"}
PROPERTIES = (("formula", "formula", str), ("mw", "MW", float), ("logp", "cLogP", float), ("tpsa", "TPSA", float),
              ("hbd", "HBD", int), ("hba", "HBA", int), ("rotb", "RotB", int), ("qed", "QED", float), ("ro5", "Ro5", int),
              ("pains", "PAINS", int), ("brenk", "Brenk", int))
_SMALLEST = (10, 5)  # the server's least cols and rows
BATCH = 250  # properties per request
SETTLE = 0.4  # seconds after a resize or Ctrl+L before the pictures are placed again
KEPT_PICTURES = 64  # pictures kept in the terminal; the least recently shown are freed beyond that
LARGE = 3  # how much larger than in a cell a molecule's own sheet shows it, at most, and drawn with as many more pixels


class MoltermError(Exception):
    """The server couldn't draw it, or couldn't be reached."""


class MoltermUnavailable(MoltermError):
    """The server isn't there, or isn't one that has this: for molterm-3d, which is optional, 3D is then simply left out."""


# The server


def server_url():
    """Where the drawings come from; the online demo pins it."""
    return vd.options.molterm_url


def server3d_url():
    """Where the 3D pictures and the 3D viewer come from; the online demo pins it too."""
    return vd.options.molterm3d_url


def _request(path, params=None, body=None):
    return _http(server_url(), path, params, body, "it needs a newer molterm server")


def _request3d(path, params=None, timeout=None):
    return _http(server3d_url(), path, params, None, "is it a molterm-3d server?", timeout)


def _http(base, path, params, body, missing, timeout=None):
    url = base.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout or vd.options.molterm_timeout) as response:
            return response.read(), response.headers
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise MoltermUnavailable(f"{base} has no {path}: {missing}") from None
        text = error.read().decode("utf-8", "replace")
        try:
            text = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise MoltermError(text.strip() or f"the server answered {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        raise MoltermUnavailable(f"can't reach {base}: {getattr(error, 'reason', error)}") from None


def _drawing(smiles, cols, rows):
    """The server's Braille drawing, with the molecule's formula and properties."""
    body, _ = _request("/api/v1/render", body={"input": smiles, "cols": max(cols, _SMALLEST[0]), "rows": max(rows, _SMALLEST[1]),
                                                "mode": "braille", "properties": True})
    result = json.loads(body)
    _store[("props", smiles)] = {"formula": result["formula"], **result["properties"]}
    return result


def _picture(smiles, cols, rows, theme, cell=CELL_PIXELS):
    """A transparent PNG that fits within cols by rows cells, cropped to its drawing: (png, the columns and rows it takes, its height
    in pixels). `cell` is its pixels per column, more for a picture to be shown larger."""
    png, headers = _request("/api/v1/render.png", {"input": smiles, "cols": max(cols, _SMALLEST[0]), "rows": max(rows, _SMALLEST[1]),
                                                   "theme": theme, "cell_width": cell})
    height = int.from_bytes(png[20:24], "big") if png[:8] == b"\x89PNG\r\n\x1a\n" else rows * 2 * cell
    return png, int(headers.get("X-Molterm-Cols") or cols), min(-(-height // (2 * cell)), rows), height


def _picture3d(smiles, cols, rows, theme, style, lighting, cell=CELL_PIXELS * 2):
    """The molecule in 3D, from molterm-3d: a transparent PNG of exactly cols by rows cells, with twice the pixels of a 2D picture,
    as a 3D one has shading to show. Same shape of answer as _picture."""
    if time.monotonic() < _down3d[0]:  # not there a moment ago: don't ask again for every row
        raise MoltermUnavailable("molterm-3d isn't reachable")
    try:
        return _render3d(smiles, cols, rows, theme, style, lighting, cell)
    except MoltermUnavailable:
        _down3d[0] = time.monotonic() + DOWN_FOR
        raise


_down3d = [0.0]  # until when molterm-3d is taken to be away
DOWN_FOR = 30  # seconds before asking a molterm-3d that wasn't there again


def available3d():
    """Whether molterm-3d answers, asked quickly: the viewer checks before taking over the screen."""
    try:
        _request3d("/health", timeout=3)
        return True
    except MoltermError:
        _down3d[0] = time.monotonic() + DOWN_FOR
        return False


def _render3d(smiles, cols, rows, theme, style, lighting, cell):
    width, height = cols * cell, rows * 2 * cell
    png, _ = _request3d("/api/v1/render.png", {"input": smiles, "width": width, "height": height, "style": style, "lighting": lighting,
                                               "hydrogens": 0, "background": "ffffff" if theme == "light" else "161615"})
    return png, cols, rows, height


def _properties_batch(smiles):
    """Properties for many SMILES in one request, into the cache: a sheet of a thousand takes a few seconds rather than minutes."""
    body, _ = _request("/api/v1/properties", body={"inputs": smiles})
    for value, result in zip(smiles, json.loads(body)["results"]):
        _store[("props", value)] = {"formula": result["formula"], **result["properties"]} if "properties" in result else \
            MoltermError(result.get("error", {}).get("message", "not read"))


def _properties(smiles):
    _drawing(smiles, *_SMALLEST)
    return _store[("props", smiles)]


_FETCHERS = {"text": _drawing, "png": _picture, "png3d": _picture3d, "props": _properties}
_store, _pending, _lock = {}, set(), threading.Lock()
_workers = threading.BoundedSemaphore(4)


def _fetch(key):
    try:
        with _workers:
            value = _FETCHERS[key[0]](*key[1:])
    except MoltermError as error:
        value = error
    except Exception as error:  # a broken answer shows in the cell rather than ending the session
        value = MoltermError(str(error))
    with _lock:
        _store[key] = value
        _pending.discard(key)


def cached(key):
    """The answer for `key` if it has come, else None, asking for it in the background. VisiData redraws while it waits."""
    with _lock:
        if key in _store:
            return _store[key]
        if key in _pending:
            return None
        _pending.add(key)
    threading.Thread(target=_fetch, args=(key,), daemon=True).start()
    return None


def fetched(key):
    """The answer for `key`, waiting for it: for VisiData's own threads."""
    if key not in _store:
        _fetch(key)
    return _store[key]


# Drawing in cells


def mode():
    """kitty or text, as the option and the terminal say."""
    chosen = vd.options.molterm_mode
    if chosen in ("kitty", "text"):
        return chosen
    if os.environ.get("TMUX") or os.environ.get("STY"):
        return "text"  # a multiplexer doesn't pass pictures on as they are placed here
    if os.environ.get("TERM") in ("xterm-kitty", "xterm-ghostty", "kitty", "ghostty") or os.environ.get("KITTY_WINDOW_ID") or \
            os.environ.get("TERM_PROGRAM") == "ghostty":
        return "kitty"
    return "text"


class Drawing(list):
    """A cell's display: its chunks as one line, which is the SMILES, and `lines`, each a list of chunks, for multiline rows."""

    def __init__(self, chunks, lines):
        super().__init__(chunks)
        self.lines = lines


def text_lines(result):
    """The server's Braille as lines of (colour, text) chunks, the element labels in their colours."""
    lines = []
    for text, codes in zip(result["render"]["text"].split("\n"), result["render"]["colors"]):
        chunks = []
        for char, code in zip(text, codes):
            colour = _COLOURS.get(code, "")
            if chunks and chunks[-1][0] == colour:
                chunks[-1][1] += char
            else:
                chunks.append([colour, char])
        lines.append(chunks)
    return lines


def _note(text, colour="color_note_type"):
    return [[[colour, text]]]


_split = _sheets._splitcell


def _splitcell(sheet, s, width=0, maxheight=1):
    """VisiData wraps each chunk of a cell onto lines of its own; a drawing comes already in lines, with colours inside them."""
    if isinstance(s, Drawing):
        return (s.lines or [list(s)])[:max(maxheight, 1)]
    return _split(sheet, s, width=width, maxheight=maxheight)


_sheets._splitcell = _splitcell


class StructureColumn(Column):
    """The molecule of each row's SMILES in `source`, drawn across the row's lines."""

    def __init__(self, name, source, **kwargs):
        super().__init__(name, width=vd.options.molterm_cols, **kwargs)
        self.source = source
        self.height = vd.options.molterm_rows
        self.displayer = "molterm"

    def calcValue(self, row):
        return str(self.source.getValue(row) or "").strip()

    def picture_key(self, smiles, cols):
        """What to fetch for a picture of `smiles` in a cell `cols` wide."""
        return ("png", smiles, cols, self.height, vd.options.molterm_theme)

    def displayer_molterm(self, dw, width=None):
        smiles = str(dw.value or "").strip()
        if not smiles:
            return Drawing([], [])
        cols = (width or self.width or vd.options.molterm_cols) - 2
        if mode() == "kitty":
            picture = cached(self.picture_key(smiles, cols))
            if isinstance(picture, MoltermError):
                return Drawing([["", smiles]], _note(str(picture), "color_error"))
            # blank under the picture, which is placed below the text
            return Drawing([["", smiles]], [[["", " "]]] if picture else _note("drawing…"))
        result = cached(("text", smiles, cols, self.height))
        if result is None:
            return Drawing([["", smiles]], _note("drawing…"))
        if isinstance(result, MoltermError):
            return Drawing([["", smiles]], _note(str(result), "color_error"))
        return Drawing([["", smiles]], text_lines(result))


class Structure3DColumn(StructureColumn):
    """The molecule of each row's SMILES in 3D, put in 3D by molterm and drawn by Mol* on a molterm-3d server. Pictures only: 3D has
    no Braille."""

    def picture_key(self, smiles, cols):
        return ("png3d", smiles, cols, self.height, vd.options.molterm_theme, vd.options.molterm3d_style, vd.options.molterm3d_lighting)

    def displayer_molterm(self, dw, width=None):
        smiles = str(dw.value or "").strip()
        if mode() != "kitty" and smiles:
            return Drawing([["", smiles]], _note("3D needs Kitty graphics"))
        cols = (width or self.width or vd.options.molterm_cols) - 2
        if smiles and isinstance(_store.get(self.picture_key(smiles, cols)), MoltermUnavailable):
            # molterm-3d is optional: without it the column stays empty but for a quiet word, and everything else works as before
            return Drawing([["", smiles]], _note("3D unavailable"))
        return super().displayer_molterm(dw, width)


def _source_column(sheet, prefer_structure=False):
    """The column of SMILES to use: the cursor's, or the one a structure column draws. With `prefer_structure`, a sheet's structure
    column wins over a cursor on some other column, as for properties and opening a molecule from anywhere in its row."""
    col = sheet.cursorCol
    if isinstance(col, StructureColumn):
        return col.source
    if prefer_structure:
        drawn = [c for c in sheet.visibleCols if isinstance(c, StructureColumn)]
        if drawn:
            return drawn[0].source
    # a cursor on some other column, as on a row's name: the sheet's column of SMILES, if one is named so
    if not re.search(r"smiles|structure|mol", col.name or "", re.I):
        named = [c for c in sheet.visibleCols if re.search(r"smiles", c.name or "", re.I) and not isinstance(c, StructureColumn)]
        if named:
            return named[0]
    return col


def _add_structure(sheet, source, beside=False):
    col = StructureColumn(f"{source.name}_structure", source)
    if beside:  # right after the SMILES, wherever the cursor is
        sheet.addColumn(col, index=sheet.columns.index(source) + 1)
    else:
        sheet.addColumnAtCursor(col)
    # every row at full height, not only the cursor's, so that the structures can be read down the sheet
    vd.options.set("disp_multiline_focus", False, sheet)  # through vd.options, whose cache the drawing reads
    return col


@Sheet.api
def addcolMolecule(sheet):
    return _add_structure(sheet, _source_column(sheet))


@Sheet.api
def addcolMolecule3d(sheet):
    source = _source_column(sheet)
    col = Structure3DColumn(f"{source.name}_3d", source)
    sheet.addColumnAtCursor(col)
    vd.options.set("disp_multiline_focus", False, sheet)
    vd.status("in 3D, drawn by Mol* on the molterm-3d server: Alt+Shift+V turns the current row's molecule with the mouse")
    return col


@Sheet.after
def afterLoad(sheet):
    """With options.molterm_auto, a sheet loaded from a file with a column named like SMILES shows its structures straight away, and
    with molterm_auto_props its properties too."""
    if not sheet.options.molterm_auto or not isinstance(getattr(sheet, "source", None), visidata.Path):
        return
    if any(isinstance(c, StructureColumn) for c in sheet.columns):
        return
    named = [c for c in sheet.columns if re.search(r"smiles", c.name or "", re.I)]
    if named:
        structure = _add_structure(sheet, named[0], beside=True)
        if sheet.options.molterm_auto_props:
            _add_properties(sheet, named[0], index=sheet.columns.index(structure) + 1)  # this runs in the loading thread already


@Sheet.api
@asyncthread
def addcolMolprops(sheet):
    _add_properties(sheet, _source_column(sheet, prefer_structure=True))


def _add_properties(sheet, source, index=None):
    """Fetch the properties of every row's SMILES, then add a column for each: at `index`, or after the cursor."""
    smiles = sorted({str(source.getValue(row) or "").strip() for row in sheet.rows} - {""})
    missing = [value for value in smiles if ("props", value) not in _store]
    with Progress(gerund="computing properties", total=len(missing)) as progress:
        for start in range(0, len(missing), BATCH):
            batch = missing[start:start + BATCH]
            try:
                _properties_batch(batch)
            except MoltermError:  # an older server, without the batch: one at a time
                for value in batch:
                    fetched(("props", value))
            progress.addProgress(len(batch))

    def getter(key):
        def get(col, row):
            props = _store.get(("props", str(source.getValue(row) or "").strip()))
            return props.get(key) if isinstance(props, dict) else None
        return get

    cols = [Column(name, type=kind, getter=getter(key), width=None) for key, name, kind in PROPERTIES]
    for col in cols:
        if col.type is float:
            col.fmtstr = "%.2f" if col.name == "QED" else "%.1f"
    if index is None:
        sheet.addColumnAtCursor(*cols)
    else:
        for offset, col in enumerate(cols):
            sheet.addColumn(col, index=index + offset)
    failed = sum(isinstance(_store.get(("props", s)), MoltermError) for s in smiles)
    if failed:
        vd.warning(f"{failed} of {len(smiles)} SMILES could not be read")
    return cols


# A chart of the molecules, with the one under the cursor shown beside it

CARD_COLS, CARD_ROWS = 30, 7  # the card's width, and the rows of its drawing


def _visible(sheet, name):
    return next((c for c in sheet.visibleCols if c.name == name), None)


def _category(sheet):
    """A column to colour the dots by: one with a few values that rows share, such as a series or class, rather than a structure, a
    name or a property. Columns named like that come first."""
    properties = {name for _, name, _ in PROPERTIES}
    rows = sheet.rows[:2000]
    candidates = [col for col in sheet.visibleCols if not isinstance(col, StructureColumn) and not vd.isNumeric(col)
                  and col.name not in properties and not re.search(r"smiles|name|^id$", col.name or "", re.I)]
    candidates.sort(key=lambda col: not re.search(r"class|series|categor|type|group|family|target", col.name or "", re.I))
    for col in candidates:
        values = {col.getValue(row) for row in rows}
        if 2 <= len(values) <= min(12, len(rows) * 0.8):
            return col
    return None


@Sheet.api
def plotMolecules(sheet):
    """cLogP against MW, a dot for each row, coloured by a category if the sheet has one; properties are added first if need be."""
    if not _visible(sheet, "MW"):
        source = _source_column(sheet, prefer_structure=True)
        drawn = [c for c in sheet.columns if isinstance(c, StructureColumn)]
        _add_properties(sheet, source, index=sheet.columns.index(drawn[0] if drawn else source) + 1)
    category = _category(sheet)
    xcols = [category, _visible(sheet, "MW")] if category else [_visible(sheet, "MW")]
    graph = GraphSheet(sheet.name, "chart", source=sheet, sourceRows=sheet.rows, xcols=xcols, ycols=[_visible(sheet, "cLogP")])
    split = vd.options.molterm_chart_split
    if split:
        # below the sheet, which stays in view: the two cursors follow each other, so a dot's row is the table's, and a row's dot the chart's
        vd.options.disp_splitwin_pct = split
        vd._molterm_split = True
        vd.push(graph, pane=-1)
        vd.activePane = graph.pane
    else:
        vd.push(graph)
    vd.status("Alt+n and Alt+b step from dot to dot, and the table follows; Tab goes to the table, whose cursor the chart follows")


def _smiles_column(sheet):
    drawn = [c for c in getattr(sheet, "columns", []) if isinstance(c, StructureColumn)]
    if drawn:
        return drawn[0].source
    return next((c for c in getattr(sheet, "columns", []) if re.search(r"smiles", c.name or "", re.I)), None)


def _under_cursor(graph):
    """The rows whose dots are under the chart's cursor, the one stepped to first."""
    try:
        rows = graph.rowsWithin(graph.plotterCursorBox)
    except Exception:
        return []
    chosen = getattr(graph, "_molterm_row", None)
    if chosen is not None and any(row is chosen for row in rows):
        rows = [chosen] + [row for row in rows if row is not chosen]
    return rows


def _card(graph, scr):
    """A card in the chart's lower right: the molecule under the cursor, its name and values, and how many more share the place."""
    graph._molterm_card = None
    column = _smiles_column(graph.source)
    if column is not None:
        _follow(graph, getattr(graph, "_molterm_row", None))
    rows = _under_cursor(graph) if column is not None else []
    if rows and vd.activeSheet is graph:
        _follow(graph, rows[0])
    if not rows:
        return
    row = rows[0]
    smiles = str(column.getValue(row) or "").strip()
    height, width = scr.getmaxyx()
    top, left = height - 4 - (CARD_ROWS + 3), width - CARD_COLS - 2
    if top < 1 or left < 0 or not smiles:
        return
    label = next((str(c.getValue(row)) for c in graph.source.visibleCols if c is not column and not isinstance(c, StructureColumn)), smiles)
    values = "  ".join(f"{c.name} {c.getDisplayValue(row)}" for c in [*vd.numericCols(graph.xcols), *graph.ycols])
    more = f" +{len(rows) - 1} here " if len(rows) > 1 else ""
    plain, edge = colors.color_default.attr, colors.get_color("color_column_sep").attr
    inner = CARD_COLS - 2

    def put(y, x, text, attr):
        try:
            scr.addstr(top + y, left + x, text, attr)
        except Exception:  # the window's last cell
            pass

    title = f" {label[:inner - 4]} "
    put(0, 0, "╭" + title + "─" * (inner - len(title)) + "╮", edge)
    put(1, 0, "│" + f" {values}"[:inner].ljust(inner) + "│", edge)
    put(1, 1, f" {values}"[:inner], plain)
    drawing = cached(("text", smiles, inner, CARD_ROWS)) if mode() == "text" else None
    lines = text_lines(drawing) if isinstance(drawing, dict) else []
    for y in range(CARD_ROWS):
        put(2 + y, 0, "│" + " " * inner + "│", edge)
        x = 1
        for colour, text in (lines[y] if y < len(lines) else []):
            put(2 + y, x, text[:max(inner - (x - 1), 0)], colors.get_color(colour).attr if colour else plain)
            x += len(text)
    put(CARD_ROWS + 2, 0, "╰" + more + "─" * (inner - len(more)) + "╯", edge)
    graph._molterm_card = (top + 2, left + 1, smiles)


@GraphSheet.after
def draw(graph, scr):
    _card(graph, scr)


@GraphSheet.api
def moltermStep(graph, step):
    """Put the chart's cursor on the next dot, or the one before, in the order of the sheet's rows."""
    points = [(vertexes[0][0], vertexes[0][1], row) for vertexes, _, row in graph.polylines if row is not None and vertexes]
    if not points:
        vd.fail("no dots in this chart")
    # in the sheet's order as it is now, so that after a sort the steps go from top to bottom
    order = {id(row): index for index, row in enumerate(graph.source.rows)}
    points.sort(key=lambda point: order.get(id(point[2]), len(order)))
    graph._molterm_index = (getattr(graph, "_molterm_index", -1) + step) % len(points)
    x, y, row = points[graph._molterm_index]
    w, h = graph.canvasCharWidth, graph.canvasCharHeight
    graph.cursorBox = Box(x - w / 2, y - h / 2, w, h)
    graph._molterm_row = row


def _dot_of(graph, row):
    """The chart's point for a row, or None."""
    return next(((vertexes[0][0], vertexes[0][1]) for vertexes, _, plotted in graph.polylines if plotted is row and vertexes), None)


def _follow(graph, row):
    """Keep the chart and its sheet on the same molecule: the sheet's cursor goes to the row under the chart's, while the chart is
    used, and the chart's to the dot of the sheet's cursor row, while the sheet is."""
    source = graph.source
    if vd.activeSheet is source:
        wanted = source.cursorRow
        if wanted is not None and wanted is not getattr(graph, "_molterm_synced", None):
            point = _dot_of(graph, wanted)
            if point:
                w, h = graph.canvasCharWidth, graph.canvasCharHeight
                graph.cursorBox = Box(point[0] - w / 2, point[1] - h / 2, w, h)
                graph._molterm_row = wanted
            graph._molterm_synced = wanted
    elif vd.activeSheet is graph and row is not None and row is not getattr(graph, "_molterm_synced", None):
        index = next((i for i, r in enumerate(source.rows) if r is row), None)
        if index is not None:
            source.cursorRowIndex = index
        graph._molterm_synced = row


# A molecule on its own sheet


class MoleculeSheet(Sheet):
    """One molecule, large, above its properties."""

    rowtype = "lines"  # rowdef: Line
    columns = [Column("molecule", getter=lambda col, row: row, displayer="molterm_line")]

    def iterload(self):
        smiles = self.source
        # a new sheet has no window of its own yet, so it takes the size of the one it was opened from
        width, height = getattr(self, "size", None) or (self.windowWidth, self.windowHeight)
        cols = max(min(width - 4, 90), _SMALLEST[0])
        # room for the properties below it, which take six lines, and the bars
        self.picture_rows = rows = max(min(height - 9, 24), _SMALLEST[1])
        self.picture_cols = cols
        result = fetched(("text", smiles, cols, rows))
        if isinstance(result, MoltermError):
            yield Line(str(result))
            return
        if mode() == "kitty":
            # molterm draws every molecule at one bond length, so a small one is small: here it's enlarged to fill the room
            self.picture_key = ("png", smiles, cols, rows, vd.options.molterm_theme, CELL_PIXELS * LARGE)
            picture = fetched(self.picture_key)
            if isinstance(picture, tuple):
                _, used, taken, height = picture
                scale = min(cols / used, rows / taken, LARGE)
                self.picture_size = (max(round(used * scale), 1), max(round(taken * scale), 1))
            for _ in range(self.picture_size[1] if isinstance(picture, tuple) else rows):  # blank lines for the picture to cover
                yield Line("")
        else:
            for chunks in text_lines(result):
                yield Line("".join(text for _, text in chunks), chunks)
        yield Line("")
        props = _store.get(("props", smiles), {})
        yield Line(f"SMILES   {smiles}")
        yield Line(f"formula  {props.get('formula', '')}")
        yield Line(f"MW {props.get('mw', 0):.1f}   cLogP {props.get('logp', 0):.2f}   TPSA {props.get('tpsa', 0):.1f}   "
                   f"QED {props.get('qed', 0):.2f}")
        yield Line(f"HBD {props.get('hbd', 0)}   HBA {props.get('hba', 0)}   RotB {props.get('rotb', 0)}   "
                   f"Ro5 violations {props.get('ro5', 0)}   PAINS {props.get('pains', 0)}   Brenk {props.get('brenk', 0)}")


class Line(str):
    """A line of a molecule's sheet, with its colours if it's part of a drawing."""

    def __new__(cls, text, chunks=None):
        line = super().__new__(cls, text)
        line.chunks = chunks
        return line


def _displayer_molterm_line(col, dw, width=None):
    chunks = getattr(dw.value, "chunks", None)
    return chunks or [("", str(dw.value))]


Column.displayer_molterm_line = _displayer_molterm_line


@Sheet.api
def openMolecule(sheet):
    smiles = str(_source_column(sheet, prefer_structure=True).getValue(sheet.cursorRow) or "").strip()
    if not smiles:
        vd.fail("no SMILES in this cell")
    # named after the row, by its first column that isn't the structure, as `name` usually is
    label = next((str(c.getValue(sheet.cursorRow)) for c in sheet.visibleCols if not isinstance(c, StructureColumn)), "")
    vs = MoleculeSheet(label[:40] or smiles[:40], source=smiles, size=(sheet.windowWidth, sheet.windowHeight))
    vd.push(vs)
    return vs


@VisiData.api
def toggleMoltermMode(vd):
    vd.options.molterm_mode = "text" if mode() == "kitty" else "kitty"
    vd.status(f"molterm draws {'pictures' if mode() == 'kitty' else 'Braille text'}")


# Pictures: placed over the cells after VisiData has drawn them, and moved only when the layout changes


class Pictures:
    def __init__(self):
        self.placed, self.sent = [], {}  # sent: image id -> None, least recently shown first
        self.generation, self.stale, self.redrawn_at = 0, [], None  # see forget
        self._out = None

    def write(self, text):
        if self._out is None:
            try:
                self._out = open("/dev/tty", "wb", buffering=0)
            except OSError:
                self._out = sys.stdout.buffer
        self._out.write(text.encode("ascii"))
        if hasattr(self._out, "flush"):
            self._out.flush()

    def image_id(self, key):
        return int.from_bytes(hashlib.sha256(repr((key, self.generation)).encode()).digest()[:4], "big") % 4_000_000_000 + 1

    def wanted(self, vd):
        """[(key, (png, cols, rows), screen row, screen col, rows shown)] for the sheets on screen: the active one, and with the screen
        split, the one in the other pane too. A picture sits in the middle of its cell; one cut off by the bottom of its window shows
        its top."""
        if mode() != "kitty":
            return []
        shown = [vd.activeSheet]
        if getattr(vd, "win2", None) is not None:
            shown = [stack[0] for stack in (vd.sheetstack(1), vd.sheetstack(2)) if stack]
        return [place for sheet in dict.fromkeys(shown) for place in self._wanted_for(vd, sheet)]

    def _wanted_for(self, vd, sheet):
        if sheet is None or getattr(sheet, "_scr", None) is None:
            return []
        top, left = sheet._scr.getbegyx()
        out = []
        if isinstance(sheet, GraphSheet):
            card = getattr(sheet, "_molterm_card", None)
            if card:
                row, col, smiles = card
                key = ("png", smiles, CARD_COLS - 2, CARD_ROWS, vd.options.molterm_theme)
                picture = cached(key)
                if isinstance(picture, tuple):
                    _, cols, rows, _ = picture
                    down = max((CARD_ROWS - rows) // 2, 0)
                    out.append((key, picture, top + row + down, left + col + max((CARD_COLS - 2 - cols) // 2, 0), rows))
            return out
        if not isinstance(sheet, Sheet):
            return []
        if isinstance(sheet, MoleculeSheet):
            layout = getattr(sheet, "_rowLayout", {})
            key, size = getattr(sheet, "picture_key", None), getattr(sheet, "picture_size", None)
            if 0 in layout and sheet.rows and key and size:
                y = layout[0][0]
                visible = min(size[1], sheet.windowHeight - 1 - y)
                picture = cached(key)
                x = sheet._visibleColLayout.get(0, (0, 0))[0]
                if isinstance(picture, tuple) and visible > 0:
                    out.append((key, (picture[0], *size, picture[3]), top + y, left + x + 1, visible))
            return out
        for vcolidx, (x, width) in sorted(getattr(sheet, "_visibleColLayout", {}).items()):
            col = sheet.availCols[vcolidx] if vcolidx < len(sheet.availCols) else None
            if not isinstance(col, StructureColumn) or x + width > sheet.windowWidth:
                continue
            for rowidx, (y, height) in sorted(getattr(sheet, "_rowLayout", {}).items()):
                if height < 2 or rowidx >= len(sheet.rows):
                    continue
                smiles = col.getValue(sheet.rows[rowidx])
                if not smiles:
                    continue
                key = col.picture_key(smiles, width - 2)
                picture = cached(key)
                if not isinstance(picture, tuple):
                    continue
                _, cols, rows, _ = picture
                down = max((col.height - rows) // 2, 0)
                shown = min(rows, height - down)
                if shown > 0:
                    out.append((key, picture, top + y + down, left + x + 1 + max((width - 2 - cols) // 2, 0), shown))
        return out

    def update(self, vd):
        # VisiData stops drawing a second after the last key, until the next: keep it drawing while pictures are still to come, after
        # a redraw (the viewer's or the editor's closing, Ctrl+L) or while drawings are fetched, or they'd wait for a key to show
        if self.redrawn_at is not None or _pending:
            vd.numTimeouts = 0
        if not self._settled():
            return
        wanted = self.wanted(vd)
        layout = [(key, row, col, shown) for key, _, row, col, shown in wanted]
        if layout == self.placed:
            return
        out = ["\x1b7"]
        for slot, (key, *_rest) in enumerate(self.placed):
            out.append(f"\x1b_Ga=d,d=i,i={self.image_id(key)},p={slot + 1},q=2\x1b\\")
        for slot, (key, (png, cols, rows, height), row, col, shown) in enumerate(wanted):
            image = self.image_id(key)
            if image not in self.sent:
                out.append(self.transmit(image, png))
            self.sent.pop(image, None)
            self.sent[image] = None
            # z below the text, so that VisiData's menus and dialogs show over the pictures; a row cut off by the bottom shows its top
            crop = f",h={round(height * shown / rows)}" if shown < rows else ""
            out.append(f"\x1b[{row + 1};{col + 1}H\x1b_Ga=p,i={image},p={slot + 1},c={cols},r={shown}{crop},C=1,z=-1,q=2\x1b\\")
        # pictures from before a redraw go only now that their replacements are placed (see forget)
        out.extend(f"\x1b_Ga=d,d=I,i={image},q=2\x1b\\" for image in self.stale)
        self.stale = []
        # a long scroll would otherwise fill the terminal's memory, and it would drop pictures this still thinks are there
        showing = {self.image_id(key) for key, *_ in layout}
        for image in [i for i in self.sent if i not in showing][:max(len(self.sent) - KEPT_PICTURES, 0)]:
            out.append(f"\x1b_Ga=d,d=I,i={image},q=2\x1b\\")
            del self.sent[image]
        out.append("\x1b8")
        self.write("".join(out))
        self.placed = layout

    @staticmethod
    def transmit(image, png):
        payload = base64.b64encode(png).decode("ascii")
        pieces = [payload[i:i + 4096] for i in range(0, len(payload), 4096)] or [""]
        out = []
        for index, piece in enumerate(pieces):
            keys = f"a=t,f=100,i={image},q=2," if index == 0 else ""
            out.append(f"\x1b_G{keys}m={int(index < len(pieces) - 1)};{piece}\x1b\\")
        return "".join(out)

    def forget(self):
        """Send every picture again once the screen has settled: a terminal may drop them when its window changes size, and clearing
        the screen removes them, which VisiData's redraw (on a resize, and Ctrl+L) does window by window, at each one's next refresh,
        some of them after this has placed the pictures. So nothing is placed until SETTLE seconds after the last redraw, and then
        everything is sent again under new ids, the old ones removed only after the new are placed."""
        self.redrawn_at = time.monotonic()

    def _settled(self):
        """False while a redraw's clearing may still come; once it's over, start again with new ids."""
        if self.redrawn_at is None:
            return True
        if time.monotonic() - self.redrawn_at < SETTLE:
            return False
        self.redrawn_at = None
        self.stale.extend(self.sent)
        self.generation += 1
        self.placed, self.sent = [], {}
        return True

    def hide(self):
        """Take the pictures off the screen, keeping them in the terminal: for a box drawn over them, whose own backgrounds they'd show
        through. The next update places them again."""
        if self.placed:
            self.write("".join(f"\x1b_Ga=d,d=i,i={self.image_id(key)},p={slot + 1},q=2\x1b\\" for slot, (key, *_rest) in enumerate(self.placed)))
        self.placed = []

    def clear(self):
        if self.sent:
            self.write("".join(f"\x1b_Ga=d,d=I,i={image},q=2\x1b\\" for image in self.sent))
        self.placed, self.sent = [], {}


pictures = Pictures()


@VisiData.after
def draw_all(vd):
    try:
        pictures.update(vd)
    except Exception as error:  # pictures are extra: a failure leaves the text as it is
        vd.debug(f"molterm: {error}")


@VisiData.after
def quit(vd, *sheets):
    # a chart's split closes with the last sheet in its pane, so that the table has the screen again
    if getattr(vd, "_molterm_split", False) and not vd.sheetstack(2):
        vd.options.disp_splitwin_pct = 0
        vd._molterm_split = False


@VisiData.after
def redraw(vd):
    # VisiData's redraw, which a resize and Ctrl+L both run
    try:
        pictures.forget()
    except Exception as error:
        vd.debug(f"molterm: {error}")


atexit.register(lambda: pictures.clear() if pictures.sent else None)


# Editing a molecule: molterm's editor in a box over the sheet. The server runs the editor; this passes it the terminal's raw input and
# prints what comes back, as molterm's own client does with -b, while VisiData waits.

EDITOR_IDS = range(7001, 7022)  # the editor's pictures, which go when it closes; the server's ids, not ours
EDITOR_LEAST = (38, 12)  # the smallest box the editor fits in, counting its border
EDITOR_MOST = (110, 34)


def _edit_request(fields):
    """One step of the editor: (outcome, state, output), the outcome 0 to go on, 1 finished, 2 failed, 3 cancelled."""
    url = server_url().rstrip("/") + "/api/v1/edit"
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    try:
        with urllib.request.urlopen(request, timeout=vd.options.molterm_timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise MoltermError(f"{server_url()} has no editor: it needs a newer molterm server") from None
        raise MoltermError(error.read().decode("utf-8", "replace").strip() or f"the server answered {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        raise MoltermError(f"can't reach {server_url()}: {getattr(error, 'reason', error)}") from None
    first, _, output = text.partition("\n")
    outcome, _, state = first.partition(" ")
    return int(outcome), state, output


def _run(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def editor_graphics(what="the editor"):
    """How the editor's pictures reach the terminal: "direct", or "placeholders" through tmux (see molterm's server/placeholders.py).
    Raises MoltermError, saying why, where they can't."""
    if os.environ.get("TMUX"):
        outer = _run(["tmux", "display", "-p", "#{client_termname} #{client_termtype}"])
        if not re.search(r"kitty|ghostty", outer, re.I):
            raise MoltermError(f"{what} needs Kitty graphics, and tmux says the terminal outside it is '{outer.strip()}', not Kitty or Ghostty")
        if not re.search(r"on|all", _run(["tmux", "show", "-Apv", "allow-passthrough"])):
            raise MoltermError(f"{what} needs Kitty graphics, which tmux passes on only with: tmux set -g allow-passthrough on")
        return "placeholders"
    if os.environ.get("STY"):
        raise MoltermError(f"{what} needs Kitty graphics, which screen doesn't pass on")
    if mode() != "kitty":
        raise MoltermError(f"{what} needs Kitty graphics, as in Kitty or Ghostty (set options.molterm_mode to kitty if this terminal has them)")
    return "direct"


def editor_box(height, width):
    """Where the editor goes on a screen of this size: (row, col, rows, cols), counting its border, in the middle below the top bar."""
    cols, rows = min(width - 4, EDITOR_MOST[0]), min(height - 2, EDITOR_MOST[1])
    if cols < EDITOR_LEAST[0] or rows < EDITOR_LEAST[1]:
        raise MoltermError(f"the screen is too small for the editor, which needs {EDITOR_LEAST[0] + 4} columns and {EDITOR_LEAST[1] + 2} rows")
    return 1 + (height - 2 - rows) // 2, (width - cols) // 2, rows, cols


class Terminal:
    """The terminal, read and written around curses, which isn't reading while a command runs."""

    def __init__(self):
        self.fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)

    def size(self):
        size = os.get_terminal_size(self.fd)
        return size.lines, size.columns

    def write(self, data):
        while data:
            data = data[os.write(self.fd, data):]

    def read(self, timeout):
        ready, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, 4096) if ready else b""

    def close(self):
        os.close(self.fd)


def _ask_cells(terminal):
    """The cell size in pixels, and whether the mouse can report in pixels, from the terminal's answers: (width, height, pixels,
    typed), `typed` being whatever else came meanwhile, such as a key or a mouse press, kept to be handled as input."""
    terminal.write(b"\x1b[16t\x1b[?1016$p\x1b[c")  # the device attributes come last, and every terminal answers them
    reply, deadline = b"", time.monotonic() + 1
    while time.monotonic() < deadline and not re.search(rb"\x1b\[\?[0-9;]*c", reply):
        reply += terminal.read(deadline - time.monotonic())
    typed = re.sub(rb"\x1b\[6;\d+;\d+t|\x1b\[\?1016;\d\$y|\x1b\[\?[0-9;]*c", b"", reply)
    size = re.search(rb"\x1b\[6;(\d+);(\d+)t", reply)
    if not size:
        return None, None, False, typed
    height, width = int(size[1]), int(size[2])
    return width, height, bool(re.search(rb"\x1b\[\?1016;[12]\$y", reply)), typed


def edit_in_box(smiles, title, terminal, graphics="direct", step=None):
    """Run the editor on `smiles` in a box over the screen until it's done: the new SMILES, or None if it was cancelled."""
    step = step or _edit_request
    width, height, pixels, typed = _ask_cells(terminal)
    fields = {"smiles": smiles, "title": title[:200], "theme": vd.options.molterm_theme}
    if width and height:
        fields.update(cell_width=width, cell_height=height)
    if pixels:
        fields["pixels"] = 1
    if graphics == "placeholders":
        fields["placeholders"] = 1
    terminal.write(b"\x1b[?25l\x1b[?1003h\x1b[?1006h" + (b"\x1b[?1016h" if pixels else b""))
    events, drawn, outcome, output = typed, None, 0, ""
    try:
        while True:
            size = terminal.size()
            if events or size != drawn:
                box = editor_box(*size)
                outcome, state, output = step({**fields, "events": events.hex(), "rows": size[0], "cols": size[1],
                                               "box": ",".join(map(str, box))})
                fields.pop("smiles", None)
                fields["state"] = state
                drawn = size
                if outcome:
                    break
                if output:
                    terminal.write(output.encode("utf-8"))
            events = terminal.read(1.0)
    finally:
        out = []
        for image in EDITOR_IDS:
            command = f"\x1b_Ga=d,d=I,i={image if graphics == 'direct' else (image // 255) << 24 | (image % 255 + 1)},q=2\x1b\\"
            out.append(command if graphics == "direct" else "\x1bPtmux;" + command.replace("\x1b", "\x1b\x1b") + "\x1b\\")
        out.append("\x1b[?1016l\x1b[?1003l")
        terminal.write("".join(out).encode("ascii"))
    if outcome == 2:
        raise MoltermError(output.strip())
    if outcome == 3:
        return None
    return output.strip()


@Sheet.api
def editMolecule(sheet):
    """Draw the current row's molecule in molterm's editor, in a box over the sheet; Done puts the new SMILES in its cell."""
    column = _source_column(sheet, prefer_structure=True)
    row = sheet.cursorRow
    if row is None:
        vd.fail("no row to edit")
    old = str(column.getValue(row) or "").strip()
    try:
        graphics = editor_graphics()
    except MoltermError as error:
        vd.fail(str(error))
    label = next((str(c.getValue(row)) for c in sheet.visibleCols if c is not column and not isinstance(c, StructureColumn)), "")
    pictures.hide()
    terminal = Terminal()
    try:
        new = edit_in_box(old, label or column.name, terminal, graphics)
    except MoltermError as error:
        new = error
    finally:
        terminal.close()
        # the screen as VisiData had it: its mouse reporting, a hidden cursor, and everything drawn again over the box
        try:
            curses.mousemask(0)
            vd.enableMouse(bool(vd.options.mouse_interval))
            curses.curs_set(0)
        except curses.error:
            pass
        vd.redraw()
    if isinstance(new, MoltermError):
        vd.fail(f"molterm: {new}")
    if new is None:
        vd.status("edit cancelled")
    elif new == old:
        vd.status("the molecule is unchanged")
    else:
        cached(("props", new))  # for the property columns, which read it once it comes
        column.setValues([row], new)
    return new


# A molecule in 3D: molterm-3d's viewer in a box over the sheet, turned with the mouse. As with the editor, the server runs the viewer
# (Mol*, on its own CPUs) and this passes it the terminal's raw input and prints what comes back, as molterm-3d's own client does.

VIEWER_IDS = (3301, 3302)  # molterm-3d's picture ids, taken in turn
VIEWER_LEAST = (30, 10)  # the smallest box, counting its frame
VIEWER_MOST = (140, 44)


def viewer_box(height, width):
    """Where the viewer goes on a screen of this size: (row, col, rows, cols), counting its frame, in the middle below the top bar."""
    cols, rows = min(width - 4, VIEWER_MOST[0]), min(height - 2, VIEWER_MOST[1])
    if cols < VIEWER_LEAST[0] or rows < VIEWER_LEAST[1]:
        raise MoltermError(f"the screen is too small for the 3D viewer, which needs {VIEWER_LEAST[0] + 4} columns and {VIEWER_LEAST[1] + 2} rows")
    return 1 + (height - 2 - rows) // 2, (width - cols) // 2, rows, cols


def _frame(row, col, rows, cols, title):
    """The box's frame, titled, drawn around where the viewer draws."""
    inner = cols - 2
    title = f" {title[:inner - 6]} " if title else ""
    out = [f"\x1b[{row + 1};{col + 1}H\x1b[2m╭─{title}{'─' * (inner - 1 - len(title))}╮"]
    for y in range(1, rows - 1):
        out.append(f"\x1b[{row + y + 1};{col + 1}H│\x1b[{row + y + 1};{col + cols}H│")
    out.append(f"\x1b[{row + rows};{col + 1}H╰{'─' * inner}╯\x1b[22m")
    return "".join(out).encode("utf-8")


class _Spinner:
    """A spinner on the frame's bottom edge while the server draws, from 0.4 s on, so that a slow picture doesn't look like a hang; the
    edge is drawn again when the answer comes."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, terminal, row, col, width):
        self.terminal, self.row, self.col, self.width = terminal, row, col, width
        self.stop, self.shown = threading.Event(), False
        self.thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        if self.stop.wait(0.4):
            return
        frame = 0
        while not self.stop.is_set():
            self.shown = True
            text = f" {self.FRAMES[frame % len(self.FRAMES)]} drawing "
            self.terminal.write(f"\x1b7\x1b[{self.row + 1};{self.col + 1}H\x1b[2m{text}\x1b[22m\x1b8".encode("utf-8"))
            frame += 1
            self.stop.wait(0.1)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()
        if self.shown:
            self.terminal.write(f"\x1b7\x1b[{self.row + 1};{self.col + 1}H\x1b[2m{'─' * self.width}\x1b[22m\x1b8".encode("utf-8"))


def _view_request(fields):
    """One step of molterm-3d's viewer: (outcome, state, output), the outcome 0 to go on, 1 finished, 2 failed."""
    url = server3d_url().rstrip("/") + "/api/v1/view"
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    try:
        with urllib.request.urlopen(request, timeout=max(vd.options.molterm_timeout, 30)) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise MoltermError(f"{server3d_url()} has no 3D viewer: is it a molterm-3d server?") from None
        raise MoltermError(error.read().decode("utf-8", "replace").strip() or f"the server answered {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        raise MoltermError(f"can't reach {server3d_url()}: {getattr(error, 'reason', error)}") from None
    first, _, output = text.partition("\n")
    outcome, _, state = first.partition(" ")
    return int(outcome), state, output


def view_in_box(smiles, title, terminal, graphics="direct", step=None):
    """Turn `smiles` in 3D in a box over the screen until q: drag to turn, the wheel to zoom, s, c and l for styles, colours and
    lightings, as in molterm-3d."""
    step = step or _view_request
    width, height, pixels, typed = _ask_cells(terminal)
    fields = {"input": smiles, "title": title[:120], "theme": "light" if vd.options.molterm_theme == "light" else "dark",
              "style": vd.options.molterm3d_style, "lighting": vd.options.molterm3d_lighting, "hydrogens": 0}
    if width and height:
        fields.update(cell_width=width, cell_height=height)
    if pixels:
        fields["pixels"] = 1
    if graphics == "placeholders":
        fields["placeholders"] = 1
    terminal.write(b"\x1b[?25l\x1b[?1003h\x1b[?1006h" + (b"\x1b[?1016h" if pixels else b""))
    events, drawn, outcome, output = typed, None, 0, ""
    try:
        while True:
            size = terminal.size()
            if events or size != drawn:
                row, col, rows, cols = viewer_box(*size)
                if size != drawn:
                    terminal.write(_frame(row, col, rows, cols, title))
                with _Spinner(terminal, row + rows - 1, col + cols - 14, 12):
                    outcome, state, output = step({**fields, "events": events.hex(), "rows": size[0], "cols": size[1],
                                                   "box": f"{row + 1},{col + 1},{rows - 2},{cols - 2}"})
                fields.pop("input", None)
                fields["state"] = state
                drawn = size
                if outcome:
                    break
                if output:
                    terminal.write(output.encode("utf-8"))
            events = terminal.read(1.0)
    finally:
        out = []
        for image in VIEWER_IDS:
            command = f"\x1b_Ga=d,d=I,i={image if graphics == 'direct' else (image // 255) << 24 | (image % 255 + 1)},q=2\x1b\\"
            out.append(command if graphics == "direct" else "\x1bPtmux;" + command.replace("\x1b", "\x1b\x1b") + "\x1b\\")
        out.append("\x1b[?1016l\x1b[?1003l")
        terminal.write("".join(out).encode("ascii"))
    if outcome == 2:
        raise MoltermError(output.strip())


@Sheet.api
def viewMolecule3d(sheet):
    """Turn the current row's molecule in 3D, in a box over the sheet, until q."""
    column = _source_column(sheet, prefer_structure=True)
    row = sheet.cursorRow
    smiles = str(column.getValue(row) or "").strip() if row is not None else ""
    if not smiles:
        vd.fail("no SMILES in this row")
    try:
        graphics = editor_graphics("the 3D viewer")
    except MoltermError as error:
        vd.fail(str(error))
    if not available3d():
        vd.fail(f"3D is unavailable: no molterm-3d at {server3d_url()} (options.molterm3d_url)")
    label = next((str(c.getValue(row)) for c in sheet.visibleCols if c is not column and not isinstance(c, StructureColumn)), "")
    pictures.hide()
    terminal = Terminal()
    failure = None
    try:
        view_in_box(smiles, label or smiles, terminal, graphics)
    except MoltermError as error:
        failure = error
    finally:
        terminal.close()
        try:
            curses.mousemask(0)
            vd.enableMouse(bool(vd.options.mouse_interval))
            curses.curs_set(0)
        except curses.error:
            pass
        vd.redraw()
    if failure:
        vd.fail(f"molterm-3d: {failure}")


Sheet.addCommand("Alt+Shift+D", "addcol-molecule-3d", "addcolMolecule3d()", "add a column of each row's molecule in 3D, drawn by Mol* on a molterm-3d server")
Sheet.addCommand("Alt+Shift+V", "view-molecule-3d", "viewMolecule3d()", "turn the current row's molecule in 3D with the mouse, in a box over the sheet")
Sheet.addCommand("Alt+m", "addcol-molecule", "addcolMolecule()", "add a column drawing the molecule of each row's SMILES in the current column")
Sheet.addCommand("Alt+y", "addcol-molprops", "addcolMolprops()", "add columns of properties computed from the current column's SMILES")
Sheet.addCommand("Alt+o", "open-molecule", "openMolecule()", "open the current row's molecule on its own sheet, large, with its properties")
Sheet.addCommand("Alt+Shift+E", "edit-molecule", "editMolecule()", "draw the current row's molecule in molterm's editor; Done puts the new SMILES in its cell")
Sheet.addCommand("Alt+t", "toggle-molterm-mode", "toggleMoltermMode()", "draw molecules as pictures or as Braille text")
Sheet.addCommand("Alt+g", "plot-molecules", "plotMolecules()", "chart cLogP against MW, coloured by a category, with the molecule under the cursor")
GraphSheet.addCommand("Alt+n", "go-next-molecule", "moltermStep(1)", "put the cursor on the next molecule's dot")
GraphSheet.addCommand("Alt+b", "go-prev-molecule", "moltermStep(-1)", "put the cursor on the previous molecule's dot")
GraphSheet.addCommand("Alt+t", "toggle-molterm-mode", "toggleMoltermMode()", "draw molecules as pictures or as Braille text")

vd.addMenuItems("""
    Column > Add column > molterm structure > addcol-molecule
    Column > Add column > molterm properties > addcol-molprops
    Column > Add column > molterm 3D structure > addcol-molecule-3d
    View > molterm > 3D viewer > view-molecule-3d
    Edit > molterm > edit molecule > edit-molecule
    View > molterm > open molecule > open-molecule
    View > molterm > pictures or text > toggle-molterm-mode
    Plot > molterm chart of cLogP against MW > plot-molecules
""")
