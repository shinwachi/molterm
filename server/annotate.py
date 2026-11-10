"""Annotations on a molecule: highlights and notes on atoms and bonds, and atom numbers or stereo labels, all drawn by RDKit.

They travel as CXSMILES atom properties, so that a line of text carries them and RDKit reads them back. RDKit keeps no bond
properties in CXSMILES, so a bond's ride on its lower-numbered atom, named after the other one.

Notes here count atoms by their index in a SMILES: as written, and as RDKit reads it. The editor keeps its own ids instead, and
converts on the way in and out. Either way the shape is the same plain data:

    {"atoms": [[atom, highlight, note], ...], "bonds": [[atom, atom, highlight, note], ...], "show": ["numbers", "cip"]}

with "" for no highlight or no note, and the lower atom first in a bond. A note may also have flags, in an optional

    "flags": {"a3": "user", "b1-2": "user group"}

keyed by its atom ("a" and the atom) or bond ("b" and its atoms): "user" for a note the person wrote (rather than an agent), which
hosts draw in the user's colour, and "group" for a note on the highlighted group its atom or bond is in, which is pointed at in the
middle of the group rather than at the atom.
"""
import base64
import re

from rdkit import Chem, rdBase
from rdkit.Chem import rdCIPLabeler
from rdkit.Chem.Draw import rdMolDraw2D

import markush

MAX_NOTE = 120
MAX_NOTE_LINES = 6
MAX_NOTES = 400
HIGHLIGHTS = {"yellow": (1.0, 0.82, 0.25), "green": (0.45, 0.82, 0.45), "blue": (0.45, 0.68, 1.0), "pink": (1.0, 0.55, 0.72)}
SHOWN = ("numbers", "cip")
_HIGHLIGHT_ALPHA = 0.55
_NOTE, _HIGHLIGHT, _SHOW, _FLAGS = "atomNote", "molterm_highlight", "molterm_show", "molterm_flags"
FLAGS = ("user", "group")
_ESCAPES = str.maketrans({c: f"&#{ord(c)};" for c in "&:;|,\n"})  # as RDKit writes them, a line break as &#10;
EXPORT_SIZE = (600, 450)


class NoteError(ValueError):
    """Annotations that can't have come from here."""


def empty():
    return {"atoms": [], "bonds": [], "show": []}


def is_empty(notes):
    return not (notes["atoms"] or notes["bonds"] or notes["show"])


def clean_note(text):
    """A note as it can be kept: printable, short, and on a few lines at most (its line breaks are kept, other control characters
    are not, nor blank lines before or after it)."""
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = ["".join(c for c in line if c.isprintable()).rstrip() for line in lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[:MAX_NOTE_LINES])[:MAX_NOTE].strip()


def check(notes, limit=10**6):
    """Raise NoteError unless `notes` has the shape above, with atoms below `limit`."""
    try:
        if not isinstance(notes, dict) or set(notes) - {"flags"} != {"atoms", "bonds", "show"}:
            raise NoteError("The annotations are not valid.")
        flags = notes.get("flags", {})
        if not isinstance(flags, dict) or len(flags) > MAX_NOTES:
            raise NoteError("The annotations are not valid.")
        for key, value in flags.items():
            if not (isinstance(key, str) and re.fullmatch(r"a\d+|b\d+-\d+", key) and isinstance(value, str) and set(value.split()) <= set(FLAGS)):
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


def key(ids):
    """A note's key in "flags": "a3" for atom 3, "b1-2" for the bond between atoms 1 and 2."""
    return f"a{ids[0]}" if len(ids) == 1 else "b{}-{}".format(*sorted(ids))


def flags(notes, ids):
    """The flags of the note on this atom ([atom]) or bond ([atom, atom])."""
    return set(notes.get("flags", {}).get(key(ids), "").split())


def set_flags(notes, ids, chosen):
    found = notes.setdefault("flags", {})
    words = " ".join(name for name in FLAGS if name in chosen)
    if words:
        found[key(ids)] = words
    else:
        found.pop(key(ids), None)
    if not found:
        del notes["flags"]


