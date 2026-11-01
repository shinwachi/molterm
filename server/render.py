"""Render a parsed molecule as text: RDKit draws the bonds, real letters carry the atom labels."""
import math
from dataclasses import dataclass
from io import BytesIO

import numpy as np
from PIL import Image
from rdkit.Chem import GetPeriodicTable, Mol
from rdkit.Chem.Draw import rdMolDraw2D

import braille
import octant

DEFAULT_COLS = 80
DEFAULT_ROWS = 30
COLS_RANGE = (10, 240)
ROWS_RANGE = (5, 120)

MODES = ("braille", "octant")
DEFAULT_MODE = "braille"
_GLYPHS = {"braille": braille, "octant": octant}

SUPERSAMPLE = 4  # RDKit pixels per dot, per axis
INK_LEVEL = 128  # a pixel is ink when its darkest channel is below this, so pure colours count as well as black
DOT_COVERAGE = 0.3  # share of a dot's pixels that must be ink to set the dot
MAX_BOND_DOTS = 6  # as compact as stays readable: at 5, labels start to swallow short bonds
CRAMPED_BOND_DOTS = 6  # a canvas that forces bonds below this loses detail

SUPERSCRIPTS = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
SUBSCRIPTS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

# Terminal colours rather than fixed RGB, so they follow the terminal's theme, light or dark.
ANSI_COLORS = {"O": 31, "N": 34, "S": 33, "P": 35, "F": 32, "Cl": 32, "Br": 32, "I": 32}
ANSI_DEFAULT = 1  # bold
ANSI_HIGHLIGHT = 36  # cyan, which no element uses
# RDKit draws each element in a pure colour, which identifies the element again in the pixels. Black is plain carbon.
_DRAW_COLORS = {31: (1, 0, 0), 32: (0, 1, 0), 33: (1, 1, 0), 34: (0, 0, 1), 35: (1, 0, 1)}
_INK_CODES = [None, *_DRAW_COLORS]
_INK_RGB = np.array([(0, 0, 0), *_DRAW_COLORS.values()], dtype=float) * 255
_PALETTE = {-1: (0, 0, 0), **{GetPeriodicTable().GetAtomicNumber(s): _DRAW_COLORS[c] for s, c in ANSI_COLORS.items()}}


def ansi_text(grid, colors):
    """Join rows of characters and their ANSI codes into text with escape sequences."""
    lines = []
    for chars, codes in zip(grid, colors):
        line, current = "", None
        for char, code in zip(chars, codes):
            if code != current:
                line += "\x1b[0m" if current is not None else ""
                line += f"\x1b[{code}m" if code is not None else ""
                current = code
            line += char
        lines.append(line + ("\x1b[0m" if current is not None else ""))
    return "\n".join(lines)


@dataclass
class Rendering:
    grid: list  # rows of single characters
    colors: list  # rows of ANSI colour codes, or None, matching the grid
    labels: list  # {"row", "col", "text", "symbol"}, positions within the grid
    mode: str = DEFAULT_MODE
    bond_dots: float | None = None  # mean bond length in dots; None without bonds

    @property
    def text(self):
        return "\n".join("".join(row) for row in self.grid)

    @property
    def ansi(self):
        """The text with colours, for a real terminal."""
        return ansi_text(self.grid, self.colors)

    def to_dict(self):
        return {"mode": self.mode, "text": self.text, "ansi": self.ansi, "colors": self.colors, "labels": self.labels,
                "rows": len(self.grid), "cols": len(self.grid[0]), "bond_dots": self.bond_dots,
                "cramped": self.bond_dots is not None and self.bond_dots < CRAMPED_BOND_DOTS}


