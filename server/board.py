"""A text interface with structures in it: click one that is editable and the editor opens in a window over the rest.

It shows how to host the editor, and is itself useful for going through a list of compounds. Each structure keeps its annotations
(highlights, notes, atom numbers and stereo labels), read and printed as CXSMILES, and its right-click menu edits or copies it. Like the editor it runs entirely here, and
the client only passes on raw input. While the box is open, input goes to the editor, whose state travels inside this one, except for
what moves, resizes, minimizes or maximizes the box, which this does as a window manager would.
"""
import json
from functools import lru_cache

from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

import annotate
import editor
import graphics
import molecule
import samples

MAX_ITEMS = 60
MAX_ITEM = 500  # characters in a line of the list
MAX_NAME = 60
READ_ONLY = "ro:"
TILE_COLS, TILE_ROWS = 30, 11  # at most this tall, and at least this wide
MIN_TILE_ROWS = 6
BOTTOM_ROWS = 2
_FIRST_ID = 7100
_THUMB_PIXELS = 2  # image pixels per screen pixel, to stay sharp on a high-density screen
_MAX_BOND = 0.2  # a small molecule's bonds as a share of the width, so that ethanol doesn't fill the tile
COPY_LABEL = " copy "  # on the frame of the tile under the pointer
DRAGS = ("move", "resize", "resize_rows", "resize_cols")
_SHOW_LABELS = {"numbers": "Show Atom Numbers", "cip": "Show Stereo Labels"}


class BoardError(ValueError):
    """The list of structures can't be used."""


def _split_line(line):
    """(SMILES with its CXSMILES extension if it has one, name) from a line of the list."""
    parts = line.split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    if rest.startswith("|") and "|" in rest[1:]:
        end = rest.index("|", 1) + 1
        return f"{parts[0]} {rest[:end]}", rest[end:].strip()
    return (parts[0] if parts else ""), rest


