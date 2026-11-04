"""Annotations on a molecule: highlights and notes on atoms and bonds, and atom numbers or stereo labels, all drawn by RDKit.

They travel as CXSMILES atom properties, so that a line of text carries them and RDKit reads them back. RDKit keeps no bond
properties in CXSMILES, so a bond's ride on its lower-numbered atom, named after the other one.

Notes here count atoms by their index in a SMILES: as written, and as RDKit reads it. The editor keeps its own ids instead, and
converts on the way in and out. Either way the shape is the same plain data:

    {"atoms": [[atom, highlight, note], ...], "bonds": [[atom, atom, highlight, note], ...], "show": ["numbers", "cip"]}

with "" for no highlight or no note, and the lower atom first in a bond.
"""
import base64

from rdkit import Chem, rdBase
from rdkit.Chem import rdCIPLabeler
from rdkit.Chem.Draw import rdMolDraw2D

import markush

MAX_NOTE = 40
MAX_NOTES = 400
HIGHLIGHTS = {"yellow": (1.0, 0.82, 0.25), "green": (0.45, 0.82, 0.45), "blue": (0.45, 0.68, 1.0), "pink": (1.0, 0.55, 0.72)}
SHOWN = ("numbers", "cip")
_HIGHLIGHT_ALPHA = 0.55
_NOTE, _HIGHLIGHT, _SHOW = "atomNote", "molterm_highlight", "molterm_show"
_ESCAPES = str.maketrans({c: f"&#{ord(c)};" for c in "&:;|,"})  # as RDKit writes them
EXPORT_SIZE = (600, 450)


class NoteError(ValueError):
    """Annotations that can't have come from here."""


def empty():
    return {"atoms": [], "bonds": [], "show": []}


def is_empty(notes):
    return not (notes["atoms"] or notes["bonds"] or notes["show"])


def clean_note(text):
    """A note as it can be kept: printable, on one line, and short."""
    return "".join(c for c in text if c.isprintable())[:MAX_NOTE].strip()


def check(notes, limit=10**6):
    """Raise NoteError unless `notes` has the shape above, with atoms below `limit`."""
    try:
        if not isinstance(notes, dict) or set(notes) != {"atoms", "bonds", "show"}:
            raise NoteError("The annotations are not valid.")
        atoms, bonds, show = notes["atoms"], notes["bonds"], notes["show"]
        if not (isinstance(atoms, list) and isinstance(bonds, list) and len(atoms) + len(bonds) <= MAX_NOTES):
            raise NoteError("There are too many annotations.")
        if not isinstance(show, list) or len(set(show)) != len(show) or not all(name in SHOWN for name in show):
            raise NoteError("The annotations are not valid.")
        seen = set()
        for entry in atoms + bonds:
            *ids, highlight, note = entry
            key = tuple(ids)
            if len(ids) not in (1, 2) or key in seen or not all(type(i) is int and 0 <= i < limit for i in ids):
                raise NoteError("The annotations are not valid.")
            if (len(ids) == 2 and ids[0] >= ids[1]) or (entry in bonds) != (len(ids) == 2):
                raise NoteError("The annotations are not valid.")
            if highlight not in ("", *HIGHLIGHTS) or not isinstance(note, str) or note != clean_note(note) or not (highlight or note):
                raise NoteError("The annotations are not valid.")
            seen.add(key)
    except (TypeError, ValueError) as error:
        if isinstance(error, NoteError):
            raise
        raise NoteError("The annotations are not valid.") from error


# As text


def _escape(text):
    return text.translate(_ESCAPES)


def split(text):
    """A SMILES that may carry a CXSMILES extension: (the SMILES with the rest of its extension, such as a Markush structure's, its
    notes). Raises ValueError if RDKit can't read it."""
    text = text.strip()
    smiles, fields = markush.split(text)  # a MarkushError is a ValueError
    kept = markush.join(smiles, [f for f in fields if not f.startswith("atomProp:")])
    core = markush.separate(text)[0]
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(core) or Chem.MolFromSmarts(core)
    if mol is None:
        raise ValueError(f"Not valid SMILES: {text[:60]}")
    return kept, read(mol)