def group(notes, pairs, ids):
    """The atoms of the highlighted group an atom ([atom]) or bond ([atom, atom]) is in: those joined to it through bonds (`pairs`)
    whose atoms are highlighted in its colour. Just its own atoms if it has no highlight."""
    shade = {entry[0]: entry[1] for entry in notes["atoms"] if entry[1]}
    bond_shade = {tuple(entry[:2]): entry[2] for entry in notes["bonds"] if entry[2]}
    colour = shade.get(ids[0]) if len(ids) == 1 else bond_shade.get(tuple(sorted(ids))) or shade.get(ids[0]) or shade.get(ids[1])
    found = list(ids)
    if not colour:
        return found
    near = {}
    for a, b in pairs:
        near.setdefault(a, []).append(b)
        near.setdefault(b, []).append(a)
    for atom in found:  # grows as it goes
        for other in near.get(atom, ()):
            if other not in found and shade.get(other) == colour:
                found.append(other)
    return found


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
    bonds, bond_flags = {}, {}
    for atom in mol.GetAtoms():
        props = atom.GetPropsAsDict(includePrivate=False, includeComputed=False)
        index = atom.GetIdx()
        highlight = str(props.get(_HIGHLIGHT, ""))
        note = clean_note(str(props.get(_NOTE, "")))
        if highlight not in HIGHLIGHTS:
            highlight = ""
        if highlight or note:
            notes["atoms"].append([index, highlight, note])
        if note and props.get(_FLAGS):
            set_flags(notes, [index], str(props[_FLAGS]).split())
        for name, value in props.items():
            parts = name.split("_")
            if len(parts) == 4 and parts[:2] == ["molterm", "bond"] and parts[2].isdigit() and parts[3] in ("highlight", "note", "flags"):
                other = int(parts[2])
                if other == index or other >= mol.GetNumAtoms() or not mol.GetBondBetweenAtoms(index, other):
                    continue
                entry = bonds.setdefault(tuple(sorted((index, other))), ["", ""])
                if parts[3] == "flags":
                    bond_flags[tuple(sorted((index, other)))] = str(value).split()
                elif parts[3] == "highlight":
                    entry[0] = str(value) if str(value) in HIGHLIGHTS else ""
                else:
                    entry[1] = clean_note(str(value))
        if index == 0:
            notes["show"] = [name for name in SHOWN if name in str(props.get(_SHOW, "")).split()]
    notes["bonds"] = [[*pair, *entry] for pair, entry in sorted(bonds.items()) if entry[0] or entry[1]]
    for pair, chosen in bond_flags.items():
        if bonds.get(pair, ["", ""])[1]:
            set_flags(notes, list(pair), chosen)
    return notes


def write(smiles, notes):
    """The SMILES as it was written, with its notes in its CXSMILES extension if there are any."""
    props = []
    for index, highlight, note in notes["atoms"]:
        if note:
            props.append((index, _NOTE, note))
            if flags(notes, [index]):
                props.append((index, _FLAGS, " ".join(name for name in FLAGS if name in flags(notes, [index]))))
        if highlight:
            props.append((index, _HIGHLIGHT, highlight))
    for first, second, highlight, note in notes["bonds"]:
        if highlight:
            props.append((first, f"molterm_bond_{second}_highlight", highlight))
        if note:
            props.append((first, f"molterm_bond_{second}_note", note))
            if flags(notes, [first, second]):
                props.append((first, f"molterm_bond_{second}_flags", " ".join(name for name in FLAGS if name in flags(notes, [first, second]))))
    if notes["show"]:
        props.append((0, _SHOW, " ".join(notes["show"])))
    base, fields = markush.split(smiles)
    if props:
        field = "atomProp:" + ":".join(f"{index}.{name}.{_escape(value)}" for index, name, value in sorted(props, key=lambda p: p[0]))
        # before the R-group definitions, which ChemAxon writes last
        place = next((i for i, f in enumerate(fields) if f.startswith(("RG:", "LOG="))), len(fields))
        fields = [*fields[:place], field, *fields[place:]]
    return markush.join(base, fields)


# A SMILES's tokens: a bracket atom, an atom of the organic subset (Br and Cl before B and C), a ring number, a bond, a branch or a dot.
# What's left over, in a SMARTS outside brackets, is taken a character at a time and belongs to nothing.
_SMILES_TOKEN = re.compile(r"\[[^\]]*\]|Br|Cl|[BCNOPSFI]|[bcnops]|\*|%\d\d|\d|[-=#$:/\\~,;!@&]|[().]|.", re.S)
_SMILES_ATOM = re.compile(r"\[[^\]]*\]|Br|Cl|[BCNOPSFI]|[bcnops]|\*")


