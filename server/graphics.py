"""Draw a molecule with real pixels for terminals with a graphics protocol: Kitty, iTerm2 or Sixel.

The server cannot ask a terminal what it supports, so `server/client/molterm.sh` does that and chooses a mode.

Kitty and iTerm2 scale a PNG to a width in columns. Given only the width, the terminal works out the height itself, and
the drawing keeps its shape in any font. Sixel has no scaling: its pixels are the screen's, so it is drawn to fit the cell
size the client reports.
"""
import base64
import math
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rdkit.Chem import GetPeriodicTable, Mol
from rdkit.Chem.Draw import rdMolDraw2D

import braille
import render as renderer

KITTY, ITERM, SIXEL = "kitty", "iterm", "sixel"
MODES = (KITTY, ITERM, SIXEL)
THEMES = ("dark", "light", "mono")
DEFAULT_THEME = "dark"
FOG_RANGE = (0, 100)

CELL_PIXELS = 20  # image pixels per column for a PNG, which stays sharp on a high-density screen with cells about 20 px wide
SIXEL_CELL_PIXELS = 10  # a typical cell's real width, when the client could not find out
CELL_PIXELS_RANGE = (4, 40)
CELL_ASPECT = 2  # a cell is taken as twice as tall as wide, as in the text modes, to fit the canvas
BOND_CELLS = renderer.MAX_BOND_DOTS / braille.CELL_WIDTH  # the same size as the text modes
MARGIN = 2  # pixels kept around the ink, so antialiased edges are not cut
CHUNK_SIZE = 4096  # the Kitty protocol's limit for one escape sequence's payload

SIXEL_COLORS = 64  # plenty for bonds, six element colours and their antialiased edges, and most terminals allow 256
SIXEL_MIN_ALPHA = 24  # fainter pixels are left unpainted, so the terminal's own background shows through them
SIXEL_FOG_MIN_ALPHA = 3  # but fog is all faint pixels, and cutting them off would give it an edge
_ASSUMED_BACKGROUND = {"dark": (0, 0, 0), "light": (255, 255, 255), "mono": (255, 255, 255)}

# The image is transparent, so its ink must suit the terminal's background. The server cannot detect that.
_INK = {"dark": "#eeeeee", "light": "#1a1a1a", "mono": "#000000"}
_ELEMENTS = {  # by ANSI code, like the text modes, with the same hues; the dark ones match the page's
    "dark": {31: "#ff8a80", 32: "#7ddc7d", 33: "#f0d060", 34: "#86b6ef", 35: "#e0a0f0"},
    "light": {31: "#c62828", 32: "#2e7d32", 33: "#8a6d00", 34: "#1565c0", 35: "#8e24aa"},
    "mono": dict.fromkeys((31, 32, 33, 34, 35), "#000000"),  # black on white, like a printed page
}


HIGHLIGHT = "#5fe3e3"


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def palette(theme):
    palette = {-1: _rgb(_INK[theme])}
    for symbol, code in renderer.ANSI_COLORS.items():
        palette[GetPeriodicTable().GetAtomicNumber(symbol)] = _rgb(_ELEMENTS[theme][code])
    return palette


def draw_image(mol: Mol, cols, rows, theme=DEFAULT_THEME, cell_pixels=CELL_PIXELS, highlight=frozenset()):
    """Draw a molecule with bonds on a transparent background; return (RGBA image, width in columns).

    `highlight` holds atom indices, marked with the same cyan as the text modes, translucent so that the bonds still show.
    """
    drawer = rdMolDraw2D.MolDraw2DCairo(cols * cell_pixels, rows * CELL_ASPECT * cell_pixels)
    options = drawer.drawOptions()
    options.clearBackground = False
    options.setBackgroundColour((0, 0, 0, 0))
    options.setAtomPalette(palette(theme))
    mean_bond = renderer.mean_bond_length(mol)
    if mean_bond > 0:
        options.fixedBondLength = BOND_CELLS * cell_pixels / mean_bond
    options.padding = 0.02
    # Unlike the text modes, wedges survive at this resolution. Chiral hydrogens would still draw as unlabelled stubs.
    options.prepareMolsBeforeDrawing = False
    options.setHighlightColour((*_rgb(HIGHLIGHT), 0.45))
    bonds = [b.GetIdx() for b in mol.GetBonds() if b.GetBeginAtomIdx() in highlight and b.GetEndAtomIdx() in highlight]
    prepared = rdMolDraw2D.PrepareMolForDrawing(mol, kekulize=True, addChiralHs=False, wedgeBonds=True)
    drawer.DrawMolecule(prepared, highlightAtoms=sorted(highlight), highlightBonds=bonds)
    drawer.FinishDrawing()
    return _crop(Image.open(BytesIO(drawer.GetDrawingText())).convert("RGBA"), cell_pixels)