def read(mol):
    """The notes kept on a molecule's atoms, as RDKit reads them from CXSMILES."""
    notes = empty()
    bonds = {}
    for atom in mol.GetAtoms():
        props = atom.GetPropsAsDict(includePrivate=False, includeComputed=False)
        index = atom.GetIdx()
        highlight = str(props.get(_HIGHLIGHT, ""))
        note = clean_note(str(props.get(_NOTE, "")))
        if highlight not in HIGHLIGHTS:
            highlight = ""
        if highlight or note:
            notes["atoms"].append([index, highlight, note])
        for name, value in props.items():
            parts = name.split("_")
            if len(parts) == 4 and parts[:2] == ["molterm", "bond"] and parts[2].isdigit() and parts[3] in ("highlight", "note"):
                other = int(parts[2])
                if other == index or other >= mol.GetNumAtoms() or not mol.GetBondBetweenAtoms(index, other):
                    continue
                entry = bonds.setdefault(tuple(sorted((index, other))), ["", ""])
                if parts[3] == "highlight":
                    entry[0] = str(value) if str(value) in HIGHLIGHTS else ""
                else:
                    entry[1] = clean_note(str(value))
        if index == 0:
            notes["show"] = [name for name in SHOWN if name in str(props.get(_SHOW, "")).split()]
    notes["bonds"] = [[*pair, *entry] for pair, entry in sorted(bonds.items()) if entry[0] or entry[1]]
    return notes


def write(smiles, notes):
    """The SMILES as it was written, with its notes in its CXSMILES extension if there are any."""
    props = []
    for index, highlight, note in notes["atoms"]:
        if note:
            props.append((index, _NOTE, note))
        if highlight:
            props.append((index, _HIGHLIGHT, highlight))
    for first, second, highlight, note in notes["bonds"]:
        if highlight:
            props.append((first, f"molterm_bond_{second}_highlight", highlight))
        if note:
            props.append((first, f"molterm_bond_{second}_note", note))
    if notes["show"]:
        props.append((0, _SHOW, " ".join(notes["show"])))
    base, fields = markush.split(smiles)
    if props:
        field = "atomProp:" + ":".join(f"{index}.{name}.{_escape(value)}" for index, name, value in sorted(props, key=lambda p: p[0]))
        # before the R-group definitions, which ChemAxon writes last
        place = next((i for i, f in enumerate(fields) if f.startswith(("RG:", "LOG="))), len(fields))
        fields = [*fields[:place], field, *fields[place:]]
    return markush.join(base, fields)


def renumber(notes, order):
    """Notes with each atom index i replaced by order[i], for when a molecule is written out in another order. Atoms missing from
    `order` lose their notes."""
    moved = empty()
    for index, highlight, note in notes["atoms"]:
        if index in order:
            moved["atoms"].append([order[index], highlight, note])
    for first, second, highlight, note in notes["bonds"]:
        if first in order and second in order:
            moved["bonds"].append([*sorted((order[first], order[second])), highlight, note])
    moved["atoms"].sort()
    moved["bonds"].sort()
    moved["show"] = list(notes["show"])
    return moved


# Drawn


def apply(mol, notes):
    """Put the notes on the molecule for RDKit to draw, and return the highlights as keyword arguments for DrawMolecule."""
    count = mol.GetNumAtoms()
    atoms, atom_colours, bonds, bond_colours = [], {}, [], {}
    for index, highlight, note in notes["atoms"]:
        if index >= count:
            continue
        if note:
            mol.GetAtomWithIdx(index).SetProp("atomNote", note)
        if highlight:
            atoms.append(index)
            atom_colours[index] = (*HIGHLIGHTS[highlight], _HIGHLIGHT_ALPHA)
    for first, second, highlight, note in notes["bonds"]:
        bond = mol.GetBondBetweenAtoms(first, second) if max(first, second) < count else None
        if not bond:
            continue
        if note:
            bond.SetProp("bondNote", note)
        if highlight:
            bonds.append(bond.GetIdx())
            bond_colours[bond.GetIdx()] = (*HIGHLIGHTS[highlight], _HIGHLIGHT_ALPHA)
    return {"highlightAtoms": atoms, "highlightAtomColors": atom_colours, "highlightBonds": bonds, "highlightBondColors": bond_colours}


