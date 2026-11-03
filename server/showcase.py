"""The automated demo: the same molecules drawn as Kitty graphics and as text, then a real edit in the boxed editor, black on white.

Every frame here is real output: the drawings come from the same code as /api/v1/render.txt, and the edit is mouse and key input put through
board.step, as `molterm tui` would send it. The web page only prints the frames in turn and moves a pointer to show where the input went.
"""

import board
import editor
import graphics
import molecule
import render as renderer

THEME = "mono"
MIN_COLS, MIN_ROWS = 90, 30
ITEMS = ""  # the list's own samples: Aspirin, read-only, then Paracetamol and others
EDITED = "CCOc1ccc(NC(C)=O)cc1F"  # what the edit below must give
COMPOUNDS = (("Caffeine", "Cn1cnc2c1c(=O)n(C)c(=O)n2C"),
             ("Paracetamol", "CC(=O)Nc1ccc(O)cc1"),
             ("Serotonin", "NCCc1c[nH]c2ccc(O)cc12"),
             ("Imatinib", "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1"))
_IMAGE_CELL = 30  # pixels per column before enlarging: stays sharp, and fits the web terminal's store of images
_MAX_ENLARGE = 2.6  # about the size of ASCII's largest bonds
_FOG_CELL = 20  # fog has no fine detail, and two to a screen take up more of that store
PASTED_FOG = 50
_HOME = "\x1b_Ga=d,q=2\x1b\\\x1b[2J\x1b[H\x1b[0m"
_BOLD = "\x1b[1m"


class ShowcaseError(ValueError):
    """The window can't hold the demo."""


def _frame(caption, output, pointer=None, click=False, key=None, pause=2200):
    return {"caption": caption, "output": output, "pointer": pointer, "click": click, "key": key, "pause": pause}