def smiles_parts(smiles):
    """Where a SMILES (without its CXSMILES extension) writes each atom and bond: ([(start, end) of atom 0, 1, ...], {(lower atom,
    higher atom): [(start, end), ...]}). Atoms count in the order they are written, which is how RDKit numbers them. A bond's parts
    are its symbol and, for a ring closure, its two ring numbers; a bond written as nothing, as most single and aromatic ones are,
    has none."""
    atoms, bonds = [], {}
    previous, branches, pending, rings = None, [], [], {}

    def bond(first, second, parts):
        if first is not None and second is not None and first != second:
            bonds.setdefault((min(first, second), max(first, second)), []).extend(parts)

    for match in _SMILES_TOKEN.finditer(smiles):
        token, span = match.group(), match.span()
        if _SMILES_ATOM.fullmatch(token):
            atoms.append(span)
            bond(previous, len(atoms) - 1, pending)
            previous, pending = len(atoms) - 1, []
        elif token == "(":
            branches.append(previous)
        elif token == ")":
            previous, pending = (branches.pop() if branches else previous), []
        elif token == ".":
            previous, pending = None, []
        elif token[0] == "%" or token.isdigit():
            if token in rings:
                opened, parts = rings.pop(token)
                bond(opened, previous, parts + pending + [span])
            else:
                rings[token] = (previous, pending + [span])
            pending = []
        elif token in "-=#$:/\\~,;!@&":
            pending.append(span)
    return atoms, bonds


def smiles_paint(smiles, notes):
    """The parts of a SMILES to paint in their highlights' colours, for showing which atoms and bonds are highlighted where the SMILES
    is written: {"smiles": the SMILES without its extension, "marks": [[start, end, colour], ...]}, in order and not overlapping.
    `notes` count atoms by their index in this SMILES, as everywhere here. A group reads as one: a branch's parentheses are painted
    too when all of it, and the atom it hangs from, are in one colour."""
    core = markush.split(smiles)[0] if "|" in smiles else smiles.strip()
    atoms, bonds = smiles_parts(core)
    colours = [""] * len(core)
    for index, highlight, _ in notes["atoms"]:
        if highlight and index < len(atoms):
            start, end = atoms[index]
            colours[start:end] = [highlight] * (end - start)
    for first, second, highlight, _ in notes["bonds"]:
        for start, end in bonds.get((first, second), []) if highlight else ():
            colours[start:end] = [highlight] * (end - start)
    pairs, opened = [], []
    for position, char in enumerate(core):
        if char == "(":
            opened.append(position)
        elif char == ")" and opened:
            pairs.append((opened.pop(), position))
    for start, end in sorted(pairs, key=lambda pair: pair[1] - pair[0]):  # inner branches first, so that outer ones see them painted
        inside = [colours[i] for i in range(start + 1, end) if core[i] not in "()" or colours[i]]
        if start > 0 and colours[start - 1] and inside and all(inside) and len(set(inside + [colours[start - 1]])) == 1:
            colours[start] = colours[end] = colours[start - 1]
    marks = []
    for position, colour in enumerate(colours):
        if not colour:
            continue
        if marks and marks[-1][1] == position and marks[-1][2] == colour:
            marks[-1][1] = position + 1
        else:
            marks.append([position, position + 1, colour])
    return {"smiles": core, "marks": marks}


def renumber(notes, order):
    """Notes with each atom index i replaced by order[i], for when a molecule is written out in another order. Atoms missing from
    `order` lose their notes."""
    moved = empty()
    for index, highlight, note in notes["atoms"]:
        if index in order:
            moved["atoms"].append([order[index], highlight, note])
            set_flags(moved, [order[index]], flags(notes, [index]))
    for first, second, highlight, note in notes["bonds"]:
        if first in order and second in order:
            moved["bonds"].append([*sorted((order[first], order[second])), highlight, note])
            set_flags(moved, [order[first], order[second]], flags(notes, [first, second]))
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
            mol.GetAtomWithIdx(index).SetProp("atomNote", note.replace("\n", " "))  # drawn by RDKit, on one line
        if highlight:
            atoms.append(index)
            atom_colours[index] = (*HIGHLIGHTS[highlight], _HIGHLIGHT_ALPHA)
    for first, second, highlight, note in notes["bonds"]:
        bond = mol.GetBondBetweenAtoms(first, second) if max(first, second) < count else None
        if not bond:
            continue
        if note:
            bond.SetProp("bondNote", note.replace("\n", " "))
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