def label_stereo(mol, source=None):
    """Give the atoms and bonds of `mol` RDKit's CIP labels, worked out on `source` if `mol` isn't valid chemistry itself. Both must
    have their atoms in the same order. Does nothing if RDKit can't label it."""
    try:
        with rdBase.BlockLogs():
            labelled = Chem.Mol(source if source is not None else mol)
            rdCIPLabeler.AssignCIPLabels(labelled)
    except (RuntimeError, ValueError):
        return
    if labelled.GetNumAtoms() != mol.GetNumAtoms():
        return
    for atom in labelled.GetAtoms():
        if atom.HasProp("_CIPCode"):
            mol.GetAtomWithIdx(atom.GetIdx()).SetProp("_CIPCode", atom.GetProp("_CIPCode"))
    for bond in labelled.GetBonds():
        if bond.HasProp("_CIPCode"):
            twin = mol.GetBondBetweenAtoms(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
            if twin:
                twin.SetProp("_CIPCode", bond.GetProp("_CIPCode"))


def set_options(options, notes):
    options.addAtomIndices = "numbers" in notes["show"]
    options.addStereoAnnotation = "cip" in notes["show"]
    options.annotationFontScale = 0.6


def export(mol, notes, fmt, palette=None, size=EXPORT_SIZE):
    """The molecule on white, for the clipboard: PNG bytes, or SVG text. `mol` must have 2D coordinates."""
    mol = Chem.Mol(mol)
    if "cip" in notes["show"]:
        label_stereo(mol)
    highlights = apply(mol, notes)
    drawer = rdMolDraw2D.MolDraw2DCairo(*size) if fmt == "png" else rdMolDraw2D.MolDraw2DSVG(*size)
    options = drawer.drawOptions()
    options.setBackgroundColour((1, 1, 1, 1))
    if palette:
        options.setAtomPalette(palette)
    set_options(options, notes)
    with rdBase.BlockLogs():
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol, **highlights)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


# On the clipboard


KINDS = ("smiles", "svg", "png", "all")
DESCRIBED = {"smiles": "SMILES", "svg": "SVG", "png": "PNG", "all": "SMILES, SVG and PNG"}


def clipboard(smiles, mol, notes, kind, palette=None):
    """What to copy, as (MIME type, bytes): text first, which is also what a terminal with only OSC 52 gets."""
    entries = []
    if kind == "svg":
        svg = export(mol, notes, "svg", palette).encode()
        entries = [("text/plain", svg), ("image/svg+xml", svg)]
    else:
        entries = [("text/plain", write(smiles, notes).encode())]
        if kind in ("png", "all"):
            entries.append(("image/png", export(mol, notes, "png", palette)))
        if kind == "all":
            entries.append(("image/svg+xml", export(mol, notes, "svg", palette).encode()))
    return entries


def clipboard_sequence(entries):
    """Ask the terminal to put these on the system clipboard. OSC 52 carries the text, as every terminal that copies at all knows it.
    Kitty's clipboard protocol (OSC 5522), after it, replaces that with all of them, so that a paste into a document gets the
    picture. Terminals that don't know it ignore it, and Kitty answers it, which the input parser takes out."""
    text = entries[0][1]
    out = [f"\x1b]52;c;{base64.b64encode(text).decode('ascii')}\x1b\\"]
    if len(entries) > 1 or entries[0][0] != "text/plain":
        out.append("\x1b]5522;type=write\x1b\\")
        for mime, data in entries:
            name = base64.b64encode(mime.encode()).decode("ascii")
            payload = base64.b64encode(data).decode("ascii")
            for start in range(0, len(payload), 5460):  # 4095 bytes before encoding, so each piece ends on whole groups
                out.append(f"\x1b]5522;type=wdata:mime={name};{payload[start:start + 5460]}\x1b\\")
        out.append("\x1b]5522;type=wdata\x1b\\")
    return "".join(out)