def _side_by_side(name, smiles, mode, cols, rows, cell_width, cell_height):
    """A molecule as an image on the left and as text on the right, each with a heading. The image is enlarged to match, as far as there's room."""
    mol = molecule.parse(smiles, "smiles").mol
    half, height = cols // 2 - 2, rows - 4
    image, used = graphics.draw_image(mol, half, height, THEME, _IMAGE_CELL)
    fit_rows = height * cell_height * image.width / (image.height * cell_width)  # the columns at which it would fill the height
    shown = max(min(round(used * _MAX_ENLARGE), half, int(fit_rows)), 1)
    picture = graphics.kitty_sequence(graphics._png(image), shown).rstrip("\n")
    text = renderer.render(mol, half, height, mode).text.split("\n")
    title = "ASCII" if mode == "ascii" else "Braille"
    out = [_HOME, editor._ansi(0, 0, f"{_BOLD}{name}"), editor._ansi(1, 0, "Kitty graphics"), editor._ansi(1, cols // 2, title), editor._ansi(3, 0), picture]
    out.extend(editor._ansi(3 + index, cols // 2, line[:half]) for index, line in enumerate(text[:height]))
    return "".join(out)


def _settings(name, smiles, left, right, cols, rows):
    """A molecule with two fog settings, each (theme, fog), at the same scale and with the options that give it."""
    mol = molecule.parse(smiles, "smiles").mol
    half, height = cols // 2 - 2, rows - 4
    drawn = [graphics.styled_image(mol, half, height, theme, _FOG_CELL, fog) for theme, fog in (left, right)]
    enlarge = min(_MAX_ENLARGE, *(half / used for _, used in drawn))
    out = [_HOME, editor._ansi(0, 0, f"{_BOLD}{name}\x1b[0m")]
    for (theme, fog), (image, used), col in zip((left, right), drawn, (0, cols // 2)):
        out.append(editor._ansi(1, col, f"-t {theme}" + (f" -f {fog}" if fog else "")))
        out.append(editor._ansi(3, col) + graphics.kitty_sequence(graphics._png(image), max(round(used * enlarge), 1), move_cursor=False).rstrip("\n"))
    return "".join(out)


def _pasted(smiles, cols):
    """A shell, with the copied SMILES pasted into a command, and what that command prints."""
    mol = molecule.parse(smiles, "smiles").mol
    command = f"$ molterm -t {THEME} -f {PASTED_FOG} '{smiles}'"
    return _HOME + editor._ansi(0, 0, command) + editor._ansi(2, 0) + graphics.render(mol, graphics.KITTY, cols, renderer.DEFAULT_ROWS, THEME, fog=PASTED_FOG).rstrip("\n")


class _Session:
    """The list of structures, driven the way the client would, with the frames it answers."""

    def __init__(self, cols, rows, cell_width, cell_height):
        self.cols, self.rows, self.cell = cols, rows, (cell_width, cell_height)
        self.state, self.frames, self.status = "", [], editor.CONTINUE
        self.output = self._send("", ITEMS)

    def _send(self, raw, items=""):
        self.status, self.state, output = board.step(self.state, items, raw, self.cols, self.rows, *self.cell, True, THEME)
        return output

    def _report(self, code, col, row, end="M"):
        return f"\x1b[<{code};{int(col * self.cell[0]) + 1};{int(row * self.cell[1]) + 1}{end}"

    def hover(self, place, caption, pause=1800):
        output = self._send(self._report(35, *place))
        self.frames.append(_frame(caption, output, list(place), pause=pause))

    def click(self, place, caption, pause=2200):
        output = self._send(self._report(0, *place) + self._report(0, *place, "m"))
        self.frames.append(_frame(caption, output, list(place), click=True, pause=pause))

    def key(self, name, caption, pause=2400):
        output = self._send(name)
        self.frames.append(_frame(caption, output, key=name, pause=pause))

    def board(self):
        return editor.decode(self.state, board._check_state)

    def tile(self, index):
        grid, b = board.Grid(self.cols, self.rows), self.board()
        row, col = grid.tile(index, b["top"])
        return col + grid.tile_cols / 2, row + grid.tile_rows / 2

    def copy_label(self, index):
        grid = board.Grid(self.cols, self.rows)
        row, col = grid.copy_label(index, self.board()["top"])
        return col + len(board.COPY_LABEL) / 2, row + 0.5

    def _box(self):
        b = self.board()
        return b["editing"]["state"], board._box_layout(b, self.cols, self.rows, (*self.cell, True))

    def atom(self, find):
        """Where on the screen an atom is, chosen by `find(state)`, which returns the atom's id."""
        s, ui = self._box()
        col, row = ui.to_cell(s, *editor._atom(s, find(s))[2:4])
        return col + ui.origin[1], row + ui.origin[0]

    def button(self, name):
        _, ui = self._box()
        _, label, col = next(button for button in ui.buttons() if button[0] == name)
        return col + len(label) / 2 + ui.origin[1], ui.rows + 0.5 + ui.origin[0]


def _hydroxyl(s):
    """The O with one neighbour and a single bond, which the carbonyl's O doesn't have."""
    return next(a[0] for a in s["atoms"] if a[1] == "O" and next(b for b in s["bonds"] if a[0] in b[:2])[2] == editor.SINGLE)


def _ether_methyl(s):
    return next(a[0] for a in s["atoms"] if a[1] == "C" and len(editor._neighbours(s, a[0])) == 1 and editor._atom(s, editor._neighbours(s, a[0])[0])[1] == "O")


def _ortho_carbon(s):
    """A ring CH beside the carbon that holds the ether's O."""
    oxygen = next(a[0] for a in s["atoms"] if a[1] == "O" and len(editor._neighbours(s, a[0])) == 2)
    holder = next(n for n in editor._neighbours(s, oxygen) if len(editor._neighbours(s, n)) == 3)
    return next(n for n in editor._neighbours(s, holder) if len(editor._neighbours(s, n)) == 2 and editor._atom(s, n)[1] == "C")


def _new_carbon(s):
    """The carbon just grown from the ring: the end of a chain whose one neighbour has three, all of them carbons."""
    ends = [a[0] for a in s["atoms"] if a[1] == "C" and len(editor._neighbours(s, a[0])) == 1]
    return next(end for end in ends if _ring_neighbour(s, editor._neighbours(s, end)[0]))


def _ring_neighbour(s, ident):
    neighbours = editor._neighbours(s, ident)
    return len(neighbours) == 3 and all(editor._atom(s, n)[1] == "C" for n in neighbours)


def _edit(cols, rows, cell_width, cell_height):
    run = _Session(cols, rows, cell_width, cell_height)
    run.frames.append(_frame("A text interface with structures in it, the kind of list a real tool would show.", _HOME + run.output, pause=2600))
    run.hover(run.tile(0), "Hovering outlines a structure, and its SMILES, formula, MW and cLogP appear at the bottom.")
    run.key("c", "Pressing c copies the SMILES of the structure under the pointer. The terminal puts it on the system clipboard, shown below the screen here.", pause=3600)
    run.click(run.tile(0), "Aspirin is marked read-only, so clicking it only says so. Copying still works, since it changes nothing.")
    run.hover(run.tile(1), "Paracetamol is editable.", pause=1500)
    run.click(run.tile(1), "Clicking it opens the editor in a window over the list, with the same tools as the full-screen editor.", pause=2800)
    run.hover(run.atom(_hydroxyl), "Hover over an atom and it becomes the hotspot, as in ChemDraw. Clicks and keys now act on it.")
    run.click(run.atom(_hydroxyl), "Clicking the oxygen grows a bond from it.")
    run.hover(run.atom(_ether_methyl), "The new carbon is the hotspot now...", pause=1400)
    run.key("e", "...and pressing e makes it an ethyl group. That gives the ethyl ether, phenacetin.", pause=2800)
    run.hover(run.atom(_ortho_carbon), "Now the ring carbon next to the ether.")
    run.click(run.atom(_ortho_carbon), "Another click, another bond.")
    run.hover(run.atom(_new_carbon), "Hover over the new carbon...", pause=1400)
    run.key("f", "...and f turns it into fluorine. The status line keeps up with the SMILES.", pause=3000)
    run.hover(run.button("copy"), "Copy SMILES is on the window's bottom edge.", pause=1400)
    run.click(run.button("copy"), "It copies the drawing as it stands, before anything is saved.", pause=3200)
    run.hover(run.button("done"), "Done is on the same edge, beside Cancel.", pause=1500)
    run.click(run.button("done"), "The window closes, and the tile is redrawn and marked edited.", pause=3000)
    run.hover(run.tile(1), "The list now holds the new structure.", pause=2600)
    run.hover(run.copy_label(1), "The frame around the structure under the pointer has a copy label, for the mouse.", pause=1500)
    run.click(run.copy_label(1), "Clicking it copies the edited SMILES.", pause=3000)
    smiles = run.board()["items"][1][1]
    run.frames.append(_frame("Pasted anywhere that takes text: here, into a shell, to draw it again with the fog setting.", _pasted(smiles, cols), pause=4500))
    output = run._send("q")
    final = _HOME + editor._ansi(0, 0, f"{_BOLD}The list, as printed on quitting\x1b[0m") + "".join(editor._ansi(2 + index, 2, line) for index, line in enumerate(output.split("\n")))
    run.frames.append(_frame("Quitting hands the list back, as text another program can read.", final, pause=4000))
    return run.frames, output


def frames(cols, rows, cell_width, cell_height):
    """Everything the page shows, in order. Raises ShowcaseError if the window is too small."""
    if cols < MIN_COLS or rows < MIN_ROWS:
        raise ShowcaseError(f"The demo needs at least {MIN_COLS} columns and {MIN_ROWS} rows.")
    shown = [_frame("The same molecule both ways: real pixels from RDKit on the left, plain ASCII on the right.", _side_by_side(*COMPOUNDS[0], "ascii", cols, rows, cell_width, cell_height), pause=4500),
             _frame("Terminals with Kitty graphics get the image. The ASCII goes anywhere text goes: a log, an email, an old terminal.", _side_by_side(*COMPOUNDS[1], "ascii", cols, rows, cell_width, cell_height), pause=4500),
             _frame("ASCII is coarse, but rings, double bonds and labels still read at this size.", _side_by_side(*COMPOUNDS[2], "ascii", cols, rows, cell_width, cell_height), pause=4500),
             _frame("A larger drug, imatinib, is too much for ASCII. Braille packs eight dots into each character, and the image stays sharp.", _side_by_side(*COMPOUNDS[3], "braille", cols, rows, cell_width, cell_height), pause=4500),
             _frame("A renderer setting: fog diffuses the lines a little, as if the drawing were seen through fogged glass.", _settings(*COMPOUNDS[0], (THEME, 0), (THEME, 50), cols, rows), pause=5000),
             _frame("Fog goes from 0 to 100. Even at 100 the lines stay sharp enough to read.", _settings(*COMPOUNDS[2], (THEME, 25), (THEME, 100), cols, rows), pause=5000)]
    edit, _ = _edit(cols, rows, cell_width, cell_height)
    return shown + edit
