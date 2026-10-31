"""Render a parsed molecule as Braille text: RDKit draws the bonds, real letters carry the atom labels."""
import math
from dataclasses import dataclass
from io import BytesIO

import numpy as np
from PIL import Image
from rdkit.Chem import Mol
from rdkit.Chem.Draw import rdMolDraw2D

import braille

DEFAULT_COLS = 80
DEFAULT_ROWS = 30
COLS_RANGE = (10, 240)
ROWS_RANGE = (5, 120)

SUPERSAMPLE = 4  # RDKit pixels per Braille dot, per axis
INK_LEVEL = 128  # grey level below which an RDKit pixel counts as ink
DOT_COVERAGE = 0.3  # share of a dot's pixels that must be ink to set the dot
MAX_BOND_DOTS = 42  # keeps small molecules from filling a large canvas
CRAMPED_BOND_DOTS = 6  # below this, double bonds and zigzags stop being readable

SUPERSCRIPTS = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
SUBSCRIPTS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
ANSI_COLORS = {"O": 31, "N": 34, "S": 33, "P": 35, "F": 32, "Cl": 32, "Br": 32, "I": 32}
ANSI_DEFAULT = 1  # bold


@dataclass
class Rendering:
    grid: list  # rows of single characters
    labels: list  # {"row", "col", "text", "symbol"}, positions within the grid
    bond_dots: float | None = None  # mean bond length in dots; None without bonds

    @property
    def text(self):
        return "\n".join("".join(row) for row in self.grid)

    @property
    def ansi(self):
        """The text with atom labels coloured, for a real terminal."""
        rows = [list(row) for row in self.grid]
        for label in self.labels:
            code = ANSI_COLORS.get(label["symbol"], ANSI_DEFAULT)
            row, col, end = label["row"], label["col"], label["col"] + len(label["text"]) - 1
            rows[row][col] = f"\x1b[{code}m{rows[row][col]}"
            rows[row][end] = f"{rows[row][end]}\x1b[0m"
        return "\n".join("".join(row) for row in rows)

    def to_dict(self):
        return {"text": self.text, "ansi": self.ansi, "labels": self.labels, "rows": len(self.grid),
                "cols": len(self.grid[0]), "bond_dots": self.bond_dots,
                "cramped": self.bond_dots is not None and self.bond_dots < CRAMPED_BOND_DOTS}


def _rasterize(mol, cols, rows):
    """Draw the bonds without labels; return the dot bitmap and each atom's (col, row) as fractional cells."""
    dot_w, dot_h = braille.CELL_WIDTH * SUPERSAMPLE, braille.CELL_HEIGHT * SUPERSAMPLE
    drawer = rdMolDraw2D.MolDraw2DCairo(cols * dot_w, rows * dot_h)
    options = drawer.drawOptions()
    options.noAtomLabels = True
    options.useBWAtomPalette()
    options.scaleBondWidth = False
    options.bondLineWidth = SUPERSAMPLE
    # despite the name, RDKit reads this as pixels per coordinate unit, and molblocks differ in their units
    conformer = mol.GetConformer()
    mean_bond = sum(
        (conformer.GetAtomPosition(b.GetBeginAtomIdx()) - conformer.GetAtomPosition(b.GetEndAtomIdx())).Length()
        for b in mol.GetBonds()
    ) / mol.GetNumBonds()
    if mean_bond > 0:
        options.fixedBondLength = MAX_BOND_DOTS * SUPERSAMPLE / mean_bond
    options.padding = 0.08
    # RDKit's default preparation adds a hydrogen at stereocentres, which would draw as an unlabelled stub
    options.prepareMolsBeforeDrawing = False
    drawn = rdMolDraw2D.PrepareMolForDrawing(mol, kekulize=True, addChiralHs=False, wedgeBonds=True)
    drawer.DrawMolecule(drawn)
    drawer.FinishDrawing()

    ink = np.asarray(Image.open(BytesIO(drawer.GetDrawingText())).convert("L")) < INK_LEVEL
    height, width = ink.shape
    coverage = ink.reshape(height // SUPERSAMPLE, SUPERSAMPLE, width // SUPERSAMPLE, SUPERSAMPLE).mean(axis=(1, 3))
    positions = [drawer.GetDrawCoords(atom.GetIdx()) for atom in drawn.GetAtoms()]
    return coverage >= DOT_COVERAGE, [(p.x / dot_w, p.y / dot_h) for p in positions]


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


def _place_labels(grid, mol, positions):
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
        grid[row][col:col + len(text)] = list(text)
        labels.append({"row": row, "col": col, "text": text, "symbol": atom.GetSymbol()})
    return labels


def _crop(grid, labels):
    """Trim blank margins. A completely blank image keeps a single blank cell."""
    blank = chr(braille.BLANK)
    used_rows = [r for r, row in enumerate(grid) if any(c != blank for c in row)]
    used_cols = [c for c in range(len(grid[0])) if any(row[c] != blank for row in grid)]
    if not used_rows or not used_cols:
        return [[blank]], []
    top, bottom, left, right = used_rows[0], used_rows[-1], used_cols[0], used_cols[-1]
    cropped = [row[left:right + 1] for row in grid[top:bottom + 1]]
    moved = [{**label, "row": label["row"] - top, "col": label["col"] - left} for label in labels]
    return cropped, moved


def _mean_bond_dots(mol, positions):
    lengths = []
    for bond in mol.GetBonds():
        (x1, y1), (x2, y2) = positions[bond.GetBeginAtomIdx()], positions[bond.GetEndAtomIdx()]
        lengths.append(math.hypot((x2 - x1) * braille.CELL_WIDTH, (y2 - y1) * braille.CELL_HEIGHT))
    return round(sum(lengths) / len(lengths), 1)


def _render_unbonded(mol, cols, rows):
    """Atoms without bonds have nothing to draw and RDKit cannot scale them, so set their labels as a line of text."""
    blank = chr(braille.BLANK)
    lines, labels, col = [[]], [], 0
    for atom in mol.GetAtoms():
        text = _label_text(atom)[0]
        if col and col + len(text) > cols:
            if len(lines) == rows:
                break
            lines.append([])
            col = 0
        text = text[:cols]
        labels.append({"row": len(lines) - 1, "col": col, "text": text, "symbol": atom.GetSymbol()})
        lines[-1].extend(text + blank)
        col += len(text) + 1
    width = max(len(line) for line in lines) - 1
    return Rendering([(line + [blank] * width)[:width] for line in lines], labels)


def render(mol: Mol, cols=DEFAULT_COLS, rows=DEFAULT_ROWS):
    """Render a molecule with a 2D conformer into at most cols x rows characters."""
    if not mol.GetNumBonds():
        return _render_unbonded(mol, cols, rows)
    bitmap, positions = _rasterize(mol, cols, rows)
    grid = braille.bitmap_to_grid(bitmap)
    labels = _place_labels(grid, mol, positions)
    return Rendering(*_crop(grid, labels), bond_dots=_mean_bond_dots(mol, positions))