def parse_items(text):
    """Lines of "SMILES name", the name optional, with ro: in front of what can't be edited. The SMILES may be CXSMILES with
    annotations, as this prints them. No lines gives a sample list."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return _sample_items()
    if len(lines) > MAX_ITEMS:
        raise BoardError(f"At most {MAX_ITEMS} structures.")
    items = []
    for line in lines:
        if len(line) > MAX_ITEM:
            raise BoardError(f"A line of at most {MAX_ITEM} characters, please: {line[:40]}...")
        editable = not line.startswith(READ_ONLY)
        written, name = _split_line(line if editable else line[len(READ_ONLY):])
        if not written:
            raise BoardError(f"No structure in: {line}")
        try:
            mol = molecule.parse(written, "smiles").mol
            smiles, notes = annotate.split(written)
            annotate.check(notes, mol.GetNumAtoms())
        except molecule.ParseError as error:
            raise BoardError(f"{written}: {error.message}") from error
        except ValueError as error:
            raise BoardError(f"{written[:60]}: {error}") from error
        items.append([name[:MAX_NAME], smiles, editable, False, notes])
    return items


def _sample_items():
    medicines = dict((name, smiles) for group, entries in samples.GROUPS for name, smiles, _ in entries)
    return [["Aspirin (reference)", medicines["Aspirin"], False, False, annotate.empty()]] + [
        [name, medicines[name], True, False, annotate.empty()] for name in ("Paracetamol", "Ibuprofen", "Fluoxetine", "Caffeine", "Nicotine", "Serotonin", "Penicillin G")]


def new_state(items):
    return {"items": items, "cursor": None, "top": 0, "press": None, "editing": None, "partial": "", "message": "", "shown": {}, "text": None, "size": None,
            "menu": None}


def _check_state(b):
    items = b["items"]
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise editor.StateError("The list is not valid.")
    for name, smiles, editable, edited, notes in items:
        if not (isinstance(name, str) and isinstance(smiles, str) and len(name) <= MAX_NAME and 0 < len(smiles) <= editor.MAX_STATE_JSON):
            raise editor.StateError("The list is not valid.")
        if editable not in (True, False) or edited not in (True, False):
            raise editor.StateError("The list is not valid.")
        try:
            annotate.check(notes, editor.MAX_ATOMS * 5)
        except annotate.NoteError as error:
            raise editor.StateError("The list is not valid.") from error
    menu = b["menu"]
    if menu is not None:
        if not (isinstance(menu, dict) and set(menu) == {"index", "col", "row", "hover"} and type(menu["index"]) is int and 0 <= menu["index"] < len(items)):
            raise editor.StateError("The list is not valid.")
        if not all(type(menu[name]) is int and 0 <= menu[name] <= 10_000 for name in ("col", "row")):
            raise editor.StateError("The list is not valid.")
        if menu["hover"] is not None and not (type(menu["hover"]) is int and 0 <= menu["hover"] < 20):
            raise editor.StateError("The list is not valid.")
    for name in ("cursor", "press"):
        if b[name] is not None and (type(b[name]) is not int or not 0 <= b[name] < len(items)):
            raise editor.StateError("The list is not valid.")
    if type(b["top"]) is not int or not 0 <= b["top"] <= MAX_ITEMS:
        raise editor.StateError("The list is not valid.")
    if not isinstance(b["partial"], str) or len(b["partial"]) > 64 or not b["partial"].isascii() or not isinstance(b["message"], str):
        raise editor.StateError("The list is not valid.")
    if not isinstance(b["shown"], dict) or len(b["shown"]) > MAX_ITEMS or not all(k.isdigit() and int(k) < MAX_ITEMS and isinstance(v, str) for k, v in b["shown"].items()):
        raise editor.StateError("The list is not valid.")
    for name in ("text", "size"):
        if b[name] is not None and not isinstance(b[name], str):
            raise editor.StateError("The list is not valid.")
    if b["editing"] is not None:
        editing = b["editing"]
        index, start = editing["index"], editing["start"]
        if type(index) is not int or not 0 <= index < len(items) or not items[index][2] or not (start is None or isinstance(start, str)):
            raise editor.StateError("The list is not valid.")
        drag = editing["drag"]
        if not (_is_box(editing["box"]) and (editing["restore"] is None or _is_box(editing["restore"])) and editing["minimized"] in (True, False)):
            raise editor.StateError("The list is not valid.")
        if drag is not None and not (drag["mode"] in DRAGS and _is_box(drag["box"]) and isinstance(drag["from"], list) and len(drag["from"]) == 2
                                     and all(isinstance(v, (int, float)) and abs(v) < 10_000 for v in drag["from"])):
            raise editor.StateError("The list is not valid.")
        editor.check_state(editing["state"])


def _is_box(box):
    return isinstance(box, list) and len(box) == 4 and all(type(v) is int and 0 <= v <= 10_000 for v in box)


def _encode(b):
    """As text, giving up the oldest undo steps of a drawing if the state would grow too large to come back."""
    text = editor.encode(b)
    undo = b["editing"]["state"]["undo"] if b["editing"] else []
    while len(text) > editor.MAX_STATE and undo:
        del undo[:max(len(undo) // 2, 1)]
        text = editor.encode(b)
    return text


class Grid:
    """Where the tiles are. Rows and columns count from 0 at the top left of the screen."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.area_rows = rows - 1 - BOTTOM_ROWS
        self.per_row = max(cols // TILE_COLS, 1)
        self.tile_cols = cols // self.per_row
        self.visible_rows = max(self.area_rows // TILE_ROWS, 1)
        self.tile_rows = min(self.area_rows // self.visible_rows, TILE_ROWS)

    def usable(self):
        return self.tile_rows >= MIN_TILE_ROWS

    def tile(self, index, top):
        """(row, col) of a tile's corner, or None if it's scrolled out of sight."""
        along = index // self.per_row - top
        if not 0 <= along < self.visible_rows:
            return None
        return 1 + along * self.tile_rows, index % self.per_row * self.tile_cols

    def tile_at(self, col, row, top, count):
        if col < 0 or row < 1 or row >= 1 + self.visible_rows * self.tile_rows:
            return None
        index = (int(row - 1) // self.tile_rows + top) * self.per_row + int(col) // self.tile_cols
        return index if index < count and int(col) // self.tile_cols < self.per_row else None

    def copy_label(self, index, top):
        """(row, col) of a tile's copy label, or None if it's scrolled out of sight."""
        place = self.tile(index, top)
        return place and (place[0], place[1] + self.tile_cols - 2 - len(COPY_LABEL))

    def on_copy_label(self, col, row, index, top):
        place = self.copy_label(index, top)
        return bool(place) and int(row) == place[0] and place[1] <= col < place[1] + len(COPY_LABEL)

    def last_top(self, count):
        return max((count - 1) // self.per_row + 1 - self.visible_rows, 0)


def box_for(cols, rows):
    """Where the editor opens: with a margin that shows it's over the list, unless the window needs the room."""
    height, width = min(rows - 2, 46), min(cols - 4, 130)
    if height < 14 or width < 60:
        height, width = rows, cols
    return (rows - height) // 2, (cols - width) // 2, height, width


@lru_cache(maxsize=256)
def _thumbnail(smiles, notes_json, cols, rows, cell_width, cell_height, theme):
    """A PNG filling a tile's picture exactly, shrunk to fit a large molecule, with its annotations."""
    notes = json.loads(notes_json)
    width, height = cols * cell_width * _THUMB_PIXELS, rows * cell_height * _THUMB_PIXELS
    drawer = rdMolDraw2D.MolDraw2DCairo(width, height)
    options = drawer.drawOptions()
    options.clearBackground = False
    options.setBackgroundColour((0, 0, 0, 0))
    options.setAtomPalette(graphics.palette(theme))
    options.fixedBondLength = width * _MAX_BOND
    options.padding = 0.06
    annotate.set_options(options, notes)
    mol = Chem.Mol(molecule.parse(smiles, "smiles").mol)
    if "cip" in notes["show"]:
        annotate.label_stereo(mol)
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol, **annotate.apply(mol, notes))
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def _style(colours, *names):
    return "\x1b[" + ";".join(colours[name] for name in names) + "m"


def _tile_text(b, grid, index, colours):
    """A tile's caption, and its frame if the pointer is on it, in cells without a background so that its picture still shows."""
    name, smiles, editable, edited, _ = b["items"][index]
    row, col = grid.tile(index, b["top"])
    width, height = grid.tile_cols, grid.tile_rows
    chosen = index == b["cursor"]
    words = ("edited" if edited else "") if editable else "read-only"
    shown = editor._fit_text(name or smiles, width - 4 - (len(words) + 1 if words else 0))
    out = [editor._ansi(row + height - 2, col + 2, f"{_style(colours, 'ink')}{shown}")]
    if words:
        out.append(editor._ansi(row + height - 2, col + width - 2 - len(words), f"{_style(colours, 'warn' if edited else 'dim')}{words}"))
    if chosen:
        edge = _style(colours, "pick_ink" if editable else "dim")
        out.append(editor._ansi(row, col, f"{edge}╭{'─' * (width - 2)}╮"))
        for along in range(row + 1, row + height - 1):
            out.append(editor._ansi(along, col, f"{edge}│") + editor._ansi(along, col + width - 1, f"{edge}│"))
        out.append(editor._ansi(row + height - 1, col, f"{edge}╰{'─' * (width - 2)}╯"))
        out.append(editor._ansi(*grid.copy_label(index, b["top"]), f"{_style(colours, 'ink')}{COPY_LABEL}"))
    return "".join(out)


def _list_text(b, grid):
    """The list's own cells: every row, since this also repaints what a closed box leaves behind."""
    return "".join(editor._ansi(row, 0, "\x1b[2K") for row in range(grid.rows))


def _status_lines(b, grid, colours):
    dim, warn = _style(colours, "dim"), _style(colours, "warn")
    if _shown(b):  # the box has its own, and this would only show beside it
        return "", ""
    if b["message"]:
        second = warn + editor._fit_text(b["message"], grid.cols)
    else:
        second = dim + editor._fit_text("Click a structure to edit it, or right-click it to copy it as SMILES, SVG or PNG. c copies its SMILES. q quits.", grid.cols)
    if b["cursor"] is None:
        return "", second
    _, smiles, editable, _, notes = b["items"][b["cursor"]]
    first = editor._fit_text(annotate.write(smiles, notes), grid.cols)
    details = editor.facts(smiles)
    if not b["message"]:
        words = "Click to edit." if editable else "Read-only: this one can't be edited."
        second = dim + editor._fit_text(f"{details}   {words}", grid.cols)
    return f"{_style(colours, 'ink')}{first}", second


def _list_frame(b, grid, theme, ui_cells):
    """The list, sending only the pictures that changed."""
    cell_width, cell_height = ui_cells
    colours = editor._CHROME.get(theme, editor._CHROME["dark"])
    out = []
    wanted = {}
    picture_cols, picture_rows = grid.tile_cols - 2, grid.tile_rows - 3
    for index in range(len(b["items"])):
        place = grid.tile(index, b["top"])
        if place:
            notes = json.dumps(b["items"][index][4], sort_keys=True)
            wanted[str(index)] = editor.hashlib.sha256(f"{b['items'][index][1]}|{notes}|{place}|{picture_cols}x{picture_rows}|{cell_width}x{cell_height}|{theme}".encode()).hexdigest()[:24]
    for key in list(b["shown"]):
        if key not in wanted:
            out.append(f"\x1b_Ga=d,d=I,i={_FIRST_ID + int(key)},q=2\x1b\\")
            del b["shown"][key]
    for key, digest in wanted.items():
        if b["shown"].get(key) != digest:
            index = int(key)
            row, col = grid.tile(index, b["top"])
            png = _thumbnail(b["items"][index][1], json.dumps(b["items"][index][4], sort_keys=True), picture_cols, picture_rows, cell_width, cell_height, theme)
            # below the editor's backing, so that the box hides it without its having to be taken away and sent again
            sequence = graphics.kitty_sequence(png, picture_cols, rows=picture_rows, image_id=_FIRST_ID + index, move_cursor=False, z=editor.HOST_Z).rstrip("\n")
            out.append(f"\x1b_Ga=d,d=I,i={_FIRST_ID + index},q=2\x1b\\" + editor._ansi(row + 1, col + 1) + sequence)
            b["shown"][key] = digest
    count, editable = len(b["items"]), sum(1 for item in b["items"] if item[2])
    edited = sum(1 for item in b["items"] if item[3])
    bar = _style(colours, "bar", "ink")
    heading = f" Molterm   {count} structures, {editable} editable" + (f", {edited} edited" if edited else "")
    first, second = _status_lines(b, grid, colours)
    tiles = "".join(_tile_text(b, grid, index, colours) for index in range(count) if grid.tile(index, b["top"]))
    text = _list_text(b, grid) + editor._ansi(0, 0, f"{bar}\x1b[K{editor._fit_text(heading, grid.cols)}") + tiles
    chip = _chip(b, grid)
    if chip:
        text += editor._ansi(0, chip[0], _style(colours, "pick", "on_pick") + chip[1])
    text += editor._ansi(grid.rows - 2, 0, first) + editor._ansi(grid.rows - 1, 0, second) + "\x1b[0m"
    text += _menu_text(b, grid, colours)
    if b["text"] != text:
        out.append(text)
        b["text"] = text
    return "".join(out)


def _open(b, index, cols, rows, ui_args):
    """Open the box on a structure."""
    name, smiles, editable, _, notes = b["items"][index]
    if not editable:
        b["message"] = f"{name or smiles} is read-only."
        return
    if b["editing"]:  # only when it's minimized, as the list has the input otherwise
        if b["editing"]["index"] == index:
            _restore(b)
        else:
            b["message"] = f"{_title(b, b['editing']['index'])} is still open: bring it back from the top bar first."
        return
    box = box_for(cols, rows)
    ui = editor.Layout(cols, rows, *ui_args, box=box, title=_title(b, index), window=True)
    if not editor.fits(ui):
        b["message"] = "This window is too small for the editor."
        return
    state = editor.new_state()
    if not editor.load_smiles(state, annotate.write(smiles, notes)):
        b["message"] = state["message"]
        return
    editor._fit(state, ui)
    b["editing"] = {"index": index, "state": state, "start": editor.annotated(state), "box": list(box), "restore": None, "minimized": False, "drag": None}
    b["message"], b["text"] = "", None


def _title(b, index):
    name, smiles = b["items"][index][:2]
    return f"Edit {name or smiles}"


def _close(b, status, answer):
    """Take a finished edit back into the list. A drawing left as it was keeps its SMILES exactly, as the editor doesn't carry everything a
    SMILES can say, such as isotopes."""
    editing = b["editing"]
    item = b["items"][editing["index"]]
    b["editing"] = None
    b["text"] = None  # the box's cells must go
    if status == editor.CANCELLED:
        b["message"] = "Cancelled: nothing changed."
    elif answer == editing["start"]:
        b["message"] = "No change."
    elif not answer:
        b["message"] = "An empty drawing isn't saved, so the structure stays as it was."
    else:
        (item[1], item[4]), item[3] = annotate.split(answer), True
        b["message"] = f"Saved {item[0] or item[1]}."
    return editor.images_gone()


def _shown(b):
    """Is the editor's window open and not minimized?"""
    return bool(b["editing"]) and not b["editing"]["minimized"]


def _repaint(b):
    """After the window moves, changes size or goes, the list and the editor are both drawn again, in that order."""
    b["text"] = None
    b["editing"]["state"]["size"] = None


def _fits(box, cols, rows, ui_args):
    return (box[0] >= 0 and box[1] >= 0 and box[0] + box[2] <= rows and box[1] + box[3] <= cols
            and editor.fits(editor.Layout(cols, rows, *ui_args, box=tuple(box), window=True)))


def _keep_in(box, cols, rows, ui_args):
    """A box kept inside the window, shrunk if it must be, or where a new one would open if it can't be made to fit."""
    height, width = min(box[2], rows), min(box[3], cols)
    kept = [max(min(box[0], rows - height), 0), max(min(box[1], cols - width), 0), height, width]
    return kept if _fits(kept, cols, rows, ui_args) else list(box_for(cols, rows))


def _minimize(b):
    b["editing"]["minimized"], b["editing"]["state"]["button"] = True, None
    b["message"] = f"{_title(b, b['editing']['index'])} is minimized. Click it in the top bar to bring it back."
    _repaint(b)
    return editor.images_gone()


def _restore(b):
    b["editing"]["minimized"], b["message"] = False, ""
    _repaint(b)


def _maximize(b, cols, rows, ui_args):
    """Fill the window, or go back to the size it had."""
    editing = b["editing"]
    if editing["restore"]:
        editing["box"], editing["restore"] = _keep_in(editing["restore"], cols, rows, ui_args), None
    else:
        editing["restore"], editing["box"] = editing["box"], [0, 0, rows, cols]
    _repaint(b)


def _window_part(b, cols, rows, ui_args, col, row):
    """What of the window's frame is at a point: one of its buttons, or how dragging it there changes the window, or None for the editor's."""
    top, left, height, width = b["editing"]["box"]
    ui = _box_layout(b, cols, rows, ui_args)
    inside_col, inside_row = col - ui.origin[1], row - ui.origin[0]
    along, across = int(row) if row >= 0 else -1, int(col) if col >= 0 else -1
    if not (top <= along < top + height and left <= across < left + width):
        return None
    if along == top:
        return ui.button_at(inside_col, inside_row) or "move"
    if along == top + height - 1:
        if across == left + width - 1:
            return "resize"
        return None if ui.button_at(inside_col, inside_row) else "resize_rows"
    return "resize_cols" if across == left + width - 1 else None


def _drag_window(b, cols, rows, ui_args, col, row):
    editing = b["editing"]
    drag = editing["drag"]
    top, left, height, width = drag["box"]
    down, right = round(row - drag["from"][1]), round(col - drag["from"][0])
    if drag["mode"] == "move":
        wanted = [max(min(top + down, rows - height), 0), max(min(left + right, cols - width), 0), height, width]
    else:
        taller = height + (down if drag["mode"] in ("resize", "resize_rows") else 0)
        wider = width + (right if drag["mode"] in ("resize", "resize_cols") else 0)
        taller, wider = min(taller, rows - top), min(wider, cols - left)
        # as near as the editor allows, keeping the size it has where the new one is too small
        wanted = next((box for box in ([top, left, taller, wider], [top, left, editing["box"][2], wider], [top, left, taller, editing["box"][3]])
                       if _fits(box, cols, rows, ui_args)), editing["box"])
    if wanted != editing["box"]:
        editing["box"], editing["restore"] = wanted, None
        _repaint(b)


def _window_event(b, event, cols, rows, ui_args, pixels):
    """Moving, resizing, minimizing and maximizing the editor's window. Returns (whether it took the event, what to print)."""
    if event[0] != "mouse":
        return False, ""
    editing = b["editing"]
    code, x, y, release = event[1:]
    col, row = (x / ui_args[0], y / ui_args[1]) if pixels else (x - 0.5, y - 0.5)
    if editing["drag"]:
        if code & 32 and not release:
            _drag_window(b, cols, rows, ui_args, col, row)
        else:
            editing["drag"] = None
        return True, ""
    if code & (3 | 32 | 64) or release:
        return False, ""
    part = _window_part(b, cols, rows, ui_args, col, row)
    if part == "minimize":
        return True, _minimize(b)
    if part == "maximize":
        _maximize(b, cols, rows, ui_args)
    elif part in DRAGS:
        editing["drag"] = {"mode": part, "from": [col, row], "box": list(editing["box"])}
    else:
        return False, ""
    return True, ""


def _chip(b, grid):
    """The minimized window's place in the top bar: (col, text), or None."""
    if not b["editing"] or not b["editing"]["minimized"]:
        return None
    text = f" □ {editor._fit_text(_title(b, b['editing']['index']), grid.cols // 2)} "
    return grid.cols - len(text) - 1, text


def _move_cursor(b, grid, name):
    count = len(b["items"])
    if b["cursor"] is None:
        b["cursor"] = b["top"] * grid.per_row
        return
    step = {"left": -1, "right": 1, "up": -grid.per_row, "down": grid.per_row}[name]
    if 0 <= b["cursor"] + step < count:
        b["cursor"] += step
    along = b["cursor"] // grid.per_row
    b["top"] = min(max(b["top"], along - grid.visible_rows + 1), along)


def _copy(b, index, copies, kind="smiles"):
    """Copy a tile, editable or not, with its annotations: as text, and for an SVG or PNG as a picture too."""
    name, smiles, _, _, notes = b["items"][index]
    mol = molecule.parse(smiles, "smiles").mol
    copies.append(annotate.clipboard_sequence(annotate.clipboard(smiles, mol, notes, kind)))
    if kind == "smiles":
        b["message"] = f"The SMILES of {name or smiles} went to the clipboard."
    else:
        b["message"] = f"{name or smiles}: " + editor.COPIED[kind]


def _menu_items(b, index):
    """A structure's right-click menu: (label, action), with None for a dividing line."""
    _, _, editable, _, notes = b["items"][index]
    items = [("Edit", ("edit", None)), None] if editable else []
    items += [(label, ("copy", kind)) for label, kind in (("Copy SMILES", "smiles"), ("Copy SVG", "svg"), ("Copy PNG", "png"),
                                                          ("Copy SMILES, SVG and PNG", "all"))]
    if editable:
        items += [None] + [(("✓ " if name in notes["show"] else "  ") + label, ("show", name)) for name, label in _SHOW_LABELS.items()]
        if not annotate.is_empty(notes):
            items.append(("Clear Annotations", ("clear", None)))
    return items


def _menu_place(b, grid):
    """(col, row, width) of the open menu, kept on the screen."""
    menu = b["menu"]
    items = _menu_items(b, menu["index"])
    width = max(len(item[0]) for item in items if item) + 2
    return max(min(menu["col"], grid.cols - width), 0), max(min(menu["row"], grid.rows - len(items)), 1), width


def _menu_text(b, grid, colours):
    if not b["menu"]:
        return ""
    col, row, width = _menu_place(b, grid)
    out = []
    for position, item in enumerate(_menu_items(b, b["menu"]["index"])):
        if item is None:
            text, style = "─" * width, _style(colours, "bar", "dim")
        else:
            text = f" {item[0]:<{width - 1}}"
            style = _style(colours, "pick", "on_pick") if position == b["menu"]["hover"] else _style(colours, "bar", "ink")
        out.append(editor._ansi(row + position, col, style + text))
    return "".join(out) + "\x1b[0m"


def _menu_at(b, grid, col, row):
    col_start, row_start, width = _menu_place(b, grid)
    position = int(row) - row_start
    items = _menu_items(b, b["menu"]["index"])
    if col_start <= col < col_start + width and 0 <= position < len(items) and items[position]:
        return position
    return None


def _open_menu(b, index, col, row):
    b["menu"] = {"index": index, "col": int(col), "row": int(row), "hover": None}
    b["cursor"], b["message"] = index, ""


def _choose(b, cols, rows, ui_args, copies):
    """Do what the hovered item of the menu says, and close it."""
    menu, b["menu"] = b["menu"], None
    items = _menu_items(b, menu["index"])
    if menu["hover"] is None or not items[menu["hover"]]:
        return
    kind, value = items[menu["hover"]][1]
    item = b["items"][menu["index"]]
    if kind == "edit":
        _open(b, menu["index"], cols, rows, ui_args)
    elif kind == "copy":
        _copy(b, menu["index"], copies, value)
    elif kind == "show":
        shown = item[4]["show"]
        item[4]["show"] = [name for name in annotate.SHOWN if (name in shown) != (name == value)]
        item[3] = True
    elif kind == "clear":
        item[4], item[3] = annotate.empty(), True
        b["message"] = f"Took the annotations off {item[0] or item[1]}."


def _menu_event(b, grid, event, cols, rows, ui_args, pixels, copies):
    """An event while the menu is open. Returns True if the menu took it."""
    if event[0] == "key":
        items = _menu_items(b, b["menu"]["index"])
        choices = [i for i, item in enumerate(items) if item]
        if event[1] in ("up", "down"):
            position = choices.index(b["menu"]["hover"]) if b["menu"]["hover"] in choices else (-1 if event[1] == "down" else 0)
            b["menu"]["hover"] = choices[(position + (1 if event[1] == "down" else -1)) % len(choices)]
        elif event[1] == "enter":
            _choose(b, cols, rows, ui_args, copies)
        else:
            b["menu"] = None
        return True
    if event[0] != "mouse":
        return False
    code, x, y, release = event[1:]
    col, row = (x / ui_args[0], y / ui_args[1]) if pixels else (x - 0.5, y - 0.5)
    if code & 64 or release:
        return True
    if code & 32:
        b["menu"]["hover"] = _menu_at(b, grid, col, row)
        return True
    b["menu"]["hover"] = _menu_at(b, grid, col, row)
    if b["menu"]["hover"] is not None and code & 3 == 0:
        _choose(b, cols, rows, ui_args, copies)
        return True
    b["menu"] = None
    return code & 3 != 2  # a right-click elsewhere opens the menu there


def _handle_list(b, grid, event, cols, rows, ui_args, pixels, copies):
    """One event for the list; returns True to quit. What to copy goes on `copies`, since it isn't kept in the state."""
    if event[0] == "clipboard":
        b["message"] = editor.copy_answer(event[1])
        return False
    if b["menu"] and _menu_event(b, grid, event, cols, rows, ui_args, pixels, copies):
        return False
    if event[0] == "key":
        name = event[1]
        b["message"] = ""
        if name in ("q", "quit", "interrupt"):
            if not b["editing"]:
                return True
            b["message"] = f"{_title(b, b['editing']['index'])} is still open: bring it back from the top bar, then Done or Cancel."
        if name in ("left", "right", "up", "down"):
            _move_cursor(b, grid, name)
        elif name == "c":
            if b["cursor"] is None:
                b["message"] = "Point at a structure first, then press c to copy its SMILES."
            else:
                _copy(b, b["cursor"], copies)
        elif name == "enter" and b["cursor"] is not None:
            _open(b, b["cursor"], cols, rows, ui_args)
        elif name in ("/", "m") and b["cursor"] is not None:
            place = grid.tile(b["cursor"], b["top"])
            if place:
                _open_menu(b, b["cursor"], place[1] + 2, place[0] + 2)
        elif name == "redraw":
            b["size"] = None
        return False
    code, x, y, release = event[1:]
    if pixels:
        col, row = x / ui_args[0], y / ui_args[1]
    else:
        col, row = x - 0.5, y - 0.5
    hit = grid.tile_at(col, row, b["top"], len(b["items"]))
    chip = _chip(b, grid)
    if chip and int(row) == 0 and chip[0] <= col < chip[0] + len(chip[1]) and not code & (3 | 32 | 64) and not release:
        _restore(b)
        return False
    if code & 64:
        b["top"] = max(min(b["top"] + (1 if code & 3 else -1), grid.last_top(len(b["items"]))), 0)
        b["cursor"], b["message"] = grid.tile_at(col, row, b["top"], len(b["items"])), ""
    elif code & 32:
        if hit != b["cursor"]:
            b["message"] = ""
        b["cursor"] = hit
    elif code & 3 == 2 and not release:
        if hit is not None:
            _open_menu(b, hit, col, row)
    elif release:
        pressed, b["press"] = b["press"], None
        if hit is not None and hit == pressed and code & 3 == 0:  # on letting go, so that the release doesn't land in the editor
            if grid.on_copy_label(col, row, hit, b["top"]):
                _copy(b, hit, copies)
            else:
                _open(b, hit, cols, rows, ui_args)
    else:
        b["press"] = hit
        b["cursor"] = hit
        b["message"] = ""
    return False


def _final_list(b):
    return "\n".join(f"{annotate.write(smiles, notes)} {name}".rstrip() for name, smiles, _, _, notes in b["items"])


def step(state_text, items_text, raw, cols, rows, cell_width=None, cell_height=None, pixels=False, theme=graphics.DEFAULT_THEME):
    """Run one request, as editor.step does: return (status, new state as text, what to print)."""
    if state_text:
        b = editor.decode(state_text, _check_state)
    else:
        try:
            b = new_state(parse_items(items_text))
        except BoardError as error:
            return editor.FAILED, "", str(error)
    pixels = pixels and bool(cell_width) and bool(cell_height)
    grid = Grid(cols, rows)
    measure = editor.Layout(cols, rows, cell_width, cell_height)
    ui_args = (measure.cell_width, measure.cell_height, pixels)
    if not grid.usable():
        return editor.CONTINUE, _encode(b), "\x1b[2J\x1b[H" + "This window is too small for the list."
    events, b["partial"] = editor.parse_events(b["partial"] + raw)
    if len(b["partial"]) > 64:
        b["partial"] = ""
    out = ["\x1b[?2026h"]
    copies = []  # sequences for the terminal: what was copied isn't kept in the state
    size = f"{cols}x{rows}"
    if b["size"] != size:
        out.append("\x1b[2J")  # which also removes the images
        b["size"], b["shown"], b["text"] = size, {}, None
        if b["editing"]:
            b["editing"]["state"]["size"] = None
            maximized = b["editing"]["restore"] is not None
            b["editing"]["box"] = [0, 0, rows, cols] if maximized else _keep_in(b["editing"]["box"], cols, rows, ui_args)
    b["top"] = min(b["top"], grid.last_top(len(b["items"])))
    for event in events:
        if _shown(b):
            taken, printed = _window_event(b, event, cols, rows, ui_args, pixels)
            if taken:
                out.append(printed)
                continue
            ui = _box_layout(b, cols, rows, ui_args)
            ended = editor.handle_events(b["editing"]["state"], ui, [event], pixels)
            finished = editor.finish(b["editing"]["state"], ended) if ended else None
            if finished:
                out.append(_close(b, *finished))
        elif _handle_list(b, grid, event, cols, rows, ui_args, pixels, copies):
            return editor.QUIT, "", _final_list(b)
    if not _shown(b) or b["text"] is None:
        out.append(_list_frame(b, grid, theme, ui_args[:2]))
    if _shown(b):
        out.append(editor.take_clipboard(b["editing"]["state"]))
        out.append(editor.frame(b["editing"]["state"], _box_layout(b, cols, rows, ui_args), theme))
    out.extend(copies)
    out.append("\x1b[?2026l")
    text = "".join(out)
    return editor.CONTINUE, _encode(b), "" if text == "\x1b[?2026h\x1b[?2026l" else text


def _box_layout(b, cols, rows, ui_args):
    return editor.Layout(cols, rows, *ui_args, box=tuple(b["editing"]["box"]), title=_title(b, b["editing"]["index"]), window=True)