def _crop(image, cell_pixels):
    """Trim to the ink, widened to whole columns so that the terminal scales it evenly."""
    ys, xs = np.nonzero(np.asarray(image)[:, :, 3])
    if not len(xs):
        left, top, right, bottom = 0, 0, 1, 1
    else:
        left, top = max(int(xs.min()) - MARGIN, 0), max(int(ys.min()) - MARGIN, 0)
        right, bottom = min(int(xs.max()) + 1 + MARGIN, image.width), min(int(ys.max()) + 1 + MARGIN, image.height)
    cols = math.ceil((right - left) / cell_pixels)
    padded = Image.new("RGBA", (cols * cell_pixels, bottom - top), (0, 0, 0, 0))
    padded.paste(image.crop((left, top, right, bottom)), (0, 0))
    return padded, cols


def _gaussian(layers, sigma):
    """Blur an array of height x width x channels. The image's margin keeps what wraps around at its edges too faint to matter."""
    height, width = layers.shape[:2]
    across, down = np.fft.rfftfreq(width)[None, :], np.fft.fftfreq(height)[:, None]
    kernel = np.exp(-2 * (np.pi * sigma) ** 2 * (across ** 2 + down ** 2))[:, :, None]
    return np.fft.irfft2(np.fft.rfft2(layers, axes=(0, 1)) * kernel, s=(height, width), axes=(0, 1))


def _peak(layer, level):
    """Scale a blurred layer so that its strongest pixel has this opacity: blurring a thin line would otherwise leave almost nothing."""
    return layer * (level / max(layer[:, :, 3].max(), 1e-6))


def _over(top, below):
    return top + below * (1 - top[:, :, 3:])


def _mottle(height, width, cell_pixels):
    """Slow, uneven patches from 0 to 1, as condensation has. Always the same, so that a drawing always comes out the same."""
    noise = np.random.default_rng(7).standard_normal((height, width, 1))
    smooth = _gaussian(noise, 2.5 * cell_pixels)
    return (smooth - smooth.min()) / max(float(smooth.max() - smooth.min()), 1e-6)


# Fog's strength at 100: how far each layer spreads, in columns, and how dark its darkest pixel is. Kept faint, so the drawing stays crisp.
_LINE_SPREAD, _GLOW_SPREAD, _HAZE_SPREAD = (0.02, 0.04), (0.12, 0.2), (0.4, 0.6)  # (at 0, added by 100)
_GLOW_LEVEL, _HAZE_LEVEL = (0.1, 0.25), (0.04, 0.12)


def _fog_margin(fog):
    """Columns kept clear on each side for what spreads from the lines."""
    return math.ceil(2.5 * (_HAZE_SPREAD[0] + _HAZE_SPREAD[1] * fog / 100))


def _scaled(pair, amount):
    return pair[0] + pair[1] * amount


def _diffuse(image, fog, cell_pixels):
    """Spread the ink a little, as through fogged glass: slightly softened lines, a close glow and a wide, faintly patchy haze."""
    margin = _fog_margin(fog) * cell_pixels
    canvas = Image.new("RGBA", (image.width + 2 * margin, image.height + 2 * margin), (0, 0, 0, 0))
    canvas.paste(image, (margin, margin))
    pixels = np.asarray(canvas, dtype=float) / 255
    ink = np.concatenate((pixels[:, :, :3] * pixels[:, :, 3:], pixels[:, :, 3:]), axis=2)  # colours scaled by opacity, so that they blur correctly
    amount = fog / 100
    patches = _mottle(*ink.shape[:2], cell_pixels)
    lines = np.clip(_peak(_gaussian(ink, _scaled(_LINE_SPREAD, amount) * cell_pixels), 1), 0, 1)
    glow = _peak(_gaussian(ink, _scaled(_GLOW_SPREAD, amount) * cell_pixels), _scaled(_GLOW_LEVEL, amount))
    haze = _peak(_gaussian(ink, _scaled(_HAZE_SPREAD, amount) * cell_pixels), _scaled(_HAZE_LEVEL, amount)) * (0.75 + 0.5 * patches)
    ink = np.clip(_over(lines, _over(glow, haze)), 0, 1)
    opacity = ink[:, :, 3:]
    # where the haze all but vanishes, rounding in the blur can leave colour without opacity, so only divide where there's some, and clamp
    colours = np.clip(np.divide(ink[:, :, :3], opacity, out=np.zeros_like(ink[:, :, :3]), where=opacity > 1e-4), 0, 1)
    return Image.fromarray((np.concatenate((colours, opacity), axis=2) * 255).round().astype(np.uint8), "RGBA")


