"""A molecule's picture with its annotations, for a host that draws notes itself, as speech bubbles beside the picture.

The highlights are drawn in the picture, as RDKit draws them. Each note is not: instead a leader runs from its atom (or the middle of
its bond) to the picture's right edge, ending in the middle of a row of cells, and the host carries it on in text to a bubble with
the note in it. The leaders' rows are kept apart, so that the bubbles have room; the host learns them with the picture.

Annotations travel in the CXSMILES, as everywhere in molterm (see annotate.py), and a `mark` adds some: atoms by index, or those a
SMARTS matches, highlighted and/or with a note.
"""
import base64
import math
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw
from rdkit import Chem, rdBase
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

import annotate
import graphics
import render as renderer

LEADER = (229, 192, 123)  # the notes' colour, which the host's bubbles share: an agent's
USER_LEADER = (140, 180, 245)  # and the person's own, the blue of what they highlight
BUBBLE_ROWS = 3  # a bubble's height at the least: its border and a line of text
MAX_MARKED = 200
MAX_INPUT = 20_000


class CalloutError(ValueError):
    pass


def _read(text):
    """(mol with atoms in the order written, the SMILES without its notes, the notes)."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT:
        raise CalloutError("Send the molecule as SMILES in \"input\".")
    try:
        kept, notes = annotate.split(text)
    except ValueError as error:
        raise CalloutError(str(error)) from error
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(kept)
    if mol is None:
        raise CalloutError(f"Not valid SMILES: {text[:60]}")
    return mol, kept, notes


def _matched(mol, mark):
    """The atoms a mark is for: its `atoms`, or those its `smarts` matches (every match)."""
    if mark.get("smarts"):
        with rdBase.BlockLogs():
            query = Chem.MolFromSmarts(str(mark["smarts"]))
        if query is None:
            raise CalloutError("That SMARTS can't be read.")
        found = []
        for match in mol.GetSubstructMatches(query, maxMatches=50):
            found += [i for i in match if i not in found]
        return found[:MAX_MARKED]
    atoms = mark.get("atoms") or []
    if not isinstance(atoms, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in atoms):
        raise CalloutError("atoms must be a list of atom indices.")
    wrong = [i for i in atoms if not 0 <= i < mol.GetNumAtoms()]
    if wrong:
        raise CalloutError(f"There is no atom {wrong[0]}: this molecule's atoms are 0 to {mol.GetNumAtoms() - 1}.")
    return list(dict.fromkeys(atoms))[:MAX_MARKED]


def add(mol, notes, mark):
    """Add a mark ({atoms or smarts, highlight, note}) to the notes: the atoms it is for, and the bonds between them, get its highlight,
    and the first atom its note. Returns the atoms."""
    highlight, note = str(mark.get("highlight") or ""), annotate.clean_note(str(mark.get("note") or ""))
    if highlight and highlight not in annotate.HIGHLIGHTS:
        raise CalloutError(f"highlight must be one of {', '.join(annotate.HIGHLIGHTS)}.")
    atoms = _matched(mol, mark)
    if not atoms:
        return atoms
    entries = {entry[0]: entry for entry in notes["atoms"]}
    for index in atoms:
        entry = entries.setdefault(index, [index, "", ""])
        if highlight:
            entry[1] = highlight
    if note:
        entries[atoms[0]][2] = note  # an agent's, about the whole of what it marked if it highlighted more than an atom
        annotate.set_flags(notes, [atoms[0]], {"group"} if highlight and len(atoms) > 1 else set())
    notes["atoms"] = sorted(entry for entry in entries.values() if entry[1] or entry[2])
    if highlight:
        bonds = {(b[0], b[1]): b for b in notes["bonds"]}
        chosen = set(atoms)
        for bond in mol.GetBonds():
            pair = tuple(sorted((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())))
            if pair[0] in chosen and pair[1] in chosen:
                bonds.setdefault(pair, [*pair, "", ""])[2] = highlight
        notes["bonds"] = sorted(bonds.values())
    return atoms


def _rows(wanted, rows):
    """Rows for leaders that want these rows (in order of the notes), kept BUBBLE_ROWS apart if they can be, within 0 to rows - 1."""
    if not wanted:
        return []
    gap = min(BUBBLE_ROWS, max(rows // len(wanted), 1))
    order = sorted(range(len(wanted)), key=lambda i: wanted[i])
    placed = []
    for i in order:  # down from the top, each at least a gap below the last
        placed.append(max(min(wanted[i], rows - 1), placed[-1] + gap if placed else 0))
    placed[-1] = min(placed[-1], rows - 1)
    for k in range(len(placed) - 2, -1, -1):  # and back up from the bottom, if that pushed any past it
        placed[k] = min(placed[k], placed[k + 1] - gap)
    result = [0] * len(wanted)
    for i, row in zip(order, placed):
        result[i] = max(min(row, rows - 1), 0)
    return result


def picture(text, cols, rows, theme=graphics.DEFAULT_THEME, cell_pixels=graphics.CELL_PIXELS, mark=None, clear=False):
    """The picture and what a host needs to put bubbles beside it: {png (base64), cols, rows, smiles (the CXSMILES with the
    annotations), notes: [{atom, bond, text, highlight, row, by, group}], marked}. `row` is the row of cells the note's leader ends
    on, at the right edge; `by` is "user" for a note the person wrote, drawn in their colour, or "agent"; a `group` note points at
    the middle of its highlighted group."""
    mol, kept, notes = _read(text)
    if clear:
        notes = annotate.empty()
    marked = add(mol, notes, mark) if mark else []
    drawn = Chem.Mol(mol)
    with rdBase.BlockLogs():
        rdDepictor.Compute2DCoords(drawn)
    highlights = annotate.apply(Chem.Mol(mol), {**notes, "atoms": [[i, h, ""] for i, h, _ in notes["atoms"]],
                                                "bonds": [[a, b, h, ""] for a, b, h, _ in notes["bonds"]]})
    width, height = cols * cell_pixels, rows * graphics.CELL_ASPECT * cell_pixels
    drawer = rdMolDraw2D.MolDraw2DCairo(width, height)
    options = drawer.drawOptions()
    options.clearBackground = False
    options.setBackgroundColour((0, 0, 0, 0))
    options.setAtomPalette(graphics.palette(theme))
    options.padding = 0.02
    options.prepareMolsBeforeDrawing = False
    annotate.set_options(options, notes)
    mean = renderer.mean_bond_length(drawn)
    if mean > 0:
        options.fixedBondLength = graphics.BOND_CELLS * cell_pixels / mean
    with rdBase.BlockLogs():
        prepared = rdMolDraw2D.PrepareMolForDrawing(drawn, kekulize=True, addChiralHs=False, wedgeBonds=True)
        if "cip" in notes["show"]:
            annotate.label_stereo(prepared, mol)
        drawer.DrawMolecule(prepared, **highlights)
    drawer.FinishDrawing()
    points = [drawer.GetDrawCoords(i) for i in range(prepared.GetNumAtoms())]
    image = Image.open(BytesIO(drawer.GetDrawingText())).convert("RGBA")
    # cropped to the ink, as molterm's pictures are, then padded to whole cells both ways, so that the terminal doesn't stretch it
    ys, xs = np.nonzero(np.asarray(image)[:, :, 3])
    margin = graphics.MARGIN
    left, top = (max(int(xs.min()) - margin, 0), max(int(ys.min()) - margin, 0)) if len(xs) else (0, 0)
    right, bottom = (min(int(xs.max()) + 1 + margin, width), min(int(ys.max()) + 1 + margin, height)) if len(xs) else (1, 1)
    row_pixels = graphics.CELL_ASPECT * cell_pixels
    used_cols, used_rows = math.ceil((right - left) / cell_pixels), math.ceil((bottom - top) / row_pixels)
    shown = [entry for entry in ([("a", i, t, h) for i, h, t in notes["atoms"] if t] + [("b", (a, b), t, h) for a, b, h, t in notes["bonds"] if t])]
    if shown:
        used_cols += 1  # a column of leaders beyond the drawing, so that none ends inside a label
    out = Image.new("RGBA", (used_cols * cell_pixels, used_rows * row_pixels), (0, 0, 0, 0))
    out.paste(image.crop((left, top, right, bottom)), (0, 0))

    pairs = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()]

    def ids(entry):
        return [entry[1]] if entry[0] == "a" else list(entry[1])

    def anchor(entry):
        """Its atom, the middle of its bond, or the middle of its highlighted group."""
        chosen = annotate.group(notes, pairs, ids(entry)) if "group" in annotate.flags(notes, ids(entry)) else ids(entry)
        return (sum(points[i].x for i in chosen) / len(chosen) - left, sum(points[i].y for i in chosen) / len(chosen) - top)

    anchors = [anchor(entry) for entry in shown]
    leader_rows = _rows([int(y // row_pixels) for _, y in anchors], used_rows)
    pen = ImageDraw.Draw(out)
    line = max(round(cell_pixels / 9), 1)
    dot = max(round(cell_pixels / 5), 2)
    edge = out.width
    for entry, (x, y), row in zip(shown, anchors, leader_rows):
        colour = USER_LEADER if "user" in annotate.flags(notes, ids(entry)) else LEADER
        level = (row + 0.5) * row_pixels
        bend = min(x + abs(level - y), edge)  # at 45 degrees to the row, then along it
        pen.line([(x, y), (bend, level), (edge, level)], fill=(*colour, 255), width=line, joint="curve")
        pen.ellipse([x - dot, y - dot, x + dot, y + dot], fill=(*colour, 255))
    buffer = BytesIO()
    out.save(buffer, "PNG")
    return {"png": base64.b64encode(buffer.getvalue()).decode("ascii"), "cols": used_cols, "rows": used_rows,
            "smiles": annotate.write(kept, notes), "marked": marked,
            "notes": [{"atom": entry[1] if entry[0] == "a" else None, "bond": list(entry[1]) if entry[0] == "b" else None,
                       "text": entry[2], "highlight": entry[3], "row": row,
                       "by": "user" if "user" in annotate.flags(notes, ids(entry)) else "agent",
                       "group": "group" in annotate.flags(notes, ids(entry))} for entry, row in zip(shown, leader_rows)]}

