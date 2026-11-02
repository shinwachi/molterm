"""A text interface with structures in it: click one that is editable and the editor opens in a box over the rest.

It shows how to host the editor, and is itself useful for going through a list of compounds. Like the editor it runs entirely here, and
the client only passes on raw input. While the box is open, input goes to the editor, whose state travels inside this one.
"""
from functools import lru_cache

from rdkit.Chem.Draw import rdMolDraw2D

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


class BoardError(ValueError):
    """The list of structures can't be used."""


def parse_items(text):
    """Lines of "SMILES name", the name optional, with ro: in front of what can't be edited. No lines gives a sample list."""
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
        parts = (line if editable else line[len(READ_ONLY):]).split(None, 1)
        if not parts:
            raise BoardError(f"No structure in: {line}")
        try:
            molecule.parse(parts[0], "smiles")
        except molecule.ParseError as error:
            raise BoardError(f"{parts[0]}: {error.message}") from error
        items.append([(parts[1] if len(parts) > 1 else "")[:MAX_NAME], parts[0], editable, False])
    return items


def _sample_items():
    medicines = dict((name, smiles) for group, entries in samples.GROUPS for name, smiles, _ in entries)
    return [["Aspirin (reference)", medicines["Aspirin"], False, False]] + [
        [name, medicines[name], True, False] for name in ("Paracetamol", "Ibuprofen", "Fluoxetine", "Caffeine", "Nicotine", "Serotonin", "Penicillin G")]


def new_state(items):
    return {"items": items, "cursor": None, "top": 0, "press": None, "editing": None, "partial": "", "message": "", "shown": {}, "text": None, "size": None}


