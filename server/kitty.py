"""Draw a molecule with real pixels for terminals that speak the Kitty graphics protocol, such as Kitty and Ghostty.

The server cannot know the terminal's cell size, so it sends a sharp image and asks for a width in columns. Only the
width is given: the terminal then works out the height itself, and the drawing keeps its shape in any font.
"""
import base64
import math
from io import BytesIO

import numpy as np
from PIL import Image
from rdkit.Chem import GetPeriodicTable, Mol
from rdkit.Chem.Draw import rdMolDraw2D

import braille
import render as renderer

MODE = "kitty"
THEMES = ("dark", "light")
DEFAULT_THEME = "dark"

CELL_PIXELS = 20  # image pixels per column, which stays sharp on a high-density screen with cells about 20 px wide
CELL_ASPECT = 2  # a cell is taken as twice as tall as wide, as in the text modes, to fit the canvas
BOND_CELLS = renderer.MAX_BOND_DOTS / braille.CELL_WIDTH  # the same size as the text modes
MARGIN = 2  # pixels kept around the ink, so antialiased edges are not cut
CHUNK_SIZE = 4096  # the protocol's limit for one escape sequence's payload

# The image is transparent, so its ink must suit the terminal's background. The server cannot detect that.
_INK = {"dark": "#eeeeee", "light": "#1a1a1a"}
_ELEMENTS = {  # by ANSI code, like the text modes, with the same hues; the dark ones match the page's
    "dark": {31: "#ff8a80", 32: "#7ddc7d", 33: "#f0d060", 34: "#86b6ef", 35: "#e0a0f0"},
    "light": {31: "#c62828", 32: "#2e7d32", 33: "#8a6d00", 34: "#1565c0", 35: "#8e24aa"},
}


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _palette(theme):
    palette = {-1: _rgb(_INK[theme])}
    for symbol, code in renderer.ANSI_COLORS.items():
        palette[GetPeriodicTable().GetAtomicNumber(symbol)] = _rgb(_ELEMENTS[theme][code])
    return palette


def draw_png(mol: Mol, cols, rows, theme=DEFAULT_THEME):
    """Draw a molecule with bonds on a transparent background; return (PNG bytes, width in columns)."""
    drawer = rdMolDraw2D.MolDraw2DCairo(cols * CELL_PIXELS, rows * CELL_ASPECT * CELL_PIXELS)
    options = drawer.drawOptions()
    options.clearBackground = False
    options.setBackgroundColour((0, 0, 0, 0))
    options.setAtomPalette(_palette(theme))
    mean_bond = renderer.mean_bond_length(mol)
    if mean_bond > 0:
        options.fixedBondLength = BOND_CELLS * CELL_PIXELS / mean_bond
    options.padding = 0.02
    # Unlike the text modes, wedges survive at this resolution. Chiral hydrogens would still draw as unlabelled stubs.
    options.prepareMolsBeforeDrawing = False
    drawer.DrawMolecule(rdMolDraw2D.PrepareMolForDrawing(mol, kekulize=True, addChiralHs=False, wedgeBonds=True))
    drawer.FinishDrawing()
    return _crop(Image.open(BytesIO(drawer.GetDrawingText())).convert("RGBA"))


def _crop(image):
    """Trim to the ink, widened to whole columns so that the terminal scales it evenly."""
    ys, xs = np.nonzero(np.asarray(image)[:, :, 3])
    if not len(xs):
        left, top, right, bottom = 0, 0, 1, 1
    else:
        left, top = max(int(xs.min()) - MARGIN, 0), max(int(ys.min()) - MARGIN, 0)
        right, bottom = min(int(xs.max()) + 1 + MARGIN, image.width), min(int(ys.max()) + 1 + MARGIN, image.height)
    cols = math.ceil((right - left) / CELL_PIXELS)
    padded = Image.new("RGBA", (cols * CELL_PIXELS, bottom - top), (0, 0, 0, 0))
    padded.paste(image.crop((left, top, right, bottom)), (0, 0))
    buffer = BytesIO()
    padded.save(buffer, "PNG")
    return buffer.getvalue(), cols


def encode(png, cols):
    """Wrap a PNG in escape sequences that place it at the cursor, then move the cursor to the next line."""
    payload = base64.b64encode(png).decode("ascii")
    chunks = [payload[i:i + CHUNK_SIZE] for i in range(0, len(payload), CHUNK_SIZE)]
    sequences = []
    for index, chunk in enumerate(chunks):
        # q=2 stops the terminal typing its replies into the shell, since curl will not read them
        keys = f"a=T,f=100,c={cols},q=2," if index == 0 else "q=2,"
        more = int(index < len(chunks) - 1)
        sequences.append(f"\x1b_G{keys}m={more};{chunk}\x1b\\")
    return "".join(sequences) + "\n"


def render(mol: Mol, cols=renderer.DEFAULT_COLS, rows=renderer.DEFAULT_ROWS, theme=DEFAULT_THEME):
    """Return what to print in the terminal. Atoms without bonds gain nothing from pixels, so they stay as text."""
    if not mol.GetNumBonds():
        return renderer.render(mol, cols, rows).ansi + "\n"
    return encode(*draw_png(mol, cols, rows, theme))