def styled_image(mol: Mol, cols, rows, theme=DEFAULT_THEME, cell_pixels=CELL_PIXELS, fog=0):
    """As draw_image, with `fog` from 0, none, to 100, still within cols and rows. The image stays transparent, in the theme's ink."""
    if not fog:
        return draw_image(mol, cols, rows, theme, cell_pixels)
    margin = _fog_margin(fog)
    image, _ = draw_image(mol, max(cols - 2 * margin, 8), max(rows - math.ceil(margin / CELL_ASPECT) * 2, 4), theme, cell_pixels)
    styled = _diffuse(image, fog, cell_pixels)
    return styled, styled.width // cell_pixels


def _png(image):
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def kitty_sequence(png, cols, rows=None, image_id=None, move_cursor=True, z=None):
    """Wrap a PNG in Kitty escape sequences that place it at the cursor, then move the cursor to the next line.

    The editor gives `rows` as well, to fill an exact area, an `image_id` so it can replace the image, a `z` for stacking, and keeps
    the cursor still.
    """
    payload = base64.b64encode(png).decode("ascii")
    chunks = [payload[i:i + CHUNK_SIZE] for i in range(0, len(payload), CHUNK_SIZE)]
    sequences = []
    for index, chunk in enumerate(chunks):
        # q=2 stops the terminal typing its replies into the shell, since curl will not read them
        keys = "q=2,"
        if index == 0:
            extra = (f"r={rows}," if rows else "") + (f"i={image_id}," if image_id else "") + ("" if move_cursor else "C=1,")
            extra += f"z={z}," if z is not None else ""
            keys = f"a=T,f=100,c={cols},{extra}q=2,"
        more = int(index < len(chunks) - 1)
        sequences.append(f"\x1b_G{keys}m={more};{chunk}\x1b\\")
    return "".join(sequences) + "\n"


def iterm_sequence(png, cols):
    """Wrap a PNG in the iTerm2 inline image sequence, also understood by WezTerm and mintty."""
    payload = base64.b64encode(png).decode("ascii")
    return f"\x1b]1337;File=inline=1;size={len(png)};width={cols};preserveAspectRatio=1:{payload}\x07\n"


def _sixel_row(values):
    """Encode one colour's sixels for one band, as characters with repeats for runs."""
    last = np.flatnonzero(values)[-1] + 1  # a band needs no trailing blanks
    values = values[:last]
    edges = np.flatnonzero(np.diff(values)) + 1
    starts, ends = np.concatenate(([0], edges)), np.concatenate((edges, [len(values)]))
    parts = []
    for start, end in zip(starts.tolist(), ends.tolist()):
        char, count = chr(63 + int(values[start])), end - start
        parts.append(f"!{count}{char}" if count > 3 else char * count)
    return "".join(parts)


def sixel_sequence(image, background=(0, 0, 0), min_alpha=SIXEL_MIN_ALPHA):
    """Encode an RGBA image as Sixel. `background` is what antialiased edges blend into; empty pixels stay unpainted."""
    pixels = np.asarray(image, dtype=float)
    alpha = pixels[:, :, 3:] / 255
    blended = (pixels[:, :, :3] * alpha + np.array(background, dtype=float) * (1 - alpha)).round().astype(np.uint8)
    quantized = Image.fromarray(blended, "RGB").quantize(SIXEL_COLORS, method=Image.Quantize.MEDIANCUT,
                                                         dither=Image.Dither.NONE)
    indices = np.asarray(quantized, dtype=int).copy()
    indices[pixels[:, :, 3] < min_alpha] = -1
    palette = quantized.getpalette()
    height, width = indices.shape

    def percentages(color):
        return ";".join(str(round(c * 100 / 255)) for c in color)

    # P2=1 leaves pixels nobody paints showing the terminal's background. tmux instead fills them with register 0, so that
    # register holds the background and ink starts at 1.
    out = [f'\x1bP0;1;0q"1;1;{width};{height}#0;2;{percentages(background)}']
    for index in np.unique(indices[indices >= 0]).tolist():
        out.append(f"#{index + 1};2;{percentages(palette[index * 3:index * 3 + 3])}")
    weights = (1 << np.arange(6)).reshape(6, 1)
    bands = []
    for top in range(0, height, 6):
        band = indices[top:top + 6]
        layers = []
        for index in np.unique(band[band >= 0]).tolist():
            values = ((band == index) * weights[:band.shape[0]]).sum(axis=0)
            layers.append(f"#{index + 1}{_sixel_row(values)}")
        bands.append("$".join(layers))
    return "".join(out) + "-".join(bands) + "\x1b\\\n"