def _check_state(b):
    items = b["items"]
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise editor.StateError("The list is not valid.")
    for name, smiles, editable, edited in items:
        if not (isinstance(name, str) and isinstance(smiles, str) and len(name) <= MAX_NAME and 0 < len(smiles) <= editor.MAX_STATE_JSON):
            raise editor.StateError("The list is not valid.")
        if editable not in (True, False) or edited not in (True, False):
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
        index, start = b["editing"]["index"], b["editing"]["start"]
        if type(index) is not int or not 0 <= index < len(items) or not items[index][2] or not (start is None or isinstance(start, str)):
            raise editor.StateError("The list is not valid.")
        editor.check_state(b["editing"]["state"])


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

    def last_top(self, count):
        return max((count - 1) // self.per_row + 1 - self.visible_rows, 0)


def box_for(cols, rows):
    """Where the editor opens: with a margin that shows it's over the list, unless the window needs the room."""
    height, width = min(rows - 2, 46), min(cols - 4, 130)
    if height < 14 or width < 60:
        height, width = rows, cols
    return (rows - height) // 2, (cols - width) // 2, height, width


@lru_cache(maxsize=256)
def _thumbnail(smiles, cols, rows, cell_width, cell_height, theme):
    """A PNG filling a tile's picture exactly, shrunk to fit a large molecule."""
    width, height = cols * cell_width * _THUMB_PIXELS, rows * cell_height * _THUMB_PIXELS
    drawer = rdMolDraw2D.MolDraw2DCairo(width, height)
    options = drawer.drawOptions()
    options.clearBackground = False
    options.setBackgroundColour((0, 0, 0, 0))
    options.setAtomPalette(graphics.palette(theme))
    options.fixedBondLength = width * _MAX_BOND
    options.padding = 0.06
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, molecule.parse(smiles, "smiles").mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def _style(colours, *names):
    return "\x1b[" + ";".join(colours[name] for name in names) + "m"


def _tile_text(b, grid, index, colours):
    """A tile's caption, and its frame if the pointer is on it, in cells without a background so that its picture still shows."""
    name, smiles, editable, edited = b["items"][index]
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
    return "".join(out)


def _list_text(b, grid):
    """The list's own cells: every row, since this also repaints what a closed box leaves behind."""
    return "".join(editor._ansi(row, 0, "\x1b[2K") for row in range(grid.rows))


def _status_lines(b, grid, colours):
    dim, warn = _style(colours, "dim"), _style(colours, "warn")
    if b["editing"]:  # the box has its own, and this would only show beside it
        return "", ""
    if b["message"]:
        second = warn + editor._fit_text(b["message"], grid.cols)
    else:
        second = dim + editor._fit_text("Click a structure to edit it, or use the arrows and Enter. The wheel scrolls. q quits and prints the list.", grid.cols)
    if b["cursor"] is None:
        return "", second
    _, smiles, editable, _ = b["items"][b["cursor"]]
    first = editor._fit_text(smiles, grid.cols)
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
            wanted[str(index)] = f"{b['items'][index][1]}|{place}|{picture_cols}x{picture_rows}|{cell_width}x{cell_height}|{theme}"
    for key in list(b["shown"]):
        if key not in wanted:
            out.append(f"\x1b_Ga=d,d=I,i={_FIRST_ID + int(key)},q=2\x1b\\")
            del b["shown"][key]
    for key, digest in wanted.items():
        if b["shown"].get(key) != digest:
            index = int(key)
            row, col = grid.tile(index, b["top"])
            png = _thumbnail(b["items"][index][1], picture_cols, picture_rows, cell_width, cell_height, theme)
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
    text += editor._ansi(grid.rows - 2, 0, first) + editor._ansi(grid.rows - 1, 0, second) + "\x1b[0m"
    if b["text"] != text:
        out.append(text)
        b["text"] = text
    return "".join(out)


def _open(b, index, cols, rows, ui_args):
    """Open the box on a structure."""
    name, smiles, editable, _ = b["items"][index]
    if not editable:
        b["message"] = f"{name or smiles} is read-only."
        return
    box = box_for(cols, rows)
    ui = editor.Layout(cols, rows, *ui_args, box=box, title=_title(b, index))
    if not editor.fits(ui):
        b["message"] = "This window is too small for the editor."
        return
    state = editor.new_state()
    if not editor.load_smiles(state, smiles):
        b["message"] = state["message"]
        return
    editor._fit(state, ui)
    b["editing"] = {"index": index, "state": state, "start": editor.analyse(state)[0]}
    b["message"], b["text"] = "", None


def _title(b, index):
    name, smiles, _, _ = b["items"][index]
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
        item[1], item[3] = answer, True
        b["message"] = f"Saved {item[0] or answer}."
    return editor.images_gone()


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


def _handle_list(b, grid, event, cols, rows, ui_args, pixels):
    """One event for the list; returns True to quit."""
    if event[0] == "key":
        name = event[1]
        b["message"] = ""
        if name in ("q", "quit", "interrupt"):
            return True
        if name in ("left", "right", "up", "down"):
            _move_cursor(b, grid, name)
        elif name == "enter" and b["cursor"] is not None:
            _open(b, b["cursor"], cols, rows, ui_args)
        elif name == "redraw":
            b["size"] = None
        return False
    code, x, y, release = event[1:]
    if pixels:
        col, row = x / ui_args[0], y / ui_args[1]
    else:
        col, row = x - 0.5, y - 0.5
    hit = grid.tile_at(col, row, b["top"], len(b["items"]))
    if code & 64:
        b["top"] = max(min(b["top"] + (1 if code & 3 else -1), grid.last_top(len(b["items"]))), 0)
        b["cursor"], b["message"] = grid.tile_at(col, row, b["top"], len(b["items"])), ""
    elif code & 32:
        b["cursor"] = hit
    elif release:
        pressed, b["press"] = b["press"], None
        if hit is not None and hit == pressed and code & 3 == 0:  # on letting go, so that the release doesn't land in the editor
            _open(b, hit, cols, rows, ui_args)
    else:
        b["press"] = hit
        b["cursor"] = hit
        b["message"] = ""
    return False


def _final_list(b):
    return "\n".join(f"{smiles} {name}".rstrip() for name, smiles, _, _ in b["items"])


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
    size = f"{cols}x{rows}"
    if b["size"] != size:
        out.append("\x1b[2J")  # which also removes the images
        b["size"], b["shown"], b["text"] = size, {}, None
        if b["editing"]:
            b["editing"]["state"]["size"] = None
    b["top"] = min(b["top"], grid.last_top(len(b["items"])))
    for position, event in enumerate(events):
        if b["editing"]:
            ui = _box_layout(b, cols, rows, ui_args)
            ended = editor.handle_events(b["editing"]["state"], ui, [event], pixels)
            finished = editor.finish(b["editing"]["state"], ended) if ended else None
            if finished:
                out.append(_close(b, *finished))
        elif _handle_list(b, grid, event, cols, rows, ui_args, pixels):
            return editor.QUIT, "", _final_list(b)
    if not b["editing"] or b["text"] is None:
        painted = _list_frame(b, grid, theme, ui_args[:2])
        out.append(painted)
    if b["editing"]:
        out.append(editor.frame(b["editing"]["state"], _box_layout(b, cols, rows, ui_args), theme))
    out.append("\x1b[?2026l")
    text = "".join(out)
    return editor.CONTINUE, _encode(b), "" if text == "\x1b[?2026h\x1b[?2026l" else text


def _box_layout(b, cols, rows, ui_args):
    return editor.Layout(cols, rows, *ui_args, box=box_for(cols, rows), title=_title(b, b["editing"]["index"]))