def _cell_colors(pixels, ink):
    """Return each cell's ANSI code: whichever element's ink covers most of the cell, or None for plain bonds."""
    dot_w, dot_h = braille.CELL_WIDTH * SUPERSAMPLE, braille.CELL_HEIGHT * SUPERSAMPLE
    rows, cols = pixels.shape[0] // dot_h, pixels.shape[1] // dot_w
    ys, xs = np.nonzero(ink)  # only ink: a large canvas is mostly empty, and classifying every pixel is costly
    nearest = ((pixels[ys, xs][:, None, :] - _INK_RGB) ** 2).sum(axis=2).argmin(axis=1)
    counts = np.zeros((len(_INK_CODES), rows, cols), dtype=int)
    np.add.at(counts, (nearest, ys // dot_h, xs // dot_w), 1)
    return [[_INK_CODES[i] for i in row] for row in counts.argmax(axis=0).tolist()]


def mean_bond_length(mol):
    """Return the mean bond length in coordinate units, for RDKit's fixedBondLength.

    Despite that option's name, RDKit reads it as pixels per coordinate unit, and molblocks differ in their units.
    """
    conformer = mol.GetConformer()
    return sum(
        (conformer.GetAtomPosition(b.GetBeginAtomIdx()) - conformer.GetAtomPosition(b.GetEndAtomIdx())).Length()
        for b in mol.GetBonds()
    ) / mol.GetNumBonds()


def _rasterize(mol, cols, rows):
    """Draw the bonds without labels; return the dot bitmap, cell colours and each atom's (col, row) in cells."""
    dot_w, dot_h = braille.CELL_WIDTH * SUPERSAMPLE, braille.CELL_HEIGHT * SUPERSAMPLE
    drawer = rdMolDraw2D.MolDraw2DCairo(cols * dot_w, rows * dot_h)
    options = drawer.drawOptions()
    options.noAtomLabels = True
    options.setAtomPalette(_PALETTE)
    options.scaleBondWidth = False
    options.bondLineWidth = SUPERSAMPLE
    mean_bond = mean_bond_length(mol)
    if mean_bond > 0:
        options.fixedBondLength = MAX_BOND_DOTS * SUPERSAMPLE / mean_bond
    options.padding = 0.08
    # RDKit's default preparation adds a hydrogen at stereocentres, which would draw as an unlabelled stub.
    # Wedges are off because at this size a hashed wedge's thin strokes fall below DOT_COVERAGE and the bond vanishes.
    options.prepareMolsBeforeDrawing = False
    drawn = rdMolDraw2D.PrepareMolForDrawing(mol, kekulize=True, addChiralHs=False, wedgeBonds=False)
    drawer.DrawMolecule(drawn)
    drawer.FinishDrawing()

    pixels = np.asarray(Image.open(BytesIO(drawer.GetDrawingText())).convert("RGB"), dtype=float)
    ink = pixels.min(axis=2) < INK_LEVEL
    height, width = ink.shape
    coverage = ink.reshape(height // SUPERSAMPLE, SUPERSAMPLE, width // SUPERSAMPLE, SUPERSAMPLE).mean(axis=(1, 3))
    positions = [drawer.GetDrawCoords(atom.GetIdx()) for atom in drawn.GetAtoms()]
    return coverage >= DOT_COVERAGE, _cell_colors(pixels, ink), [(p.x / dot_w, p.y / dot_h) for p in positions]


def _label_text(atom):
    """Return (text, index of the element's first character), or None for a plain skeletal carbon."""
    symbol = atom.GetSymbol()
    charge = atom.GetFormalCharge()
    isotope = atom.GetIsotope()
    if symbol == "C" and atom.GetDegree() and not charge and not isotope:
        return None

    hydrogens = atom.GetTotalNumHs()
    h_text = ("H" + (str(hydrogens) if hydrogens > 1 else "")).translate(SUBSCRIPTS) if hydrogens else ""
    charge_text = ""
    if charge:
        charge_text = (f"{abs(charge) if abs(charge) > 1 else ''}{'+' if charge > 0 else '-'}").translate(SUPERSCRIPTS)
    prefix = str(isotope).translate(SUPERSCRIPTS) if isotope else ""

    # hydrogens go on the side away from the bonds
    conformer = atom.GetOwningMol().GetConformer()
    x = conformer.GetAtomPosition(atom.GetIdx()).x
    spread = sum(conformer.GetAtomPosition(n.GetIdx()).x - x for n in atom.GetNeighbors())
    hydride_first = not atom.GetDegree() and symbol in ("O", "S", "Se", "F", "Cl", "Br", "I")  # H2O, not OH2
    if h_text and (spread > 0 or hydride_first):
        return f"{h_text}{prefix}{symbol}{charge_text}", len(h_text) + len(prefix)
    return f"{prefix}{symbol}{h_text}{charge_text}", len(prefix)


def _label_color(symbol):
    return ANSI_COLORS.get(symbol, ANSI_DEFAULT)


def _place_labels(grid, colors, mol, positions, highlight=frozenset()):
    labels = []
    rows, cols = len(grid), len(grid[0])
    for atom, (x, y) in zip(mol.GetAtoms(), positions):
        placed = _label_text(atom)
        if placed is None:
            continue
        text, anchor = placed
        col = min(max(int(x) - anchor, 0), max(cols - len(text), 0))
        row = min(max(int(y), 0), rows - 1)
        text = text[:cols - col]
        color = ANSI_HIGHLIGHT if atom.GetIdx() in highlight else _label_color(atom.GetSymbol())
        grid[row][col:col + len(text)] = list(text)
        colors[row][col:col + len(text)] = [color] * len(text)
        labels.append({"row": row, "col": col, "text": text, "symbol": atom.GetSymbol()})
    return labels


def _highlight_bonds(grid, colors, mol, positions, highlight, blank):
    """Colour the drawn cells along each bond whose two atoms are both highlighted, and a lone highlighted atom's cell."""
    rows, cols = len(grid), len(grid[0])

    def mark(x, y):
        row, col = int(y), int(x)
        if 0 <= row < rows and 0 <= col < cols and grid[row][col] != blank:
            colors[row][col] = ANSI_HIGHLIGHT

    for index in highlight:
        mark(*positions[index])
    for bond in mol.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if a in highlight and b in highlight:
            (x1, y1), (x2, y2) = positions[a], positions[b]
            steps = max(int(max(abs(x2 - x1), abs(y2 - y1)) * 4), 1)
            for step in range(steps + 1):
                mark(x1 + (x2 - x1) * step / steps, y1 + (y2 - y1) * step / steps)


def _crop(grid, colors, labels, blank):
    """Trim blank margins. A completely blank image keeps a single blank cell."""
    used_rows = [r for r, row in enumerate(grid) if any(c != blank for c in row)]
    used_cols = [c for c in range(len(grid[0])) if any(row[c] != blank for row in grid)]
    if not used_rows or not used_cols:
        return [[blank]], [[None]], []
    top, bottom, left, right = used_rows[0], used_rows[-1], used_cols[0], used_cols[-1]
    moved = [{**label, "row": label["row"] - top, "col": label["col"] - left} for label in labels]
    return ([row[left:right + 1] for row in grid[top:bottom + 1]],
            [row[left:right + 1] for row in colors[top:bottom + 1]], moved)


def _mean_bond_dots(mol, positions):
    lengths = []
    for bond in mol.GetBonds():
        (x1, y1), (x2, y2) = positions[bond.GetBeginAtomIdx()], positions[bond.GetEndAtomIdx()]
        lengths.append(math.hypot((x2 - x1) * braille.CELL_WIDTH, (y2 - y1) * braille.CELL_HEIGHT))
    return round(sum(lengths) / len(lengths), 1)


def _render_unbonded(mol, cols, rows, mode):
    """Atoms without bonds have nothing to draw and RDKit cannot scale them, so set their labels as a line of text."""
    blank = _GLYPHS[mode].BLANK_CHAR
    lines, tints, labels, col = [[]], [[]], [], 0
    for atom in mol.GetAtoms():
        text = _label_text(atom)[0]
        if col and col + len(text) > cols:
            if len(lines) == rows:
                break
            lines.append([])
            tints.append([])
            col = 0
        text = text[:cols]
        labels.append({"row": len(lines) - 1, "col": col, "text": text, "symbol": atom.GetSymbol()})
        lines[-1].extend(text + blank)
        tints[-1].extend([_label_color(atom.GetSymbol())] * len(text) + [None])
        col += len(text) + 1
    width = max(len(line) for line in lines) - 1
    return Rendering([(line + [blank] * width)[:width] for line in lines],
                     [(tint + [None] * width)[:width] for tint in tints], labels, mode)


def render(mol: Mol, cols=DEFAULT_COLS, rows=DEFAULT_ROWS, mode=DEFAULT_MODE, highlight=frozenset()):
    """Render a molecule with a 2D conformer into at most cols x rows characters; `highlight` holds atom indices to mark."""
    if not mol.GetNumBonds():
        return _render_unbonded(mol, cols, rows, mode)
    glyphs = _GLYPHS[mode]
    bitmap, colors, positions = _rasterize(mol, cols, rows)
    grid = glyphs.bitmap_to_grid(bitmap)
    colors = [[code if char != glyphs.BLANK_CHAR else None for char, code in zip(g, c)] for g, c in zip(grid, colors)]
    _highlight_bonds(grid, colors, mol, positions, highlight, glyphs.BLANK_CHAR)
    labels = _place_labels(grid, colors, mol, positions, highlight)
    return Rendering(*_crop(grid, colors, labels, glyphs.BLANK_CHAR), mode, _mean_bond_dots(mol, positions))