def gallery(items, cols, cell_pixels, theme=DEFAULT_THEME):
    """Set drawings side by side in one image, wrapping at cols; return (RGBA image, width in columns).

    Each item is (mol, highlight, caption lines), and a caption line is (text, "#rrggbb"). One image rather than several,
    because graphics protocols can't place images beside each other without moving the cursor by hand.
    """
    font = ImageFont.load_default(size=max(cell_pixels * 1.6, 10))  # about the size of the terminal's own text
    line_height, gap = cell_pixels * 2, cell_pixels * 3
    tiles = []
    for mol, highlight, captions in items:
        image, _ = draw_image(mol, renderer.DEFAULT_COLS, renderer.DEFAULT_ROWS, theme, cell_pixels, frozenset(highlight))
        widest = max((round(font.getlength(text)) for text, _ in captions), default=0)
        tile = Image.new("RGBA", (max(image.width, widest), image.height + line_height * len(captions) + cell_pixels), (0, 0, 0, 0))
        tile.paste(image, (0, 0))
        pen = ImageDraw.Draw(tile)
        for index, (text, color) in enumerate(captions):
            pen.text((0, image.height + cell_pixels // 2 + index * line_height), text, font=font, fill=color)
        tiles.append(tile)

    limit = cols * cell_pixels
    shelves, shelf, used = [], [], 0
    for tile in tiles:
        if shelf and used + gap + tile.width > limit:
            shelves.append(shelf)
            shelf, used = [], 0
        used += (gap if shelf else 0) + tile.width
        shelf.append(tile)
    shelves.append(shelf)

    width = max(sum(t.width for t in row) + gap * (len(row) - 1) for row in shelves)
    height = sum(max(t.height for t in row) for row in shelves) + cell_pixels * (len(shelves) - 1)
    used_cols = math.ceil(width / cell_pixels)
    sheet = Image.new("RGBA", (used_cols * cell_pixels, height), (0, 0, 0, 0))
    top = 0
    for row in shelves:
        left = 0
        for tile in row:
            sheet.paste(tile, (left, top))
            left += tile.width + gap
        top += max(t.height for t in row) + cell_pixels
    return sheet, used_cols


def sequence(image, cols, mode, background=None, theme=DEFAULT_THEME):
    """Wrap an image in the escape sequence of a mode, without a line ending."""
    if mode == SIXEL:
        text = sixel_sequence(image, background or _ASSUMED_BACKGROUND[theme])
    else:
        text = (iterm_sequence if mode == ITERM else kitty_sequence)(_png(image), cols)
    return text.rstrip("\n")


def render(mol: Mol, mode=KITTY, cols=renderer.DEFAULT_COLS, rows=renderer.DEFAULT_ROWS, theme=DEFAULT_THEME,
           cell_pixels=None, background=None, fog=0):
    """Return what to print in the terminal. Atoms without bonds gain nothing from pixels, so they stay as text."""
    if not mol.GetNumBonds():
        return renderer.render(mol, cols, rows).ansi + "\n"
    if mode == SIXEL:
        image, _ = styled_image(mol, cols, rows, theme, cell_pixels or SIXEL_CELL_PIXELS, fog)
        return sixel_sequence(image, background or _ASSUMED_BACKGROUND[theme], SIXEL_FOG_MIN_ALPHA if fog else SIXEL_MIN_ALPHA)
    image, used = styled_image(mol, cols, rows, theme, CELL_PIXELS, fog)
    return (iterm_sequence if mode == ITERM else kitty_sequence)(_png(image), used)
