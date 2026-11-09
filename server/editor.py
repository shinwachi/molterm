"""A molecular editor for terminals with Kitty graphics, used with the mouse and the keyboard.

The client only passes on raw input and prints what comes back, so everything happens here. The server keeps no sessions: the
drawing travels with each request as an opaque string, and is checked on the way back in because the client could send anything.

The molecule is kept as a plain list of atoms and bonds rather than an RDKit object, because a drawing in progress is often not
valid chemistry. RDKit reads it as a molblock, which also works out charges, wedges and double bond geometry, and draws each frame.

Atoms are [id, element, x, y, charge] with, when they have any, a dict of properties after them; bonds are [first, second, kind] and
likewise. Besides elements an atom can be a Markush atom: R# (an R-group label, its number in "r"), L (an atom list in "list", a NOT
list with "not") or one of markush.GENERICS. The properties, all optional:

    atoms   iso (mass number), rad (radical, as in a molfile: 1 singlet, 2 doublet, 3 triplet), r, list, not,
            ap (attachment point of an R-group definition: 1 first, 2 second, 3 both), rg (the R-group this atom's definition is of),
            st (enhanced stereo group: abs, and1-9, or1-9), link ([least, most] repeats of a link node),
            sru ([bracket number, label]: a repeating unit, such as n or 1-3)
    bonds   ends (ids of the atoms a variable attachment can go to, the bond's second atom among them; the first is the substituent's),
            topo (ring or chain, for a query)
"""
import base64
import hashlib
import json
import math
import re
import zlib
from io import BytesIO

from PIL import Image
from rdkit import Chem, rdBase
from rdkit.Chem import AllChem, Crippen, Descriptors, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D
from rdkit.Geometry import Point2D

import annotate
import callouts
import editor_icons
import graphics
import markush
import placeholders
import themes
import molecule

MAX_ATOMS = 200
MAX_BONDS = 400
MAX_UNDO = 30
MAX_STATE = 64_000  # bytes as sent
MAX_STATE_JSON = 1_000_000
MAX_ENTRY = 2000
MAX_EVENTS = 16_000  # bytes of input in one request
MAX_PIXELS = 2_000_000  # a very large window is drawn smaller and scaled up by the terminal, to keep frames quick
COLS_RANGE, ROWS_RANGE = (50, 400), (14, 200)
CELL_WIDTH_RANGE, CELL_HEIGHT_RANGE = (4, 60), (4, 120)
DEFAULT_CELL = (10, 20)

SINGLE, DOUBLE, TRIPLE, WEDGE, HASH = 1, 2, 3, 4, 5
AROMATIC, ANY, SINGLE_DOUBLE, SINGLE_AROMATIC, DOUBLE_AROMATIC, WAVY, DATIVE, CROSSED = 6, 7, 8, 9, 10, 11, 12, 13
# each kind's bond type in a V3000 molfile, and its CFG there
_ORDER = {SINGLE: 1, DOUBLE: 2, TRIPLE: 3, WEDGE: 1, HASH: 1, AROMATIC: 4, ANY: 8, SINGLE_DOUBLE: 5, SINGLE_AROMATIC: 6,
          DOUBLE_AROMATIC: 7, WAVY: 1, DATIVE: 9, CROSSED: 2}
_CFG = {WEDGE: 1, WAVY: 2, HASH: 3, CROSSED: 2}
DIRECTED = (WEDGE, HASH, WAVY, DATIVE)  # whose first atom matters: the narrow end, or the donor
QUERY_BONDS = (ANY, SINGLE_DOUBLE, SINGLE_AROMATIC, DOUBLE_AROMATIC)
BOND_NAMES = {SINGLE: "single", DOUBLE: "double", TRIPLE: "triple", WEDGE: "wedged", HASH: "hashed", AROMATIC: "aromatic", ANY: "any",
              SINGLE_DOUBLE: "single or double", SINGLE_AROMATIC: "single or aromatic", DOUBLE_AROMATIC: "double or aromatic",
              WAVY: "wavy (either stereo)", DATIVE: "dative", CROSSED: "crossed double (either geometry)"}
MARKUSH = ("R#", "L", *markush.GENERICS)  # atoms that aren't elements
MAX_LIST = 12
MAX_BRACKET = 99
_STEREO_GROUP = re.compile(r"abs|(?:and|or)[1-9]")
_BRACKET_LABEL = re.compile(r"[a-z]|\d{1,2}(?:-\d{1,2})?")
TOOLS = ("select", "erase", "pan", "text", "bond", "ring", "plus", "minus", "chain", "rlabel")
RINGS = (3, 4, 5, 6, 7, 8, "benzene", "cyclopentadiene")
_SIDES = {"benzene": 6, "cyclopentadiene": 5}
_DOUBLES = {"benzene": 3, "cyclopentadiene": 2}  # double bonds a ring comes with
MENU_ROWS, STATUS_ROWS = 1, 2
ZOOM_RANGE = (1.5, 16.0)  # cell widths per bond
DEFAULT_ZOOM = 6.0
DRAG_START = 0.35  # bond lengths the pointer must travel before a press counts as a drag
MERGE = 0.25  # a new ring atom this close to an existing one becomes that atom
MAX_LABEL = 32
PRESS_MODES = ("draw", "move", "marquee", "pan", "none")
LAYERS = ("palette", "structure", "overlay", "text", "backing", "notes")
MIN_BOX_COLS = 36  # room for the buttons on the bottom edge
STRIP_COLS, STRIP_WIDE = 56, 96  # an inline editor's strip at its narrowest, for its line of status and buttons, and when there is room
HOST_REPAINT = "\x1b]5523;repaint\x1b\\"  # to an inline editor's host: draw your screen again here, and the editor draws over it
HOST_TOOLS = "\x1b]5523;tools={}\x1b\\"  # to an inline editor's host: the tools were shown (1) or put away (0), to remember
# to an inline editor's host: the notes, which it shows in bubbles beside the drawing while it is edited, as base64 of JSON
# {"edge": the column the leaders end at, "notes": [{"text", "row", "by", and "draft": true for the note being written, its text as
# typed so far, which the host shows with a cursor where it is typed}]}; each leader, drawn in the picture, ends in the middle of its row
HOST_NOTES = "\x1b]5523;notes={}\x1b\\"
WINDOW_BUTTONS = (("minimize", " _ "), ("maximize", " □ "))  # on the top edge of a box its host can move: the host acts on them
RESIZE_GRIP = "◢"

ACCENT, RED = (0.22, 0.53, 0.9), (0.94, 0.33, 0.31)
HOTSPOT, HOTSPOT_DOT = (0.45, 0.72, 1.0), (0.2, 0.8, 0.35)  # ChemDraw marks an atom with a light blue circle and a green dot
CONTINUE, QUIT, FAILED, CANCELLED = 0, 1, 2, 3
CANCEL, INTERRUPT = "cancel", "interrupt"  # how the editor can end, besides Done

# Text for the atom label box. Each group is written from the atom it replaces, with * as whatever that atom is bonded to.
GROUPS = {"ch3": "*C", "me": "*C", "et": "*CC", "pr": "*CCC", "npr": "*CCC", "ipr": "*C(C)C", "bu": "*CCCC", "nbu": "*CCCC", "tbu": "*C(C)(C)C",
          "oh": "*O", "ome": "*OC", "oet": "*OCC", "oac": "*OC(C)=O", "oph": "*Oc1ccccc1", "nh2": "*N", "nhme": "*NC", "nme2": "*N(C)C",
          "nhac": "*NC(C)=O", "nh3+": "*[NH3+]", "sh": "*S", "sme": "*SC", "cf3": "*C(F)(F)F", "ccl3": "*C(Cl)(Cl)Cl", "cn": "*C#N",
          "no2": "*[N+](=O)[O-]", "cho": "*C=O", "ac": "*C(C)=O", "come": "*C(C)=O", "cooh": "*C(O)=O", "co2h": "*C(O)=O", "coome": "*C(=O)OC",
          "co2me": "*C(=O)OC", "cooet": "*C(=O)OCC", "co2et": "*C(=O)OCC", "conh2": "*C(N)=O", "ph": "*c1ccccc1", "bn": "*Cc1ccccc1",
          "bz": "*C(=O)c1ccccc1", "boc": "*C(=O)OC(C)(C)C", "cbz": "*C(=O)OCc1ccccc1", "ms": "*S(C)(=O)=O", "so2me": "*S(C)(=O)=O",
          "ts": "*S(=O)(=O)c1ccc(C)cc1", "so3h": "*S(O)(=O)=O", "tms": "*[Si](C)(C)C"}

KEYS_HELP = """Keys follow ChemDraw 17.1's tables. They act on the hotspot, shown in light blue: what the pointer is over.
Arrows move it between atoms and bonds, Shift+arrows from atom to atom. The mouse takes it back.

On an atom   c n o s p f i h k  elements   b Br   l C Cl   w N   q O   S Si   N Na   B boron   d D   x X
             m Me   e Et   A Ac   P Ph   E CO2Me   F CF3   + -  charge   = Enter  label box
  Markush    r R-group label (again for the next number)   . attachment point: first, second, both
  sprout     1 bond, zigzag   0 bond, curling round   2 acetyl or C=O   3 a phenyl
             4 wedge   5 hash   6 cyclohexane   7 cyclopentane   8 =CH2   9 dimethyl   z alkyne
  remove     Space Delete Backspace  the label, leaving carbon. Delete on carbon removes the atom.
On a bond    1 2 3  order   w wedge   h hash   y wavy   Delete  remove
  fuse       a benzene   z cyclopentadiene   v cyclopropane   4 5 6 7 8  ring of that size
On nothing   x bond tool   X chain tool   u cyclohexane   U cyclopentane   j benzene   J cyclopentadiene
             t text   Space selection   ? this help   / load a SMILES   v fit

Ring tool    click a bond or atom to fuse, Alt+click a ring atom to sprout, or click empty space
Bond tool    click to draw or to step a bond's order, drag to aim or to join
Text tool    click an atom and type: N, Cl, OMe, CF3, CO2H, NO2, Ph, Boc, Ts...
             or R1, A, Q, X, M, AH; a list as C,N,O or !F,Cl; an isotope as 13C or D
Chain tool   drag from an atom or empty space; the status line counts the bonds
Markush      right-click an atom for its properties and R-groups, a bond for queries and variable attachment,
             a selection for repeating units, R-group definitions and stereo groups. R-group logic: Structure
Selection    drag to move, drag across empty space to select, Shift+click to add; a letter labels them all
Right-click, or / or ? on an atom or bond, for its menu. Right-click a selection to copy or annotate it.
The wheel zooms. The middle button pans. Annotations are kept in the SMILES as CXSMILES.

Anywhere     Ctrl-Z undo   Ctrl-Y redo   Ctrl-A select all   Ctrl-K clean up   Tab next   Esc back out
             F5 actual size   F6 fit   F7 F8 ] [ zoom   F10 menus   Ctrl-Q quit and print the SMILES"""
MAX_PROMPT = annotate.MAX_NOTE
# what a line of text typed at the bottom is for: a note, or one of the properties that take a value
PROMPTS = ("note", "isotope", "rgroup", "list", "link", "bracket", "stereo", "logic", "fragment")


class StateError(ValueError):
    """The client sent a drawing that can't have come from here."""


# The drawing as text


def new_state():
    return {"atoms": [], "bonds": [], "next": 1, "tool": "bond", "bond": SINGLE, "ring": "benzene",
            "view": [0.0, 0.0, DEFAULT_ZOOM], "focus": None, "hover": None, "last": None, "selected": [], "pointer": None,
            "press": None, "drag": None, "undo": [], "redo": [], "entry": None, "label": None, "help": False, "message": "",
            "menu": None, "menu_target": None, "menu_hover": None, "tip": None, "button": None, "cover": False, "shown": {}, "flips": {}, "size": None,
            "partial": "", "notes": annotate.empty(), "prompt": None, "rlogic": {}, "strip": True}


def encode(state):
    packed = zlib.compress(json.dumps(state, separators=(",", ":")).encode(), 6)
    return base64.urlsafe_b64encode(packed).decode()


def decode(text, check=None):
    """The state from its text, checked by `check`: a drawing's own checks unless a host keeps a drawing inside its state."""
    if len(text) > MAX_STATE:
        raise StateError("The drawing is too large.")
    try:
        unpacker = zlib.decompressobj()
        raw = unpacker.decompress(base64.urlsafe_b64decode(text), MAX_STATE_JSON)
        if unpacker.unconsumed_tail:
            raise StateError("The drawing is too large.")
        state = json.loads(raw)
        (check or check_state)(state)
    except StateError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, zlib.error, RecursionError, RuntimeError) as error:
        raise StateError("That is not a drawing from this editor.") from error
    return state


def _number(value, limit=1e6):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
        raise StateError("A number in the drawing is out of range.")


def _integer(value, limit=10**9):
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > limit:
        raise StateError("A number in the drawing is out of range.")


def _small(value, low, high):
    return type(value) is int and low <= value <= high


def _check_atom_extras(element, extras):
    """An atom's properties: each known, in range, and fitting what the atom is."""
    if not isinstance(extras, dict) or not extras:
        raise StateError("An atom in the drawing is not valid.")
    real = element in _SYMBOLS
    for key, value in extras.items():
        ok = {"iso": lambda: real and _small(value, 1, 300),
              "rad": lambda: real and value in (1, 2, 3) and type(value) is int,
              "r": lambda: element == "R#" and _small(value, 1, markush.MAX_RGROUP),
              "list": lambda: element == "L" and isinstance(value, list) and 0 < len(value) <= MAX_LIST
                              and all(v in _SYMBOLS for v in value) and len(set(value)) == len(value),
              "not": lambda: element == "L" and value is True,
              "ap": lambda: value in (1, 2, 3) and type(value) is int,
              "rg": lambda: _small(value, 1, markush.MAX_RGROUP),
              "st": lambda: isinstance(value, str) and bool(_STEREO_GROUP.fullmatch(value)),
              "link": lambda: isinstance(value, list) and len(value) == 2 and _small(value[0], 1, 20) and _small(value[1], 1, 20)
                              and value[0] < value[1],
              "sru": lambda: isinstance(value, list) and len(value) == 2 and _small(value[0], 1, MAX_BRACKET)
                             and isinstance(value[1], str) and bool(_BRACKET_LABEL.fullmatch(value[1]))}.get(key)
        if ok is None or not ok():
            raise StateError("An atom in the drawing is not valid.")
    if (element == "R#") != ("r" in extras) or (element == "L") != ("list" in extras):
        raise StateError("An atom in the drawing is not valid.")


def _check_structure(atoms, bonds, next_id):
    if not isinstance(atoms, list) or not isinstance(bonds, list) or len(atoms) > MAX_ATOMS or len(bonds) > MAX_BONDS:
        raise StateError("The drawing is too large.")
    ids = set()
    for atom in atoms:
        if not isinstance(atom, list) or len(atom) not in (5, 6):
            raise StateError("An atom in the drawing is not valid.")
        ident, element, x, y, charge = atom[:5]
        _integer(ident)
        _number(x)
        _number(y)
        _integer(charge, 8)
        if not isinstance(element, str) or (element not in _SYMBOLS and element not in MARKUSH) or ident in ids:
            raise StateError("An atom in the drawing is not valid.")
        if element in ("R#", "L"):
            if len(atom) == 5:
                raise StateError("An atom in the drawing is not valid.")
        if len(atom) == 6:
            _check_atom_extras(element, atom[5])
        ids.add(ident)
    pairs = set()
    for bond in bonds:
        if not isinstance(bond, list) or len(bond) not in (3, 4):
            raise StateError("A bond in the drawing is not valid.")
        begin, end, kind = bond[:3]
        pair = frozenset((begin, end))
        if begin not in ids or end not in ids or begin == end or kind not in _ORDER or pair in pairs or isinstance(kind, bool):
            raise StateError("A bond in the drawing is not valid.")
        if len(bond) == 4:
            extras = bond[3]
            if not isinstance(extras, dict) or not extras or set(extras) - {"ends", "topo"}:
                raise StateError("A bond in the drawing is not valid.")
            ends = extras.get("ends")
            if "ends" in extras and (not isinstance(ends, list) or not 2 <= len(ends) <= MAX_LIST or len(set(ends)) != len(ends)
                                     or end not in ends or begin in ends or not all(type(i) is int and i in ids for i in ends)):
                raise StateError("A bond in the drawing is not valid.")
            if extras.get("topo", "ring") not in ("ring", "chain"):
                raise StateError("A bond in the drawing is not valid.")
        pairs.add(pair)
    _integer(next_id)
    if ids and next_id <= max(ids):
        raise StateError("An atom in the drawing is not valid.")


def _check_logic(logic):
    """R-group logic, by R number as text: {"1": {"then": 0 or another R number, "rest_h": bool, "occurrence": "" or as >0, 1-3}}."""
    if not isinstance(logic, dict) or len(logic) > markush.MAX_RGROUP:
        raise StateError("The R-group logic is not valid.")
    for key, rule in logic.items():
        if (not isinstance(key, str) or not key.isdigit() or not 1 <= int(key) <= markush.MAX_RGROUP or not isinstance(rule, dict)
                or set(rule) != {"then", "rest_h", "occurrence"} or not _small(rule["then"], 0, markush.MAX_RGROUP)
                or rule["rest_h"] not in (True, False) or not isinstance(rule["occurrence"], str) or len(rule["occurrence"]) > 20
                or not markush.valid_occurrence(rule["occurrence"]) or rule["then"] == int(key)):
            raise StateError("The R-group logic is not valid.")


def _check_target(target):
    if target is None:
        return
    if not isinstance(target, list) or target[0] not in ("a", "b") or len(target) != (2 if target[0] == "a" else 3):
        raise StateError("A choice in the drawing is not valid.")
    for ident in target[1:]:
        _integer(ident)


def _check_flag(value):
    if value is not True and value is not False:
        raise StateError("A choice in the drawing is not valid.")


def _check_point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise StateError("A choice in the drawing is not valid.")
    _number(value[0])
    _number(value[1])


def _check_press(press):
    for name in ("cell", "model", "last"):
        _check_point(press[name])
    if not isinstance(press["view"], list) or len(press["view"]) != 3:
        raise StateError("A choice in the drawing is not valid.")
    for value in press["view"]:
        _number(value)
    _check_target(press["target"])
    _check_flag(press["moved"])
    if press["mode"] not in PRESS_MODES:
        raise StateError("A choice in the drawing is not valid.")
    _check_ids(press["ids"])


def _check_ids(ids):
    if not isinstance(ids, list) or len(ids) > MAX_ATOMS:
        raise StateError("A choice in the drawing is not valid.")
    for ident in ids:
        _integer(ident)


def _check_menu(menu):
    if menu is None:
        return
    if not isinstance(menu, list) or not menu:
        raise StateError("A menu in the drawing is not valid.")
    if menu[0] == "bar" and len(menu) == 2:
        _integer(menu[1])
        if not 0 <= menu[1] < len(MENUS):
            raise StateError("A menu in the drawing is not valid.")
    elif menu[0] == "context" and len(menu) == 4 and menu[1] in CONTEXT_MENUS:
        _integer(menu[2], 1000)
        _integer(menu[3], 1000)
    else:
        raise StateError("A menu in the drawing is not valid.")


def _check_notes(notes, atoms, bonds):
    """Annotations by editor id, on atoms and bonds that are there."""
    try:
        annotate.check(notes)
    except annotate.NoteError as error:
        raise StateError(str(error)) from error
    ids, pairs = {a[0] for a in atoms}, {tuple(sorted(b[:2])) for b in bonds}
    if not all(entry[0] in ids for entry in notes["atoms"]) or not all(tuple(entry[:2]) in pairs for entry in notes["bonds"]):
        raise StateError("An annotation in the drawing is not valid.")


def check_state(s):
    s.pop("clipboard", None)  # only ever made here, and printed as it is
    _check_structure(s["atoms"], s["bonds"], s["next"])
    _check_notes(s["notes"], s["atoms"], s["bonds"])
    _check_logic(s["rlogic"])
    if s["tool"] not in TOOLS or s["bond"] not in _ORDER or isinstance(s["bond"], bool) or s["bond"] in QUERY_BONDS:
        raise StateError("A tool in the drawing is not valid.")
    if s["ring"] not in RINGS or isinstance(s["ring"], bool):
        raise StateError("A tool in the drawing is not valid.")
    x, y, zoom = s["view"]
    _number(x)
    _number(y)
    _number(zoom)
    if not ZOOM_RANGE[0] <= zoom <= ZOOM_RANGE[1]:
        raise StateError("A number in the drawing is out of range.")
    for name in ("focus", "hover", "last", "menu_target"):
        _check_target(s[name])
    _check_ids(s["selected"])
    for stack in (s["undo"], s["redo"]):
        if not isinstance(stack, list) or len(stack) > MAX_UNDO:
            raise StateError("The drawing is too large.")
        for entry in stack:
            if not isinstance(entry, list) or len(entry) != 5:
                raise StateError("The drawing is not valid.")
            atoms, bonds, next_id, notes, logic = entry
            _check_structure(atoms, bonds, next_id)
            _check_notes(notes, atoms, bonds)
            _check_logic(logic)
    if s["press"] is not None:
        _check_press(s["press"])
    for name in ("drag", "pointer"):
        if s[name] is not None:
            _check_point(s[name])
    if s["entry"] is not None and (not isinstance(s["entry"], str) or len(s["entry"]) > MAX_ENTRY):
        raise StateError("The text in the drawing is too long.")
    if s["prompt"] is not None:
        prompt = s["prompt"]
        if not isinstance(prompt["targets"], list) or not prompt["targets"] or len(prompt["targets"]) > MAX_ATOMS + MAX_BONDS:
            raise StateError("A choice in the drawing is not valid.")
        for target in prompt["targets"]:
            _check_target(target)
        if not isinstance(prompt["text"], str) or len(prompt["text"]) > MAX_PROMPT:
            raise StateError("The text in the drawing is too long.")
        if prompt.get("kind", "note") not in PROMPTS:
            raise StateError("A choice in the drawing is not valid.")
    if s["label"] is not None:
        label = s["label"]
        _check_target(label["target"])
        _check_point(label["at"])
        if not isinstance(label["text"], str) or len(label["text"]) > MAX_LABEL:
            raise StateError("The text in the drawing is too long.")
    _check_flag(s["help"])
    _check_flag(s["cover"])
    if not isinstance(s["message"], str):
        raise StateError("A choice in the drawing is not valid.")
    _check_menu(s["menu"])
    for name in ("menu_hover", "tip"):
        if s[name] is not None:
            _integer(s[name], 1000)
    _check_flag(s.setdefault("strip", True))
    canvas = s.setdefault("canvas", None)
    if canvas is not None and (not isinstance(canvas, list) or len(canvas) != 4):
        raise StateError("A choice in the drawing is not valid.")
    for number in canvas or ():
        _integer(number, 10_000)
    if s["button"] not in (None, "done", "cancel", "copy", "tools", *dict(WINDOW_BUTTONS)):
        raise StateError("A choice in the drawing is not valid.")
    if not isinstance(s["shown"], dict) or not all(k in LAYERS and isinstance(v, str) and len(v) <= 64 for k, v in s["shown"].items()):
        raise StateError("A choice in the drawing is not valid.")
    if not isinstance(s["flips"], dict) or not all(k in LAYERS and v in (0, 1) and not isinstance(v, bool) for k, v in s["flips"].items()):
        raise StateError("A choice in the drawing is not valid.")
    if s["size"] is not None and not isinstance(s["size"], str):
        raise StateError("A choice in the drawing is not valid.")
    if not isinstance(s["partial"], str) or len(s["partial"]) > 64 or not s["partial"].isascii():
        raise StateError("A choice in the drawing is not valid.")


# The structure


def _atom(s, ident):
    return next((a for a in s["atoms"] if a[0] == ident), None)


def _bond(s, first, second):
    return next((b for b in s["bonds"] if {b[0], b[1]} == {first, second}), None)


def _neighbours(s, ident):
    return [b[1] if b[0] == ident else b[0] for b in s["bonds"] if ident in b[:2]]


def _extras(atom):
    return atom[5] if len(atom) > 5 else {}


def _bond_extras(bond):
    return bond[3] if len(bond) > 3 else {}


def _set_extra(atom, key, value):
    """Set or, with None, remove one of an atom's properties, keeping the atom without a dict when it has none."""
    extras = dict(_extras(atom))
    if value is None:
        extras.pop(key, None)
    else:
        extras[key] = value
    del atom[5:]
    if extras:
        atom.append(extras)


def _set_bond_extra(bond, key, value):
    extras = dict(_bond_extras(bond))
    if value is None:
        extras.pop(key, None)
    else:
        extras[key] = value
    del bond[3:]
    if extras:
        bond.append(extras)


def _relabel(atom, symbol, charge=None):
    """Make an atom an element or a Markush atom, dropping the properties that no longer fit it."""
    atom[1] = symbol
    if charge is not None:
        atom[4] = charge
    if symbol not in _SYMBOLS:
        atom[4] = 0
        for key in ("iso", "rad"):
            _set_extra(atom, key, None)
    if symbol != "R#":
        _set_extra(atom, "r", None)
    if symbol != "L":
        _set_extra(atom, "list", None)
        _set_extra(atom, "not", None)


def _symbol(atom):
    """How an atom is written for people: its element, R1, [C,N,O], ![F,Cl], Q and so on."""
    extras = _extras(atom)
    if atom[1] == "R#":
        return f"R{extras['r']}"
    if atom[1] == "L":
        return ("!" if extras.get("not") else "") + "[" + ",".join(extras["list"]) + "]"
    return (str(extras["iso"]) if "iso" in extras else "") + atom[1]


def _bond_ends(s, bond):
    """Where a bond is drawn: between its atoms, or for a variable attachment from the substituent to the middle of the atoms it may go to."""
    first = _atom(s, bond[0])
    ends = _bond_extras(bond).get("ends")
    if ends:
        points = [_atom(s, i) for i in ends if _atom(s, i)]
        return (first[2], first[3]), (sum(a[2] for a in points) / len(points), sum(a[3] for a in points) / len(points))
    second = _atom(s, bond[1])
    return (first[2], first[3]), (second[2], second[3])


def _save(s):
    """Remember the structure for undo. Call this before changing it."""
    s["undo"] = (s["undo"] + [json.loads(json.dumps([s["atoms"], s["bonds"], s["next"], s["notes"], s["rlogic"]]))])[-MAX_UNDO:]
    s["redo"] = []


def _restore(s, source, target):
    if not s[source]:
        s["message"] = f"Nothing to {source}."
        return
    s[target] = (s[target] + [[s["atoms"], s["bonds"], s["next"], s["notes"], s["rlogic"]]])[-MAX_UNDO:]
    s["atoms"], s["bonds"], s["next"], s["notes"], s["rlogic"] = s[source].pop()
    _forget_missing(s)


def _forget_missing(s):
    for name in ("focus", "hover", "last", "menu_target"):
        target = s[name]
        if target and ((target[0] == "a" and not _atom(s, target[1])) or (target[0] == "b" and not _bond(s, target[1], target[2]))):
            s[name] = None
    s["selected"] = [ident for ident in s["selected"] if _atom(s, ident)]
    ids, pairs = {a[0] for a in s["atoms"]}, {tuple(sorted(b[:2])) for b in s["bonds"]}
    notes = s["notes"]
    notes["atoms"] = [entry for entry in notes["atoms"] if entry[0] in ids]
    notes["bonds"] = [entry for entry in notes["bonds"] if tuple(entry[:2]) in pairs]
    for bond in s["bonds"]:
        ends = _bond_extras(bond).get("ends")
        if ends and len([i for i in ends if i in ids]) != len(ends):
            kept = [i for i in ends if i in ids]
            _set_bond_extra(bond, "ends", kept if len(kept) >= 2 else None)


def _add_atom(s, element, x, y):
    if len(s["atoms"]) >= MAX_ATOMS:
        s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
        return None
    ident = s["next"]
    s["next"] += 1
    s["atoms"].append([ident, element, x, y, 0])
    return ident


def _set_bond(s, first, second, kind):
    bond = _bond(s, first, second)
    if bond:
        if kind in (WEDGE, HASH):
            bond[0], bond[1] = first, second  # a wedge starts narrow at the first atom
        bond[2] = kind
    elif len(s["bonds"]) < MAX_BONDS:
        s["bonds"].append([first, second, kind])


def _delete_target(s, target):
    if target is None:
        return False
    if target[0] == "a":
        if not _atom(s, target[1]):
            return False
        _save(s)
        beside = _neighbours(s, target[1])
        s["atoms"] = [a for a in s["atoms"] if a[0] != target[1]]
        s["bonds"] = [b for b in s["bonds"] if target[1] not in b[:2]]
        if s["focus"] == target:
            s["focus"] = ["a", beside[0]] if beside else None
    else:
        if not _bond(s, target[1], target[2]):
            return False
        _save(s)
        s["bonds"] = [b for b in s["bonds"] if {b[0], b[1]} != {target[1], target[2]}]
    _forget_missing(s)
    return True


def _delete_selection(s):
    gone = set(s["selected"])
    _save(s)
    s["atoms"] = [a for a in s["atoms"] if a[0] not in gone]
    s["bonds"] = [b for b in s["bonds"] if b[0] not in gone and b[1] not in gone]
    s["selected"] = []
    _forget_missing(s)


def _brackets(atoms, bonds, members):
    """Where a repeating unit's brackets go, in molfile units: one across each bond out of it, or either side of it if none leaves it."""
    position = {a[0]: (a[2] * 1.5, a[3] * 1.5) for a in atoms}
    found = []
    for bond in bonds:
        inside = [i in members for i in bond[:2]]
        if inside[0] == inside[1]:
            continue
        (x0, y0), (x1, y1) = (position[bond[0]], position[bond[1]]) if inside[0] else (position[bond[1]], position[bond[0]])
        mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2
        length = math.hypot(x1 - x0, y1 - y0) or 1
        across_x, across_y = -(y1 - y0) / length * 0.5, (x1 - x0) / length * 0.5
        found.append((mid_x + across_x, mid_y + across_y, mid_x - across_x, mid_y - across_y))
    if not found:
        xs, ys = [position[i][0] for i in members], [position[i][1] for i in members]
        low, high = min(ys) - 0.6, max(ys) + 0.6
        found = [(min(xs) - 0.6, high, min(xs) - 0.6, low), (max(xs) + 0.6, low, max(xs) + 0.6, high)]
    return found


def to_molblock(atoms, bonds):
    """The drawing as a V3000 molfile, which holds everything it can have: queries, R-groups, attachment points, repeating units, link
    nodes, variable attachments and enhanced stereo. Each variable attachment adds a star atom after the drawing's own, as molfiles have
    one, in the middle of the atoms it may go to."""
    index = {a[0]: i + 1 for i, a in enumerate(atoms)}
    lines, bond_lines, stars = [], [], []
    for number, atom in enumerate(atoms, 1):
        ident, element, x, y, charge = atom[:5]
        extras = _extras(atom)
        if element == "R#":
            symbol = "R#"
        elif element == "L":
            symbol = ("NOT" if extras.get("not") else "") + "[" + ",".join(extras["list"]) + "]"
        else:
            symbol = element
        fields = [f"M  V30 {number} {symbol} {x * 1.5:.4f} {y * 1.5:.4f} 0 0"]
        if charge:
            fields.append(f"CHG={charge}")
        if "iso" in extras:
            fields.append(f"MASS={extras['iso']}")
        if "rad" in extras:
            fields.append(f"RAD={extras['rad']}")
        if element == "R#":
            fields.append(f"RGROUPS=(1 {extras['r']})")
        if "ap" in extras:
            fields.append(f"ATTCHPT={-1 if extras['ap'] == 3 else extras['ap']}")
        lines.append(" ".join(fields))
    for bond in bonds:
        begin, end, kind = bond[:3]
        extras = _bond_extras(bond)
        fields = [f"{_ORDER[kind]}"]
        ends = extras.get("ends")
        if ends:
            points = [a for a in atoms if a[0] in ends]
            star = len(atoms) + len(stars) + 1
            stars.append(f"M  V30 {star} * {sum(a[2] for a in points) / len(points) * 1.5:.4f} {sum(a[3] for a in points) / len(points) * 1.5:.4f} 0 0")
            fields += [str(index[begin]), str(star), f"ENDPTS=({len(ends)} {' '.join(str(index[i]) for i in ends)})", "ATTACH=ANY"]
        else:
            fields += [str(index[begin]), str(index[end])]
        if kind in _CFG:
            fields.append(f"CFG={_CFG[kind]}")
        if "topo" in extras:
            fields.append(f"TOPO={1 if extras['topo'] == 'ring' else 2}")
        bond_lines.append(f"M  V30 {len(bond_lines) + 1} " + " ".join(fields))
    groups = {}
    for atom in atoms:
        if "sru" in _extras(atom):
            groups.setdefault(_extras(atom)["sru"][0], (_extras(atom)["sru"][1], []))[1].append(atom[0])
    sgroups = []
    numbered = {tuple(sorted(b[:2])): n for n, b in enumerate(bonds, 1)}
    for number, (_, (label, members)) in enumerate(sorted(groups.items()), 1):
        chosen = set(members)
        crossing = [numbered[tuple(sorted(b[:2]))] for b in bonds if (b[0] in chosen) != (b[1] in chosen) and not _bond_extras(b).get("ends")]
        fields = [f"M  V30 {number} SRU 0 ATOMS=({len(members)} {' '.join(str(index[i]) for i in members)})"]
        if crossing:
            fields.append(f"XBONDS=({len(crossing)} {' '.join(map(str, crossing))})")
        fields.append(f"LABEL={label}")
        fields += [f"BRKXYZ=(9 {x0:.4f} {y0:.4f} 0 {x1:.4f} {y1:.4f} 0 0 0 0)" for x0, y0, x1, y1 in _brackets(atoms, bonds, chosen)]
        sgroups.append(" ".join(fields))
    links = []
    for atom in atoms:
        link = _extras(atom).get("link")
        around = [b for b in bonds if atom[0] in b[:2]]
        if link and len(around) == 2:
            pairs = " ".join(f"{index[atom[0]]} {index[b[1] if b[0] == atom[0] else b[0]]}" for b in around)
            links.append(f"M  V30 LINKNODE {link[0]} {link[1]} 2 {pairs}")
    stereo = {}
    for atom in atoms:
        group = _extras(atom).get("st")
        if group:
            stereo.setdefault(group, []).append(index[atom[0]])
    names = {"abs": "MDLV30/STEABS"}
    collection = [f"M  V30 {names.get(group) or ('MDLV30/STERAC' + group[3:] if group.startswith('and') else 'MDLV30/STEREL' + group[2:])} "
                  f"ATOMS=({len(members)} {' '.join(map(str, members))})" for group, members in sorted(stereo.items())]
    out = ["", "  molterm", "", "  0  0  0     0  0            999 V3000", "M  V30 BEGIN CTAB",
           f"M  V30 COUNTS {len(atoms) + len(stars)} {len(bonds)} {len(sgroups)} 0 0", "M  V30 BEGIN ATOM", *lines, *stars, "M  V30 END ATOM"]
    if bond_lines:
        out += ["M  V30 BEGIN BOND", *bond_lines, "M  V30 END BOND"]
    if sgroups:
        out += ["M  V30 BEGIN SGROUP", *sgroups, "M  V30 END SGROUP"]
    out += links
    if collection:
        out += ["M  V30 BEGIN COLLECTION", *collection, "M  V30 END COLLECTION"]
    return "\n".join(out + ["M  V30 END CTAB", "M  END", ""])


def _mark(mol, atoms, bonds):
    """Keep the editor's ids and properties on RDKit's atoms and bonds, for a round trip through RDKit such as Clean Up, and give the
    Markush atoms the labels RDKit draws and writes. The star atoms of variable attachments come after the drawing's atoms."""
    for atom, entry in zip(mol.GetAtoms(), atoms):
        atom.SetIntProp("molterm_id", entry[0])
        atom.SetProp("molterm_atom", json.dumps([entry[1], _extras(entry)]))
        if entry[1] == "R#":
            atom.SetIsotope(0)
            atom.SetProp("_displayLabel", f"R{_extras(entry)['r']}")
        elif entry[1] in markush.GENERICS:
            atom.SetProp("_displayLabel", entry[1])
    varying = [b for b in bonds if _bond_extras(b).get("ends")]
    for star, bond in zip(list(mol.GetAtoms())[len(atoms):], varying):
        star.SetProp("molterm_star", json.dumps(bond))
    for bond, entry in zip(mol.GetBonds(), bonds):
        bond.SetProp("molterm_bond", json.dumps(entry))
    return mol


def _raw_mol(s):
    """The drawing as an RDKit molecule, whether or not it is valid chemistry."""
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]), sanitize=False, removeHs=False)
    mol.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(mol)
    return _mark(mol, s["atoms"], s["bonds"])


def _is_query(atoms, bonds):
    """Whether only SMARTS can say it: an atom list, or a query bond."""
    return any(a[1] == "L" for a in atoms) or any(b[2] in QUERY_BONDS or "topo" in _bond_extras(b) for b in bonds)


def _core(s, chosen=None):
    """The atoms and bonds of the structure itself, without its R-group definitions: all of it, or only `chosen` ids."""
    atoms = [a for a in s["atoms"] if (chosen is None or a[0] in chosen) and "rg" not in _extras(a)]
    ids = {a[0] for a in atoms}
    bonds = [b for b in s["bonds"] if b[0] in ids and b[1] in ids and all(i in ids for i in _bond_extras(b).get("ends", []))]
    return atoms, bonds


def analyse(s):
    """Return (what Done would write, without the annotations, or None; what's wrong or None; indices of atoms that are wrong)."""
    if not s["atoms"]:
        return "", None, []
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]))
        if mol is not None:
            found = written(s)
            if found:
                return found[0], None, []
            return None, "RDKit can't write this structure.", []
        problems = Chem.DetectChemistryProblems(Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]), sanitize=False, removeHs=False))
    atoms, text = [], "RDKit can't read this structure."
    for problem in problems:
        text = problem.Message()
        if hasattr(problem, "GetAtomIdx"):
            atoms.append(problem.GetAtomIdx())
        elif hasattr(problem, "GetAtomIndices"):
            atoms.extend(problem.GetAtomIndices())
    return None, text, atoms


def _mol_with_ids(atoms, bonds):
    """The atoms and bonds as a valid molecule without explicit hydrogens, each atom keeping its id as molterm_id, or None."""
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(to_molblock(atoms, bonds), removeHs=False)
        if mol is None:
            return None
        _mark(mol, atoms, bonds)
        try:
            return Chem.RemoveHs(mol)
        except Chem.MolSanitizeException:
            return mol


def _by_index(notes, index):
    """Notes keyed by editor id to notes keyed by index, through {id: index}, leaving out what isn't there."""
    return annotate.renumber(notes, index)


_CX_FIELDS = (Chem.CXSmilesFields.CX_ATOM_LABELS | Chem.CXSmilesFields.CX_ENHANCEDSTEREO | Chem.CXSmilesFields.CX_RADICALS
              | Chem.CXSmilesFields.CX_SGROUPS | Chem.CXSmilesFields.CX_POLYMER | Chem.CXSmilesFields.CX_LINKNODES
              | Chem.CXSmilesFields.CX_COORDINATE_BONDS)


def _cx_text(mol, query):
    """The molecule as CXSMILES, or for a query CXSMARTS, with its atoms' order as written. The Markush atoms carry ChemAxon's labels."""
    mol = Chem.Mol(mol)
    for atom in mol.GetAtoms():
        if atom.HasProp("molterm_atom"):
            element, extras = json.loads(atom.GetProp("molterm_atom"))
            if element == "R#":
                atom.SetProp("atomLabel", f"_R{extras['r']}")
            elif element in markush.GENERICS:
                atom.SetProp("atomLabel", markush.GENERICS[element][0])
        elif atom.HasProp("molterm_star"):
            atom.SetProp("atomLabel", "star_e")
    if query:
        text = Chem.MolToCXSmarts(mol)
    else:
        text = Chem.MolToCXSmiles(mol, Chem.SmilesWriteParams(), _CX_FIELDS)
        Chem.MolToSmiles(mol)  # which numbers the atoms in the same order, and says so, as the CXSMILES writer doesn't
    order = list(mol.GetPropsAsDict(True, True)["_smilesAtomOutputOrder"])
    smiles, fields = markush.split(text)
    # RDKit's own: coordinates and its atom properties, which a SMARTS gets, aren't wanted
    fields = [f for f in fields if not f.startswith(("(", "atomProp:"))]
    return smiles, fields, order


def written(s, selection=False):
    """(the text, the molecule in its atom order with 2D coordinates, its notes by that order), for the drawing or only the selected
    atoms and the bonds between them; or None if that isn't valid chemistry. The text is CXSMILES, or CXSMARTS for a query, with the
    R-group definitions after the core."""
    chosen = set(s["selected"]) if selection else None
    atoms, bonds = _core(s, chosen)
    if not atoms:
        return None
    mol = _mol_with_ids(atoms, bonds)
    if mol is None:
        return None
    pointed = [(atom.GetIdx(), json.loads(atom.GetProp("molterm_atom"))[1].get("ap")) for atom in mol.GetAtoms()
               if atom.HasProp("molterm_atom") and json.loads(atom.GetProp("molterm_atom"))[1].get("ap")]
    if pointed:  # attachment points outside an R-group definition, as ChemAxon writes them: a star on each, labelled _AP1 or _AP2
        editable = Chem.RWMol(mol)
        for index, point in pointed:
            for number in ((1, 2) if point == 3 else (point,)):
                star = editable.AddAtom(Chem.Atom(0))
                editable.AddBond(index, star, Chem.BondType.SINGLE)
                editable.GetAtomWithIdx(star).SetProp("atomLabel", f"_AP{number}")
        mol = editable.GetMol()
        mol.UpdatePropertyCache(strict=False)
    try:
        smiles, fields, order = _cx_text(mol, _is_query(atoms, bonds))
    except (RuntimeError, ValueError):
        return None
    mol = Chem.RenumberAtoms(mol, order)
    index = {atom.GetIntProp("molterm_id"): atom.GetIdx() for atom in mol.GetAtoms() if atom.HasProp("molterm_id")}
    varying = []
    for atom in mol.GetAtoms():
        if atom.HasProp("molterm_star"):
            ends = _bond_extras(json.loads(atom.GetProp("molterm_star")))["ends"]
            varying.append((atom.GetIdx(), [index[i] for i in ends if i in index]))
    fields.append(markush.position_variation(varying))
    if chosen is None:
        definitions = _definitions(s)
        if definitions is None:
            return None
        fields.append(markush.write_rgroups(definitions))
        fields.append(markush.write_logic({int(n): rule for n, rule in s["rlogic"].items()}))
    return markush.join(smiles, fields), mol, _by_index(s["notes"], index)


def _fragments(s):
    """The R-group definitions as drawn: {R number: [[atom ids of one definition], ...]}, each the connected atoms marked with it."""
    marked = {a[0]: _extras(a)["rg"] for a in s["atoms"] if "rg" in _extras(a)}
    found, seen = {}, set()
    for ident, number in marked.items():
        if ident in seen:
            continue
        members, todo = [], [ident]
        while todo:
            current = todo.pop()
            if current in seen or marked.get(current) != number:
                continue
            seen.add(current)
            members.append(current)
            todo.extend(_neighbours(s, current))
        found.setdefault(number, []).append(sorted(members))
    return found


def _definitions(s):
    """{R number: [the SMILES of each definition]}, or None if one isn't valid chemistry."""
    definitions = {}
    for number, groups in sorted(_fragments(s).items()):
        for members in groups:
            chosen = set(members)
            atoms = [a for a in s["atoms"] if a[0] in chosen]
            bonds = [b for b in s["bonds"] if b[0] in chosen and b[1] in chosen]
            mol = _mol_with_ids(atoms, bonds)
            if mol is None:
                return None
            index = {atom.GetIntProp("molterm_id"): atom.GetIdx() for atom in mol.GetAtoms()}
            first = next((index[a[0]] for a in atoms if _extras(a).get("ap") in (1, 3)), index[atoms[0][0]])
            second = next((index[a[0]] for a in atoms if _extras(a).get("ap") == 2), None)
            definitions.setdefault(number, []).append(markush.fragment_smiles(mol, first, second))
    return definitions


def annotated(s):
    """The drawing as CXSMILES, with its annotations if it has any; "" if it's empty, None if it's not valid."""
    if not s["atoms"]:
        return ""
    found = written(s)
    return annotate.write(found[0], found[2]) if found else None


def facts(smiles):
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return ""
    return f"{rdMolDescriptors.CalcMolFormula(mol)}   MW {Descriptors.MolWt(mol):.1f}   cLogP {Crippen.MolLogP(mol):.1f}"


_BOND_TYPES = {Chem.BondType.DOUBLE: DOUBLE, Chem.BondType.TRIPLE: TRIPLE, Chem.BondType.AROMATIC: AROMATIC, Chem.BondType.DATIVE: DATIVE}
_QUERY_BOND_SMARTS = {"~": ANY, "-,=": SINGLE_DOUBLE, "=,-": SINGLE_DOUBLE, "-,:": SINGLE_AROMATIC, ":,-": SINGLE_AROMATIC,
                      "=,:": DOUBLE_AROMATIC, ":,=": DOUBLE_AROMATIC, ":": AROMATIC, "-": SINGLE, "=": DOUBLE, "#": TRIPLE}
_GENERIC_SMARTS = {"*": "A", "[!#1]": "A", "[!#6&!#1]": "Q", "[!#6]": "QH", "[#9,#17,#35,#53,#85]": "X", "[#1,#9,#17,#35,#53,#85]": "XH"}


def _read_atom(atom):
    """(element, properties) for an RDKit atom read from SMILES, SMARTS or a molfile, or None for the star of a variable attachment."""
    if atom.HasProp("molterm_atom"):
        element, extras = json.loads(atom.GetProp("molterm_atom"))
        return element, dict(extras)
    extras = {}
    label = atom.GetProp("atomLabel") if atom.HasProp("atomLabel") else ""
    if atom.GetAtomicNum() == 0:
        if re.fullmatch(r"_R\d{1,2}", label):
            return "R#", {"r": int(label[2:])}
        if atom.HasProp("_MolFileRLabel") and 1 <= atom.GetIntProp("_MolFileRLabel") <= markush.MAX_RGROUP:
            return "R#", {"r": atom.GetIntProp("_MolFileRLabel")}
        if atom.HasProp("dummyLabel") and re.fullmatch(r"R\d{1,2}", atom.GetProp("dummyLabel")):
            return "R#", {"r": int(atom.GetProp("dummyLabel")[1:])}
        if label in markush.LABELS:
            return markush.LABELS[label], {}
    if atom.HasQuery():
        smarts = atom.GetSmarts()
        listed = re.fullmatch(r"\[(#\d+(?:,#\d+)+)\]", smarts)
        unlisted = re.fullmatch(r"\[(!#\d+(?:&!#\d+)*)\]", smarts)
        table = Chem.GetPeriodicTable()
        if smarts in _GENERIC_SMARTS:
            return _GENERIC_SMARTS[smarts], {}
        if listed or unlisted:
            numbers = re.findall(r"\d+", (listed or unlisted)[1])
            extras = {"list": [table.GetElementSymbol(int(n)) for n in numbers][:MAX_LIST]}
            if unlisted:
                extras["not"] = True
            return "L", extras
        if atom.GetAtomicNum() == 0:
            return markush.LABELS.get(label, "A"), {}
    if atom.GetAtomicNum() == 0:
        return "*", {}
    if atom.GetIsotope():
        extras["iso"] = atom.GetIsotope()
    if atom.GetNumRadicalElectrons():
        extras["rad"] = 2 if atom.GetNumRadicalElectrons() == 1 else 3
    if atom.HasProp("molAttchpt"):
        extras["ap"] = {1: 1, 2: 2, -1: 3}.get(atom.GetIntProp("molAttchpt"), 1)
    return atom.GetSymbol(), extras


def _read_bond(bond, wedges):
    if bond.HasProp("molterm_bond"):
        return json.loads(bond.GetProp("molterm_bond"))[2]
    if bond.HasQuery():
        return _QUERY_BOND_SMARTS.get(re.sub(r"&!?@", "", bond.GetSmarts()), SINGLE)
    kind = _BOND_TYPES.get(bond.GetBondType())
    if kind == DOUBLE and bond.GetStereo() == Chem.BondStereo.STEREOANY:
        return CROSSED
    if kind:
        return kind
    direction = wedges.get(bond.GetIdx())
    return {Chem.BondDir.BEGINWEDGE: WEDGE, Chem.BondDir.BEGINDASH: HASH, Chem.BondDir.UNKNOWN: WAVY}.get(direction, SINGLE)


def _from_mol(s, mol):
    """Replace the drawing with a molecule that already has 2D coordinates, scaled so that its bonds are about 1 long. It keeps what
    the molecule says of Markush atoms, queries, repeating units, link nodes, variable attachments and enhanced stereo."""
    plain = Chem.Mol(mol)
    conformer = plain.GetConformer()
    with rdBase.BlockLogs():
        try:
            Chem.WedgeMolBonds(plain, conformer)
        except (RuntimeError, ValueError):
            pass
    wedges = {b.GetIdx(): b.GetBondDir() for b in plain.GetBonds()}
    if not any(b.HasProp("molterm_bond") for b in plain.GetBonds()):
        try:
            Chem.Kekulize(plain, clearAromaticFlags=True)
        except (Chem.KekulizeException, RuntimeError, ValueError):
            pass
    lengths = [(conformer.GetAtomPosition(b.GetBeginAtomIdx()) - conformer.GetAtomPosition(b.GetEndAtomIdx())).Length()
               for b in plain.GetBonds()]
    scale = (sum(lengths) / len(lengths)) if lengths and sum(lengths) else 1.0
    atoms, stars = [], {}
    for atom in plain.GetAtoms():
        point = conformer.GetAtomPosition(atom.GetIdx())
        if atom.HasProp("molterm_star"):
            stars[atom.GetIdx()] = json.loads(atom.GetProp("molterm_star"))
            continue
        varying = [b for b in atom.GetBonds() if b.HasProp("_MolFileBondEndPts")]
        if atom.GetAtomicNum() == 0 and varying and not atom.HasProp("molterm_atom"):
            stars[atom.GetIdx()] = None
            continue
        point_label = re.fullmatch(r"_AP([12])", atom.GetProp("atomLabel")) if atom.HasProp("atomLabel") else None
        if atom.GetAtomicNum() == 0 and point_label and atom.GetDegree() == 1:  # ChemAxon's attachment point: a star on the atom it marks
            stars[atom.GetIdx()] = ("ap", int(point_label[1]), atom.GetNeighbors()[0].GetIdx() + 1)
            continue
        element, extras = _read_atom(atom)
        entry = [atom.GetIdx() + 1, element, round(point.x / scale, 4), round(point.y / scale, 4), atom.GetFormalCharge() if element in _SYMBOLS else 0]
        if extras:
            entry.append(extras)
        atoms.append(entry)
    ids = {a[0] for a in atoms}
    # the editor's ids, by RDKit's numbering, for what an earlier drawing's bonds say: as after Clean Up
    renamed = {atom.GetIntProp("molterm_id"): atom.GetIdx() + 1 for atom in plain.GetAtoms() if atom.HasProp("molterm_id")}
    bonds = []
    for bond in plain.GetBonds():
        first, second = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if first in stars or second in stars:
            star = first if first in stars else second
            other = second if star == first else first
            if isinstance(stars[star], tuple):
                continue
            if stars[star] is not None:  # from this editor, by way of Clean Up and the like
                kept = stars[star]
                ends = [renamed[i] for i in _bond_extras(kept)["ends"] if i in renamed]
                if len(ends) >= 2:
                    bonds.append([other + 1, ends[0] if renamed.get(kept[1]) not in ends else renamed[kept[1]], kept[2],
                                  {**_bond_extras(kept), "ends": ends}])
                continue
            ends = [int(i) for i in re.findall(r"\d+", bond.GetProp("_MolFileBondEndPts"))[1:]]
            ends = [i for i in ends if i in ids]  # numbered from 1, as the editor's ids are here
            if len(ends) >= 2:
                bonds.append([other + 1, ends[0], SINGLE, {"ends": ends[:MAX_LIST]}])
            continue
        kind = _read_bond(bond, wedges)
        entry = [first + 1, second + 1, kind]
        if bond.HasProp("molterm_bond"):
            kept = json.loads(bond.GetProp("molterm_bond"))
            entry = [first + 1, second + 1, kept[2], *kept[3:]]
            if kept[2] in DIRECTED and plain.GetAtomWithIdx(first).GetIntProp("molterm_id") != kept[0]:
                entry[:2] = entry[1::-1]
        elif re.search(r"&!?@", bond.GetSmarts()) and bond.HasQuery():
            entry.append({"topo": "chain" if "!@" in bond.GetSmarts() else "ring"})
        bonds.append(entry)
    for star in stars.values():
        if isinstance(star, tuple):
            entry = next((a for a in atoms if a[0] == star[2]), None)
            if entry:
                old = _extras(entry).get("ap", 0)
                _set_extra(entry, "ap", 3 if old and old != star[1] else star[1])
    # what RDKit keeps of a molecule beside its atoms: enhanced stereo, repeating units and link nodes
    for group in plain.GetStereoGroups():
        kind = group.GetGroupType()
        name = "abs" if kind == Chem.StereoGroupType.STEREO_ABSOLUTE else ("or" if kind == Chem.StereoGroupType.STEREO_OR else "and")
        if name != "abs":
            name += str(min(max(group.GetReadId() or 1, 1), 9))
        for atom in group.GetAtoms():
            entry = next((a for a in atoms if a[0] == atom.GetIdx() + 1), None)
            if entry and "st" not in _extras(entry):
                _set_extra(entry, "st", name)
    for number, group in enumerate(Chem.GetMolSubstanceGroups(plain), 1):
        if group.GetProp("TYPE") != "SRU" or number > MAX_BRACKET:
            continue
        label = group.GetProp("LABEL") if group.HasProp("LABEL") else "n"
        label = label if _BRACKET_LABEL.fullmatch(label) else "n"
        for index in group.GetAtoms():
            entry = next((a for a in atoms if a[0] == index + 1), None)
            if entry and "sru" not in _extras(entry):
                _set_extra(entry, "sru", [number, label])
    if plain.HasProp("_molLinkNodes"):
        for text in plain.GetProp("_molLinkNodes").split("|"):
            numbers = [int(n) for n in text.split()]
            if len(numbers) >= 4 and 1 <= numbers[0] < numbers[1] <= 20:
                entry = next((a for a in atoms if a[0] == numbers[3]), None)
                if entry:
                    _set_extra(entry, "link", numbers[:2])
    s["atoms"], s["bonds"], s["next"] = atoms, bonds, max(ids | {0}) + 1
    s["selected"] = []
    _forget_missing(s)
    s["last"] = None


def _add_definitions(s, definitions):
    """Draw R-group definitions to the right of the structure, one below another, each marked as its R-group's."""
    if not definitions:
        return True
    right = max((a[2] for a in s["atoms"]), default=0.0) + 3
    top = max((a[3] for a in s["atoms"]), default=0.0)
    for number, fragments in sorted(definitions.items()):
        for text in fragments:
            fragment, first, second = markush.fragment_mol(text)
            if len(s["atoms"]) + fragment.GetNumAtoms() > MAX_ATOMS:
                s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
                return False
            sub = new_state()
            _from_mol(sub, fragment)
            xs, ys = [a[2] for a in sub["atoms"]], [a[3] for a in sub["atoms"]]
            shift = {}
            for atom in sub["atoms"]:
                ident = s["next"]
                s["next"] += 1
                shift[atom[0]] = ident
                moved = [ident, atom[1], round(atom[2] - min(xs) + right, 4), round(atom[3] - max(ys) + top, 4), atom[4], *atom[5:]]
                _set_extra(moved, "rg", number)
                if atom[0] == first + 1:
                    _set_extra(moved, "ap", 3 if second == first else 1)
                elif second is not None and atom[0] == second + 1:
                    _set_extra(moved, "ap", 2)
                s["atoms"].append(moved)
            for bond in sub["bonds"]:
                s["bonds"].append([shift[bond[0]], shift[bond[1]], *bond[2:]])
            top -= max(ys) - min(ys) + 2
    return True


def load_smiles(s, text):
    """Replace the drawing with a SMILES, which may be a Markush structure or a query. Returns False and sets a message if it can't."""
    try:
        with rdBase.BlockLogs():
            parsed = molecule.parse(text, "smiles")
    except molecule.ParseError as error:
        s["message"] = error.message
        return False
    mol = parsed.core
    if mol.GetNumAtoms() > MAX_ATOMS or mol.GetNumBonds() > MAX_BONDS:
        s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
        return False
    saved = json.loads(json.dumps([s["atoms"], s["bonds"], s["next"], s["notes"], s["rlogic"]]))
    with rdBase.BlockLogs():
        before = Chem.MolToSmiles(mol)
    notes = annotate.read(mol)
    _from_mol(s, mol)
    s["notes"] = annotate.renumber(notes, {index: index + 1 for index in range(mol.GetNumAtoms())})
    s["rlogic"] = {str(n): rule for n, rule in parsed.logic.items()}
    s["focus"] = None
    try:
        made = _add_definitions(s, parsed.rgroups)
    except markush.MarkushError as error:
        s["message"], made = str(error), False
    if not made:
        s["atoms"], s["bonds"], s["next"], s["notes"], s["rlogic"] = saved
        return False
    if saved[0]:
        s["undo"] = (s["undo"] + [saved])[-MAX_UNDO:]
        s["redo"] = []
    atoms, bonds = _core(s)
    again = _mol_with_ids(atoms, bonds)
    with rdBase.BlockLogs():
        after = Chem.MolToSmiles(again) if again is not None else None
    pointed = any(a.HasProp("atomLabel") and a.GetProp("atomLabel").startswith("_AP") for a in mol.GetAtoms())
    if after != before and parsed.format != "smarts" and not pointed:
        s["message"] = "Some details, such as unusual bonds, aren't kept by this editor."
    return True


# Where new atoms go


def _straight_on(s, ident):
    """The direction along an end atom's only bond, from its neighbour through it: where a group or ring that takes the atom's place
    goes, so that the bond meets it at even angles rather than folded back."""
    atom, neighbour = _atom(s, ident), _atom(s, _neighbours(s, ident)[0])
    return math.atan2(atom[3] - neighbour[3], atom[2] - neighbour[2])


def _direction(s, ident, kind=SINGLE, cyclic=False):
    """The angle for a new bond from an atom: into the largest gap, and straight on beside a triple bond.

    At the end of a chain it turns away from the molecule, to make a zigzag, or if cyclic towards it, which curls round into a ring.
    """
    atom = _atom(s, ident)
    angles = sorted(math.atan2(_atom(s, n)[3] - atom[3], _atom(s, n)[2] - atom[2]) for n in _neighbours(s, ident))
    if not angles:
        return math.radians(30)
    if len(angles) == 1:
        back = angles[0]
        bond = _bond(s, ident, _neighbours(s, ident)[0])
        if kind == TRIPLE or bond[2] == TRIPLE:
            return back + math.pi

        def room(angle):
            x, y = atom[2] + math.cos(angle), atom[3] + math.sin(angle)
            return min((math.hypot(a[2] - x, a[3] - y) for a in s["atoms"] if a[0] != ident), default=9)
        return (min if cyclic else max)((back + math.radians(120), back - math.radians(120)), key=room)
    gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % (2 * math.pi) or 2 * math.pi for i in range(len(angles))]
    widest = gaps.index(max(gaps))
    return angles[widest] + gaps[widest] / 2


def _grow(s, ident, element, kind, angle=None, cyclic=False, save=True):
    """A new bond from an atom. Returns the atom at its far end, which a curling chain may find already there, or None if it is full."""
    angle = _direction(s, ident, kind, cyclic) if angle is None else angle
    atom = _atom(s, ident)
    if save:
        _save(s)
    x, y = atom[2] + math.cos(angle), atom[3] + math.sin(angle)
    new = _vertex(s, x, y) if cyclic else _add_atom(s, element, x, y)
    if new is None:
        if save:
            s["undo"].pop()
        return None
    _set_bond(s, ident, new, kind)
    s["focus"] = ["a", new]
    s["last"] = ["b", ident, new]
    return new


def _vertex(s, x, y):
    """The atom at a position, made if there isn't one yet."""
    for atom in s["atoms"]:
        if math.hypot(atom[2] - x, atom[3] - y) < MERGE:
            return atom[0]
    return _add_atom(s, "C", x, y)


def _add_ring(s, size, centre_x, centre_y, start_angle, shared=()):
    """Make a regular ring, reusing atoms already at its corners, with alternating double bonds if it is benzene."""
    atoms = len(s["atoms"])
    sides = _SIDES.get(size, size)
    if atoms + sides > MAX_ATOMS:
        s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
        return False
    radius = 1 / (2 * math.sin(math.pi / sides))
    corners = list(shared)
    for step in range(len(corners), sides):
        angle = start_angle + step * 2 * math.pi / sides
        corners.append(_vertex(s, centre_x + radius * math.cos(angle), centre_y + radius * math.sin(angle)))
    edges = [(corners[i], corners[(i + 1) % sides]) for i in range(sides)]
    for first, second in edges:
        if not _bond(s, first, second):
            _set_bond(s, first, second, SINGLE)
    if size in _DOUBLES:
        _alternate(s, edges, _DOUBLES[size])
    return True


def _alternate(s, edges, doubles):
    def has_double(ident, skip):
        return any(b[2] in (DOUBLE, TRIPLE) and ident in b[:2] and b is not skip for b in s["bonds"])

    def doubles_from(offset):
        return [edges[(offset + 2 * i) % len(edges)] for i in range(doubles)]

    def usable(edge):
        bond = _bond(s, *edge)
        return bond[2] == DOUBLE or (bond[2] == SINGLE and not has_double(edge[0], bond) and not has_double(edge[1], bond))

    best = max(range(len(edges)), key=lambda offset: sum(usable(edge) for edge in doubles_from(offset)))
    for edge in doubles_from(best):
        if usable(edge):
            _bond(s, *edge)[2] = DOUBLE


def _ring_on_target(s, target, size, x, y, sprout=False):
    """Add a ring: fused onto a bond, sharing an atom (a spiro link, if the atom is in a ring), joined to an atom by a new bond if sprouting,
    or free in an empty space."""
    sides = _SIDES.get(size, size)
    radius = 1 / (2 * math.sin(math.pi / sides))
    apothem = radius * math.cos(math.pi / sides)
    _save(s)
    made = False
    if target and target[0] == "b":
        a, b = _atom(s, target[1]), _atom(s, target[2])
        mid_x, mid_y = (a[2] + b[2]) / 2, (a[3] + b[3]) / 2
        normal_x, normal_y = -(b[3] - a[3]), b[2] - a[2]
        length = math.hypot(normal_x, normal_y) or 1
        normal_x, normal_y = normal_x / length, normal_y / length
        # the side with fewer atoms; by the pointer when it is even, as with a lone bond
        side = sum((atom[2] - mid_x) * normal_x + (atom[3] - mid_y) * normal_y for atom in s["atoms"])
        if abs(side) < 1e-6:
            side = (x - mid_x) * normal_x + (y - mid_y) * normal_y
        sign = -1 if side > 0 else 1
        centre_x, centre_y = mid_x + sign * apothem * normal_x, mid_y + sign * apothem * normal_y
        first, second = (a, b) if sign > 0 else (b, a)
        start = math.atan2(first[3] - centre_y, first[2] - centre_x)
        # corners run anticlockwise from the first shared atom, so the second comes next
        if abs(_angle_between(start + 2 * math.pi / sides, math.atan2(second[3] - centre_y, second[2] - centre_x))) > 0.01:
            first, second = second, first
            start = math.atan2(first[3] - centre_y, first[2] - centre_x)
        made = _add_ring(s, size, centre_x, centre_y, start, (first[0], second[0]))
    elif target and target[0] == "a":
        atom = _atom(s, target[1])
        angle = _direction(s, target[1])
        if sprout and _neighbours(s, target[1]):
            first_x, first_y = atom[2] + math.cos(angle), atom[3] + math.sin(angle)
            first = _vertex(s, first_x, first_y)
            if first is not None:
                _set_bond(s, target[1], first, SINGLE)
                made = _add_ring(s, size, first_x + radius * math.cos(angle), first_y + radius * math.sin(angle), angle + math.pi, (first,))
        else:
            if len(_neighbours(s, target[1])) == 1:  # the ring takes in the end of a chain: centred straight on, the chain between its sides
                angle = _straight_on(s, target[1])
            made = _add_ring(s, size, atom[2] + radius * math.cos(angle), atom[3] + radius * math.sin(angle), angle + math.pi, (target[1],))
    else:
        made = _add_ring(s, size, x, y, math.radians(90))
    if not made:
        s["undo"].pop()
    return made


def _angle_between(first, second):
    return (first - second + math.pi) % (2 * math.pi) - math.pi


# asking RDKit about a symbol it doesn't know writes a stack trace to the log
_SYMBOLS = frozenset(Chem.GetPeriodicTable().GetElementSymbol(number) for number in range(1, 119))


def _apply_label(s, target, text, x, y):
    """Turn what was typed in the label box into atoms. Returns False and sets a message if it means nothing here."""
    text = text.strip()

    def element(symbol):
        return symbol if symbol in _SYMBOLS else None

    atom = _atom(s, target[1]) if target and target[0] == "a" else None
    special = _special_label(s, text)
    if special:
        if isinstance(special, str):
            s["message"] = special
            return False
        symbol, extras = special
        _save(s)
        if not atom:
            new = _add_atom(s, "C", x, y)
            if new is None:
                s["undo"].pop()
                return False
            atom = _atom(s, new)
            s["focus"] = ["a", new]
        _relabel(atom, symbol)
        for key in ("iso", "r", "list", "not"):
            _set_extra(atom, key, extras.get(key))
        return True
    # the group first: in a drawing Ac, Pr and Ts are acetyl, propyl and tosyl, not actinium, praseodymium and tennessine
    group = GROUPS.get(text.lower())
    symbol = None if group else element(text)
    if not symbol and not group and text.islower():
        symbol = element(text.capitalize())
    if symbol:
        _save(s)
        if atom:
            _relabel(atom, symbol)
        else:
            new = _add_atom(s, symbol, x, y)
            if new is None:
                s["undo"].pop()
                return False
            s["focus"] = ["a", new]
        return True
    if not group:
        s["message"] = f"{_fit_text(text, 20)} isn't an element or a group this editor knows."
        return False
    _attach_group(s, atom, group, x, y)
    return True


def _free_rgroup(s):
    """The lowest R number not yet on an atom of the structure."""
    used = {_extras(a)["r"] for a in s["atoms"] if a[1] == "R#"}
    return next((n for n in range(1, markush.MAX_RGROUP + 1) if n not in used), 1)


def _parse_list(text):
    """C,N,O or [C,N,O] as (["C", "N", "O"], False), and !C,N, ![C,N] or NOT C,N as a NOT list; else a message, or None if it isn't one."""
    match = re.fullmatch(r"(!|not\s*)?\[?\s*([A-Za-z]{1,2}(?:\s*,\s*[A-Za-z]{1,2})*)\s*\]?", text.strip(), re.I)
    if not match or ("," not in match[2] and not match[1] and not text.strip().startswith("[")):
        return None
    symbols = [part.strip().capitalize() for part in match[2].split(",")]
    wrong = [symbol for symbol in symbols if symbol not in _SYMBOLS]
    if wrong:
        return f"{wrong[0]} isn't an element."
    if len(set(symbols)) > MAX_LIST:
        return f"A list holds at most {MAX_LIST} elements."
    return list(dict.fromkeys(symbols)), bool(match[1])


def _special_label(s, text):
    """What a label that isn't an element or a group means: (symbol, properties) for an R-group, a generic atom, an atom list or an
    isotope; a message if it's nearly one; or None."""
    if re.fullmatch(r"R", text):
        return "R#", {"r": _free_rgroup(s)}
    rgroup = re.fullmatch(r"R(\d{1,2})", text, re.I)
    if rgroup:
        number = int(rgroup[1])
        return ("R#", {"r": number}) if 1 <= number <= markush.MAX_RGROUP else f"R-groups go from R1 to R{markush.MAX_RGROUP}."
    if text in markush.GENERICS or text.upper() in ("AH", "QH", "XH", "MH"):
        return text.upper(), {}
    if text in ("D", "T"):
        return "H", {"iso": 2 if text == "D" else 3}
    isotope = re.fullmatch(r"(\d{1,3})([A-Z][a-z]?)", text)
    if isotope and isotope[2] in _SYMBOLS:
        return (isotope[2], {"iso": int(isotope[1])}) if 1 <= int(isotope[1]) <= 300 else "A mass number goes from 1 to 300."
    listed = _parse_list(text)
    if isinstance(listed, str):
        return listed
    if listed:
        return "L", {"list": listed[0], "not": True if listed[1] else None}
    return None


def _attach_group(s, atom, smiles, x, y):
    """Add a group laid out by RDKit, turned to point away from the rest of the molecule.

    It replaces an atom at the end of a chain, but an atom with two or more neighbours, such as one in a ring, keeps its place and
    gains the group as a substituent, since an oxygen with four bonds is never what was meant.
    """
    with rdBase.BlockLogs():
        fragment = Chem.MolFromSmiles(smiles)
    Chem.Kekulize(fragment, clearAromaticFlags=True)
    AllChem.Compute2DCoords(fragment)
    conformer = fragment.GetConformer()
    dummy = next(a for a in fragment.GetAtoms() if a.GetAtomicNum() == 0)
    first = dummy.GetNeighbors()[0]
    points = [conformer.GetAtomPosition(i) for i in range(fragment.GetNumAtoms())]
    if len(s["atoms"]) + fragment.GetNumAtoms() > MAX_ATOMS:
        s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
        return
    _save(s)
    outward = _direction(s, atom[0]) if atom else 0.0
    if atom and len(_neighbours(s, atom[0])) == 1:
        # the group's first atom takes this atom's place, so it leaves along the bond it replaces the end of, not along a new bond
        # from here, which would turn the group 60 degrees and fold one of its branches back onto the chain
        outward = _straight_on(s, atom[0])
    parent = None
    if atom and len(_neighbours(s, atom[0])) >= 2:
        parent, atom = atom, None
        x, y = parent[2] + math.cos(outward), parent[3] + math.sin(outward)
    anchor_x, anchor_y = (atom[2], atom[3]) if atom else (x, y)
    start, end = points[dummy.GetIdx()], points[first.GetIdx()]
    length = math.hypot(end.x - start.x, end.y - start.y) or 1
    turn = outward - math.atan2(end.y - start.y, end.x - start.x)
    cos, sin = math.cos(turn) / length, math.sin(turn) / length

    def placed(index, mirror=False):
        dx, dy = points[index].x - end.x, points[index].y - end.y
        along_x, along_y = dx * cos - dy * sin, dx * sin + dy * cos
        if mirror:  # across the line the group leaves along
            axis_x, axis_y = math.cos(outward), math.sin(outward)
            dot = along_x * axis_x + along_y * axis_y
            along_x, along_y = 2 * dot * axis_x - along_x, 2 * dot * axis_y - along_y
        return anchor_x + along_x, anchor_y + along_y

    # A chain should zigzag, and RDKit labels a carbon drawn straight through. A group can land that way depending on which way it was laid out.
    before = parent or (_atom(s, _neighbours(s, atom[0])[0]) if atom and _neighbours(s, atom[0]) else None)
    others = [n for n in first.GetNeighbors() if n.GetIdx() != dummy.GetIdx()]
    mirror = False
    if before and len(others) == 1 and fragment.GetBondBetweenAtoms(first.GetIdx(), others[0].GetIdx()).GetBondType() == Chem.BondType.SINGLE:
        next_x, next_y = placed(others[0].GetIdx())
        bend = math.atan2(next_y - anchor_y, next_x - anchor_x) - math.atan2(anchor_y - before[3], anchor_x - before[2])
        mirror = abs(math.remainder(bend, math.tau)) < 0.2
    ids = {}
    for fragment_atom in fragment.GetAtoms():
        index = fragment_atom.GetIdx()
        if index == dummy.GetIdx():
            continue
        place_x, place_y = placed(index, mirror)
        if index == first.GetIdx() and atom:
            ids[index] = atom[0]
            _relabel(atom, fragment_atom.GetSymbol(), fragment_atom.GetFormalCharge())
        else:
            ids[index] = _add_atom(s, fragment_atom.GetSymbol(), place_x, place_y)
            _atom(s, ids[index])[4] = fragment_atom.GetFormalCharge()
    orders = {Chem.BondType.DOUBLE: DOUBLE, Chem.BondType.TRIPLE: TRIPLE}
    for bond in fragment.GetBonds():
        if dummy.GetIdx() not in (bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()):
            _set_bond(s, ids[bond.GetBeginAtomIdx()], ids[bond.GetEndAtomIdx()], orders.get(bond.GetBondType(), SINGLE))
    if parent:
        _set_bond(s, parent[0], ids[first.GetIdx()], SINGLE)
    s["focus"] = ["a", ids[first.GetIdx()]]


# The screen


# In the palette: (icon, action, what the status line calls it)
PALETTE = [("select", ("tool", "select"), "Selection [Space]: drag to move, or drag across empty space to select"),
           ("erase", ("tool", "erase"), "Eraser: click an atom to delete it, or a bond to reduce its order"),
           ("pan", ("tool", "pan"), "Pan: drag to move the view"),
           ("text", ("tool", "text"), "Text [t]: click an atom and type an element or group, such as OMe or CF3"),
           ("single", ("bond", SINGLE), "Solid bond [x]"),
           ("double", ("bond", DOUBLE), "Double bond"),
           ("triple", ("bond", TRIPLE), "Triple bond"),
           ("wedge", ("bond", WEDGE), "Wedged bond: starts narrow at the atom you click"),
           ("hash", ("bond", HASH), "Hashed bond: starts narrow at the atom you click"),
           ("chain", ("tool", "chain"), "Chain [X]: drag from an atom or empty space to draw a zigzag chain"),
           ("rlabel", ("tool", "rlabel"), "R-group label: click an atom to make it the next free R, again for the next number"),
           ("plus", ("tool", "plus"), "Positive charge: click an atom"),
           ("minus", ("tool", "minus"), "Negative charge: click an atom"),
           ("ring3", ("ring", 3), "Cyclopropane"),
           ("ring4", ("ring", 4), "Cyclobutane"),
           ("ring5", ("ring", 5), "Cyclopentane [U]"),
           ("ring6", ("ring", 6), "Cyclohexane [u]"),
           ("ring7", ("ring", 7), "Cycloheptane"),
           ("ring8", ("ring", 8), "Cyclooctane"),
           ("cyclopentadiene", ("ring", "cyclopentadiene"), "Cyclopentadiene [J]"),
           ("benzene", ("ring", "benzene"), "Benzene [j]")]
_ICON_NAMES = tuple(entry[0] for entry in PALETTE)
# the tools of a compact editor, one strip of them over the drawing, for a host that edits a molecule where it shows it
COMPACT_TOOLS = ("select", "erase", "text", "single", "double", "triple", "wedge", "hash", "plus", "minus", "ring5", "ring6", "benzene")

# (label, key, action), with None for a dividing line
_COPY = [("Copy SMILES", "", ("copy", ("smiles", False))), ("Copy SVG", "", ("copy", ("svg", False))), ("Copy PNG", "", ("copy", ("png", False))),
         ("Copy SMILES, SVG and PNG", "", ("copy", ("all", False)))]
_ANNOTATE = [*((f"Highlight {name.title()}", "", ("highlight", name)) for name in annotate.HIGHLIGHTS), ("Remove Highlight", "", ("highlight", "")),
             ("Note...", "", ("note", None)), ("Clear Annotations", "", ("clear", None))]
MENUS = [("File", [("Load SMILES...", "/", ("command", "entry")), *_COPY, ("Copy Molfile (V3000)", "", ("command", "copy_molfile")),
                   ("Quit and print SMILES", "Ctrl-Q", ("command", "quit"))]),
         ("Edit", [("Undo", "Ctrl-Z", ("command", "undo")), ("Redo", "Ctrl-Y", ("command", "redo")), None,
                   ("Select All", "Ctrl-A", ("command", "select_all")), ("Delete", "Del", ("command", "delete"))]),
         ("View", [("Actual Size", "F5", ("command", "actual")), ("Fit to Window", "F6", ("command", "fit")), ("Magnify", "F7", ("command", "zoom_in")), ("Reduce", "F8", ("command", "zoom_out"))]),
         ("Structure", [("Clean Up", "Ctrl-K", ("command", "tidy")), ("Aromatize", "", ("command", "aromatize")),
                        ("Dearomatize", "", ("command", "dearomatize")), ("Add Explicit Hydrogens", "", ("command", "add_h")),
                        ("Remove Explicit Hydrogens", "", ("command", "remove_h")), None, ("Flip Horizontal", "", ("command", "flip_h")),
                        ("Flip Vertical", "", ("command", "flip_v")), ("Rotate 90°", "", ("command", "rotate")), None,
                        ("Check Structure", "", ("command", "check")), ("Calculated Values", "", ("command", "calc")), None,
                        ("R-group Logic...", "", ("command", "logic"))]),
         ("Annotate", [*_ANNOTATE, None, ("Show Atom Numbers", "", ("show", "numbers")), ("Show Stereo Labels", "", ("show", "cip")), None,
                       ("Clear All Annotations", "", ("command", "clear_notes"))]),
         ("Help", [("Keys and Mouse", "?", ("command", "help"))])]
CONTEXT_MENUS = {
    "atom": [(symbol, key, ("element", symbol)) for symbol, key in (("C", "c"), ("N", "n"), ("O", "o"), ("S", "s"), ("P", "p"), ("F", "f"), ("Cl", "l"), ("Br", "b"))]
    + [None, ("Label...", "Enter", ("label", None)), ("Charge +", "+", ("charge", 1)), ("Charge -", "-", ("charge", -1)), ("Delete", "Del", ("delete", None)),
       None, ("Properties and Markush ▸", "", ("submenu", "atom_more")),
       None, ("Highlight", "", ("highlight", "yellow")), ("Note...", "", ("note", None)), ("Clear Annotations", "", ("clear", None))],
    # what Ketcher's atom dialog has, and its R-group tools
    "atom_more": [("Isotope...", "", ("prompt", "isotope")), ("Radical", "", ("radical", None)), None,
                  ("R-group Label...", "", ("prompt", "rgroup")), ("Any Atom (A)", "", ("element", "A")), ("Heteroatom (Q)", "", ("element", "Q")),
                  ("Halogen (X)", "x", ("element", "X")), ("Metal (M)", "", ("element", "M")), ("Atom List...", "", ("prompt", "list")), None,
                  ("Attachment Point", ".", ("point", None)), ("Link Node...", "", ("prompt", "link")), ("Stereo Group...", "", ("prompt", "stereo"))],
    "bond": [("Single", "1", ("kind", SINGLE)), ("Double", "2", ("kind", DOUBLE)), ("Triple", "3", ("kind", TRIPLE)), ("Wedged", "w", ("kind", WEDGE)),
             ("Hashed", "h", ("kind", HASH)), ("Wavy", "y", ("kind", WAVY)), None, ("More Bonds and Queries ▸", "", ("submenu", "bond_more")),
             ("Delete", "Del", ("delete", None)),
             None, ("Highlight", "", ("highlight", "yellow")), ("Note...", "", ("note", None)), ("Clear Annotations", "", ("clear", None))],
    "bond_more": [("Aromatic", "", ("kind", AROMATIC)), ("Dative", "", ("kind", DATIVE)), ("Crossed Double", "", ("kind", CROSSED)), None,
                  ("Any Bond", "", ("kind", ANY)), ("Single or Double", "", ("kind", SINGLE_DOUBLE)), ("Single or Aromatic", "", ("kind", SINGLE_AROMATIC)),
                  ("Double or Aromatic", "", ("kind", DOUBLE_AROMATIC)), ("In a Ring", "", ("topo", "ring")), ("In a Chain", "", ("topo", "chain")),
                  ("Ring or Chain", "", ("topo", None)), None, ("Attach to Selected Atoms", "", ("vary", None))],
    "empty": [("Undo", "Ctrl-Z", ("command", "undo")), ("Redo", "Ctrl-Y", ("command", "redo")), None, ("Clean Up", "Ctrl-K", ("command", "tidy")),
              ("Fit to Window", "F6", ("command", "fit")), ("Select All", "Ctrl-A", ("command", "select_all")), None, *_COPY],
    # the selection's own: what is copied is the selected atoms and the bonds between them
    "selection": [*((label.replace("Copy", "Copy Selection as", 1) if "," not in label else "Copy Selection as SMILES, SVG and PNG", key, ("copy", (action[1][0], True)))
                    for label, key, action in _COPY), None, ("Markush and Stereo ▸", "", ("submenu", "selection_more")),
                  ("Flip Horizontal", "", ("command", "flip_h")), ("Flip Vertical", "", ("command", "flip_v")), ("Rotate 90°", "", ("command", "rotate")),
                  None, *_ANNOTATE, None, ("Delete", "Del", ("command", "delete"))],
    "selection_more": [("Repeating Unit...", "", ("prompt", "bracket")), ("Remove Repeating Unit", "", ("unbracket", None)), None,
                       ("R-group Definition...", "", ("prompt", "fragment")), ("Stereo Group...", "", ("prompt", "stereo"))],
}


class Layout:
    """Where everything is. Columns and rows here count from 0 at the top left of the editor, which is the screen's unless the editor
    is in a box: then `box` is (row, col, rows, cols) on the screen, counting its border, and `title` goes in the border. A `window` box
    also has buttons to minimize and maximize it on its top edge, and a grip to resize it at the bottom right, for a host that does so.
    A `compact` editor has no menu bar and a short strip of tools above the drawing (COMPACT_TOOLS); its menus are those of the
    right button. `tools` are the palette's indices that are shown, in order.

    An `inline` editor draws over a host's screen, the whole of which is its own coordinates: the drawing in `inline`, (row, col, rows,
    cols), and a strip floating over the host's text above it (below, if there is no room above), of the compact tools and a line for
    the status and the buttons. With `strip` false the tools are put away and only that line is shown. Everything else on the screen
    is the host's, which draws it again when the editor asks (HOST_REPAINT)."""

    def __init__(self, cols, rows, cell_width=None, cell_height=None, pixels=False, box=None, title="", window=False, compact=False,
                 inline=None, strip=True):
        self.box, self.title = box, title
        self.boxed = box is not None
        self.inline = inline is not None and not self.boxed
        self.hosted = self.boxed or self.inline  # inside a host's screen, which keeps the rest of it
        self.window = self.boxed and window
        self.origin = (box[0] + 1, box[1] + 1) if box else (0, 0)
        if box:
            rows, cols = box[2] - 2, box[3] - 2
        self.cols, self.rows = cols, rows
        self.cell_width, self.cell_height = cell_width or DEFAULT_CELL[0], cell_height or DEFAULT_CELL[1]
        self.pixels = pixels
        self.compact = compact or self.inline
        self.strip = strip or not self.inline
        self.tools = [i for i, entry in enumerate(PALETTE) if entry[0] in COMPACT_TOOLS] if self.compact else list(range(len(PALETTE)))
        # square buttons two rows high, in as few columns as the height of the window allows
        self.button_cols = min(max(round(2 * self.cell_height / self.cell_width), 3), 6)
        self.palette_left, self.bar_row, self.strip_width = 0, None, 0
        self.menu_bottom = rows - STATUS_ROWS  # menus stay above the status lines
        if self.inline:
            self._place_inline(*inline)
            return
        if compact:  # the strip, then the drawing below it, the full width
            self.per_row, self.palette_rows, self.palette_top = len(self.tools), 2, 0
            self.top, self.left = 2, 0
            self.canvas_rows = rows - 2 - STATUS_ROWS
        else:  # the menu bar, then the palette on the left and the drawing beside it
            self.top = self.palette_top = MENU_ROWS
            self.canvas_rows = rows - MENU_ROWS - STATUS_ROWS
            self.per_row = 2
            while math.ceil(len(self.tools) / self.per_row) * 2 > self.canvas_rows and self.per_row < len(self.tools):
                self.per_row += 1
            self.palette_rows = math.ceil(len(self.tools) / self.per_row) * 2
            self.left = self.per_row * self.button_cols + 1
        self.canvas_cols = cols - self.left
        self.titles = []
        col = 1
        for index, (title, _) in enumerate(() if compact else MENUS):
            self.titles.append((col, len(title) + 2, index))
            col += len(title) + 2

    def _place_inline(self, row, col, rows, cols):
        if not self.strip:
            self.tools = []
        self.per_row = len(self.tools)
        self.palette_rows = 2 if self.tools else 0
        need = self.palette_rows + 1  # the tools, then the line
        if row >= need:  # above the drawing, the line next to it
            self.palette_top, self.bar_row = row - need, row - 1
        elif row + rows + need <= self.rows:  # below it, the line next to it
            self.bar_row, self.palette_top = row + rows, row + rows + 1
        else:  # over the drawing's own top
            self.palette_top, self.bar_row = row, row + self.palette_rows
            row, rows = row + need, rows - need
        self.strip_width = min(max(self.per_row * self.button_cols, STRIP_WIDE), self.cols - 1)
        self.palette_left = max(min(col, self.cols - 1 - self.strip_width), 0)
        self.top, self.left, self.canvas_rows, self.canvas_cols = row, col, rows, cols
        self.menu_bottom = self.rows
        self.titles = []

    def strip_rows(self):
        """The screen's rows the floating strip takes."""
        return sorted({self.bar_row, *range(self.palette_top, self.palette_top + self.palette_rows)})

    def at(self, row, col, text=""):
        """Print at a place in the editor."""
        return _ansi(row + self.origin[0], col + self.origin[1], text)

    def erase(self):
        """Blank the rest of a row, in the current colours, without touching what is beside a box."""
        return " " * self.cols if self.boxed else "\x1b[K"

    def blank(self, row):
        return self.at(row, 0, " " * self.cols if self.boxed else "\x1b[2K")

    def from_report(self, x, y, pixels):
        """A mouse report to a position here, as floats, which is outside the editor for a pointer outside the box."""
        if pixels:
            return x / self.cell_width - self.origin[1], y / self.cell_height - self.origin[0]
        return x - 0.5 - self.origin[1], y - 0.5 - self.origin[0]

    def buttons(self):
        """On the bottom edge of the box, or at the right of an inline editor's line: (name, label, column)."""
        if self.inline:
            col, found = self.palette_left + self.strip_width, []
            for name, label in (("tools", " ▾ F2 " if self.strip else " ▴ tools F2 "), ("cancel", " Cancel ^C "), ("done", " Done ^Q ")):
                col -= len(label)
                found.append((name, label, col))
            return found
        col, found = self.cols, []
        for name, label in (("done", " Done Ctrl-Q "), ("cancel", " Cancel Ctrl-C "), ("copy", " Copy SMILES ")):
            col -= len(label) + 1
            found.append((name, label, col))
        return found

    def controls(self):
        """On the top edge of a window: (name, label, column)."""
        col, found = self.cols, []
        for name, label in WINDOW_BUTTONS[::-1] if self.window else ():
            col -= len(label)
            found.append((name, label, col))
        return found

    def button_at(self, col, row):
        if self.inline:
            found = self.buttons() if math.floor(row) == self.bar_row else ()
            return next((name for name, label, start in found if start <= col < start + len(label)), None)
        if not self.boxed or math.floor(row) not in (-1, self.rows):
            return None
        found = self.controls() if math.floor(row) == -1 else self.buttons()
        return next((name for name, label, start in found if start <= col < start + len(label)), None)

    def title_at(self, col, row):
        if not 0 <= row < MENU_ROWS:
            return None
        return next((index for start, width, index in self.titles if start <= col < start + width), None)

    def palette_at(self, col, row):
        """The palette's index of the button at a place, or None."""
        col -= self.palette_left
        if not (self.palette_top <= row < self.palette_top + self.palette_rows and 0 <= col < self.per_row * self.button_cols):
            return None
        index = (int(row) - self.palette_top) // 2 * self.per_row + int(col) // self.button_cols
        return self.tools[index] if index < len(self.tools) else None

    def to_model(self, s, col, row):
        """Canvas position in cells, as floats, to the molecule's coordinates. y runs upwards there."""
        x, y, zoom = s["view"]
        return (x + (col - self.left - self.canvas_cols / 2) / zoom,
                y - (row - self.top - self.canvas_rows / 2) * self.cell_height / self.cell_width / zoom)

    def to_cell(self, s, x, y):
        view_x, view_y, zoom = s["view"]
        return ((x - view_x) * zoom + self.left + self.canvas_cols / 2,
                (view_y - y) * zoom * self.cell_width / self.cell_height + self.top + self.canvas_rows / 2)

    def in_canvas(self, col, row):
        return self.top <= row < self.top + self.canvas_rows and self.left <= col < self.cols


def _is_active(s, action):
    kind, value = action
    return ((kind == "bond" and s["tool"] == "bond" and s["bond"] == value)
            or (kind == "ring" and s["tool"] == "ring" and s["ring"] == value)
            or (kind == "tool" and s["tool"] == value))


def _drag_end(s, ui, press):
    """Where a bond being dragged would end: (x, y, id of the atom it reaches or None)."""
    x, y = s["drag"]
    hit = _atom_at(s, ui, x, y)
    start = press["target"][1] if press["target"] and press["target"][0] == "a" else None
    if hit and hit != start:
        atom = _atom(s, hit)
        return atom[2], atom[3], hit
    origin_x, origin_y = press["model"]
    if start is not None:
        atom = _atom(s, start)
        origin_x, origin_y = atom[2], atom[3]
    # a pointer reported only by cell can't aim finely enough for the smaller step
    snap = math.radians(15 if ui.pixels else 30)
    angle = round(math.atan2(y - origin_y, x - origin_x) / snap) * snap
    return origin_x + math.cos(angle), origin_y + math.sin(angle), None


def _chain_points(s, ui, press):
    """The atoms a chain dragged so far would add, after its first: a zigzag of bonds 1 long towards the pointer, 120 degrees apart."""
    x, y = s["drag"]
    start = press["target"][1] if press["target"] and press["target"][0] == "a" else None
    origin = _atom(s, start)[2:4] if start is not None else press["model"]
    angle = math.atan2(y - origin[1], x - origin[0])
    snap = math.radians(15 if ui.pixels else 30)
    angle = round(angle / snap) * snap
    step = math.cos(math.radians(30))  # how far along the line each bond of the zigzag goes
    count = max(1, min(int(math.hypot(x - origin[0], y - origin[1]) / step + 0.5), 30))
    points, (px, py) = [], origin
    for index in range(count):
        turn = math.radians(30) * (1 if index % 2 == 0 else -1)
        px, py = px + math.cos(angle + turn), py + math.sin(angle + turn)
        points.append([px, py])
    return [list(origin), *points]


def _target_points(s, target):
    """The atom's position, or both ends of the bond, for marking a target on the canvas."""
    if target[0] == "a":
        atom = _atom(s, target[1])
        return [(atom[2], atom[3])] if atom else []
    bond = _bond(s, target[1], target[2])
    if not bond:
        return []
    return list(_bond_ends(s, bond))


def _ghost(s, ui):
    """The bonds a click would add, as pairs of points: tried out on a copy of the drawing."""
    if s["press"] or s["menu"] or s["label"] or s["help"] or s["entry"] is not None or not s["pointer"]:
        return []
    target, (x, y) = s["hover"], s["pointer"]
    if s["tool"] == "bond" and not (target and target[0] == "a"):
        return []
    if s["tool"] not in ("bond", "ring"):
        return []
    trial = {**s, "atoms": json.loads(json.dumps(s["atoms"])), "bonds": json.loads(json.dumps(s["bonds"])), "undo": [], "redo": []}
    if s["tool"] == "bond":
        _grow(trial, target[1], "C", s["bond"])
    else:
        _ring_on_target(trial, target, s["ring"], x, y)
    known = {frozenset(b[:2]) for b in s["bonds"]}
    return [(_atom(trial, b[0])[2:4], _atom(trial, b[1])[2:4]) for b in trial["bonds"] if frozenset(b[:2]) not in known]


def _marks(s, ui, wrong):
    """Everything drawn over the molecule, as plain data, so that an unchanged list needs no new image."""
    marks = []
    for index in wrong:
        if index < len(s["atoms"]):
            marks.append(["box", s["atoms"][index][2:4], RED, 0.95, False])
    chosen = set(s["selected"])
    for bond in s["bonds"]:
        if bond[0] in chosen and bond[1] in chosen:
            start, end = _bond_ends(s, bond)
            marks.append(["line", list(start), list(end), ACCENT, 0.22, 0.14])
    for ident in s["selected"]:
        atom = _atom(s, ident)
        if atom:
            marks.append(["box", atom[2:4], ACCENT, 0.22, True])
    points = _target_points(s, _target(s)) if _target(s) else []
    if len(points) == 1:
        marks.append(["spot", list(points[0])])
    elif len(points) == 2:
        marks.append(["line", list(points[0]), list(points[1]), HOTSPOT, 0.45, 0.2])
    for start, end in _ghost(s, ui):
        marks.append(["line", list(start), list(end), ACCENT, 0.5, 0.05])
    press = s["press"]
    if press and press["moved"] and s["drag"]:
        if press["mode"] == "draw" and s["tool"] == "chain":
            points = _chain_points(s, ui, press)
            for first, second in zip(points, points[1:]):
                marks.append(["line", first, second, ACCENT, 0.9, 0.05])
        elif press["mode"] == "draw":
            end_x, end_y, _ = _drag_end(s, ui, press)
            start = _atom(s, press["target"][1]) if press["target"] and press["target"][0] == "a" else None
            marks.append(["line", start[2:4] if start else list(press["model"]), [end_x, end_y], ACCENT, 0.9, 0.05])
        elif press["mode"] == "marquee":
            marks.append(["rect", list(press["model"]), list(s["drag"])])
    return marks


def _drawer(s, ui):
    """A transparent image of the canvas, with coordinates set up for the molecule. Returns (drawer, pixels per bond)."""
    x, y, zoom = s["view"]
    width, height = ui.canvas_cols * ui.cell_width, ui.canvas_rows * ui.cell_height
    shrink = min(1.0, math.sqrt(MAX_PIXELS / (width * height)))
    width_px, height_px = max(int(width * shrink), 1), max(int(height * shrink), 1)
    per_unit = zoom * ui.cell_width * shrink
    half_x, half_y = width_px / (2 * per_unit), height_px / (2 * per_unit)
    drawer = rdMolDraw2D.MolDraw2DCairo(width_px, height_px)
    options = drawer.drawOptions()
    options.clearBackground = False
    options.setBackgroundColour((0, 0, 0, 0))
    options.padding = 0
    # model units are 1.5 per bond in the molblock, as RDKit expects
    drawer.SetScale(width_px, height_px, Point2D((x - half_x) * 1.5, (y - half_y) * 1.5), Point2D((x + half_x) * 1.5, (y + half_y) * 1.5))
    return drawer, per_unit


def draw_structure(s, ui, theme):
    """The molecule alone, as a PNG."""
    drawer, per_unit = _drawer(s, ui)
    options = drawer.drawOptions()
    options.prepareMolsBeforeDrawing = False
    options.setAtomPalette(graphics.palette(theme))
    options.bondLineWidth = 2
    options.useMolBlockWedging = True
    options.fixedFontSize = max(int(per_unit * 0.42), 8)
    mol, points = _with_attachments(s, _raw_mol(s))
    notes = _by_index(s["notes"], {a[0]: i for i, a in enumerate(s["atoms"])})
    annotate.set_options(options, notes)
    if "cip" in notes["show"]:
        with rdBase.BlockLogs():
            valid = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]), removeHs=False)
        if valid is not None:
            annotate.label_stereo(mol, valid)
    if ui.inline:  # the host shows the notes, in bubbles at the ends of leaders drawn here
        notes = {**notes, "atoms": [[i, h, ""] for i, h, _ in notes["atoms"]], "bonds": [[a, b, h, ""] for a, b, h, _ in notes["bonds"]]}
    highlights = annotate.apply(mol, notes)
    with rdBase.BlockLogs():
        Chem.ReapplyMolBlockWedging(mol)
        drawer.DrawMolecule(mol, **highlights)
    _decorate(s, drawer, points, graphics.palette(theme)[-1])
    if ui.inline:
        _draw_leaders(s, ui, drawer)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def inline_notes(s, ui):
    """An inline editor's notes, for its host's bubbles: [(text, the anchor's model point, the row its leader ends on, its flags)],
    for the notes whose atom or bond is on the drawing. The anchor is the atom, the middle of the bond, or the middle of the
    highlighted group for a group's note. The rows are kept apart, as molterm's callouts keeps them."""
    found = []
    entries = [([ident], text) for ident, _, text in s["notes"]["atoms"]] + [([a, b], text) for a, b, _, text in s["notes"]["bonds"]]
    draft = _draft(s)  # a note being written is shown where it will go, as it is typed, flagged "draft"
    if draft:
        entries = [(ids, text) for ids, text in entries if ids != draft] + [(draft, s["prompt"]["text"])]
    for ids, text in entries:
        flags = annotate.flags(s["notes"], ids)
        if ids == draft:
            flags = {"user", "draft"} | ({"group"} if s["prompt"].get("group") else set())
        chosen = [_atom(s, i) for i in (_group(s, ["a", *ids] if len(ids) == 1 else ["b", *ids]) if "group" in flags else ids)]
        if (text or ids == draft) and all(chosen):
            found.append((text, (sum(a[2] for a in chosen) / len(chosen), sum(a[3] for a in chosen) / len(chosen)), flags))
    shown = []
    for text, point, flags in found:
        col, row = ui.to_cell(s, *point)
        if ui.left <= col < ui.left + ui.canvas_cols and ui.top <= row < ui.top + ui.canvas_rows:
            shown.append((text, point, int(row) - ui.top, flags))
    rows = callouts._rows([row for _, _, row, _ in shown], ui.canvas_rows)
    return [(text, point, ui.top + row, flags) for (text, point, _, flags), row in zip(shown, rows)]


def _draft(s):
    """The atom ([atom]) or bond ([atom, atom]) whose note is being written, the first if it is for several, or None."""
    prompt = s["prompt"]
    if prompt is None or prompt.get("kind", "note") != "note" or not prompt["targets"]:
        return None
    target = prompt["targets"][0]
    return list(target[1:2]) if target[0] == "a" else sorted(target[1:])


def _draw_leaders(s, ui, drawer):
    """Each note's leader, from its atom to the drawing's right edge, in the middle of its row: where the host's bubble takes it on."""
    edge = ui.left + ui.canvas_cols
    for _, (x, y), row, flags in inline_notes(s, ui):
        colour = tuple(c / 255 for c in (callouts.USER_LEADER if "user" in flags else callouts.LEADER))
        col, at_row = ui.to_cell(s, x, y)
        level = row + 0.5
        bend = min(col + abs(level - at_row) * ui.cell_height / ui.cell_width, edge)
        path = [ui.to_model(s, col, at_row), ui.to_model(s, bend, level), ui.to_model(s, edge, level)]
        drawer.SetColour((*colour, 1.0))
        drawer.SetLineWidth(2)
        for (x0, y0), (x1, y1) in zip(path, path[1:]):
            drawer.DrawLine(Point2D(x0 * 1.5, y0 * 1.5), Point2D(x1 * 1.5, y1 * 1.5))
        drawer.SetFillPolys(True)
        drawer.DrawArc(Point2D(x * 1.5, y * 1.5), 0.12, 0, 360)


def _with_attachments(s, mol):
    """The molecule with a hydrogen fewer on each attachment point, for the bond it stands for, and [(the atom's point, where that bond
    ends, which attachment point it is, the atom's id)], for _decorate to draw it as Ketcher does."""
    found = []
    for index, atom in enumerate(s["atoms"]):
        point = _extras(atom).get("ap")
        if not point:
            continue
        rd = mol.GetAtomWithIdx(index)
        bonds = 2 if point == 3 else 1
        rd.SetNumExplicitHs(max(rd.GetTotalNumHs() - bonds, 0))
        rd.SetNoImplicit(True)
        angle = _direction(s, atom[0])
        for number, turn in (((1, -0.5), (2, 0.5)) if point == 3 else ((point, 0.0),)):
            found.append(((atom[2], atom[3]), (atom[2] + 0.7 * math.cos(angle + turn), atom[3] + 0.7 * math.sin(angle + turn)), number, atom[0]))
    if found:
        mol.UpdatePropertyCache(strict=False)
    return mol, found


def _decorate(s, drawer, points, ink):
    """What RDKit doesn't draw: the wavy end of each attachment point, numbered when there are two kinds, and "R1 =" before each of
    an R-group's definitions."""
    colour = (*ink, 1.0)
    # numbered where one atom or one definition has both
    groups = {_extras(a).get("rg") for a in s["atoms"] if _extras(a).get("ap") in (2, 3)}
    for (x0, y0), (x1, y1), number, ident in points:
        numbered = _extras(_atom(s, ident)).get("rg") in groups or _extras(_atom(s, ident)).get("ap") == 3
        length = math.hypot(x1 - x0, y1 - y0) or 1
        # from the edge of a label, if the atom has one, which RDKit draws about a third of a bond wide
        labelled = _atom(s, ident)[1] != "C" or _extras(_atom(s, ident)).get("iso")
        start = 0.3 if labelled else 0.0
        drawer.SetColour(colour)
        drawer.SetLineWidth(2)
        drawer.DrawLine(Point2D((x0 + (x1 - x0) / length * start) * 1.5, (y0 + (y1 - y0) / length * start) * 1.5), Point2D(x1 * 1.5, y1 * 1.5))
        across_x, across_y = -(y1 - y0) / length * 0.3, (x1 - x0) / length * 0.3
        drawer.DrawWavyLine(Point2D((x1 + across_x) * 1.5, (y1 + across_y) * 1.5), Point2D((x1 - across_x) * 1.5, (y1 - across_y) * 1.5),
                            colour, colour, 6, 0.04)
        if numbered:
            drawer.SetColour(colour)
            drawer.DrawString(str(number), Point2D((x1 + (x1 - x0) / length * 0.3) * 1.5, (y1 + (y1 - y0) / length * 0.3) * 1.5))
    drawer.SetColour(colour)
    for number, groups in sorted(_fragments(s).items()):
        for members in groups:
            atoms = [_atom(s, i) for i in members]
            xs = [a[2] for a in atoms] + [end[0] for _, end, _, ident in points if ident in members]
            middle = (min(a[3] for a in atoms) + max(a[3] for a in atoms)) / 2
            drawer.DrawString(f"R{number} =", Point2D((min(xs) - 0.5) * 1.5, middle * 1.5), 2)


def draw_overlay(s, ui, marks):
    """The marks alone, as a PNG. Mostly empty, so far smaller than the molecule's image, which is what keeps hovering quick."""
    drawer, per_unit = _drawer(s, ui)

    def point(p):
        return Point2D(p[0] * 1.5, p[1] * 1.5)

    half = 0.26
    def disc(centre, radius):
        drawer.SetFillPolys(True)
        drawer.DrawEllipse(point((centre[0] - radius, centre[1] - radius)), point((centre[0] + radius, centre[1] + radius)))

    for mark in marks:
        if mark[0] == "spot":
            drawer.SetColour((*HOTSPOT, 0.45))
            disc(mark[1], half)
            drawer.SetColour((*HOTSPOT_DOT, 1.0))
            disc(mark[1], 0.06)
        elif mark[0] == "box":
            (x, y), colour, alpha, filled = mark[1:]
            drawer.SetColour((*colour, alpha))
            drawer.SetFillPolys(filled)
            drawer.SetLineWidth(max(int(per_unit * 0.03), 2))
            drawer.DrawRect(point((x - half, y - half)), point((x + half, y + half)))
        elif mark[0] == "line":
            start, end, colour, alpha, width = mark[1:]
            drawer.SetColour((*colour, alpha))
            drawer.SetLineWidth(max(int(per_unit * width), 2))
            drawer.DrawLine(point(start), point(end))
        else:
            first, second = mark[1:]
            drawer.SetColour((*ACCENT, 0.12))
            drawer.SetFillPolys(True)
            drawer.DrawRect(point(first), point(second))
            drawer.SetColour((*ACCENT, 0.8))
            drawer.SetFillPolys(False)
            drawer.SetLineWidth(2)
            drawer.DrawRect(point(first), point(second))
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


# Kitty draws images whose z is below this under any cell with its own background, so the menus, which have one, cover the drawing.
_BELOW_CELLS = -1_073_741_824
_Z = {"palette": _BELOW_CELLS - 1, "structure": _BELOW_CELLS - 2, "overlay": _BELOW_CELLS - 1, "backing": _BELOW_CELLS - 3}
_IDS = {"palette": 7010, "structure": 7001, "overlay": 7005, "backing": 7020}  # each also uses the id after it
HOST_Z = _BELOW_CELLS - 10  # a host's images at or below this are hidden by the box
def _ansi(row, col, text=""):
    return f"\x1b[{row + 1};{col + 1}H\x1b[0m{text}"


def _fit_text(text, width):
    text = "".join(c if c.isprintable() else " " for c in text)
    return text if len(text) <= width else text[:max(width - 1, 0)] + "…"


def _menu_items(s, ui=None):
    menu = s["menu"]
    if menu[0] == "context":
        items = CONTEXT_MENUS[menu[1]]
        target = s.get("menu_target")
        if menu[1] in ("atom", "bond") and target and len(_group(s, target)) > 1:  # in a highlighted group: a note on it, or on the group
            at = items.index(("Note...", "", ("note", None)))
            own = "Note This Atom..." if menu[1] == "atom" else "Note This Bond..."
            items = [*items[:at], (own, "", ("note", None)), ("Note Highlighted Group...", "", ("note", "group")), *items[at + 1:]]
        return items
    if menu[1] == 0 and ui and ui.hosted:
        return [*MENUS[0][1][:-1], ("Done", "Ctrl-Q", ("command", "quit")), ("Cancel", "Ctrl-C", ("command", "cancel"))]
    return MENUS[menu[1]][1]


def _item_label(s, item):
    """A menu item's text, with a tick on a setting that is on."""
    label, _, (kind, value) = item
    if kind == "show":
        return ("✓ " if value in s["notes"]["show"] else "  ") + label
    return label


def _menu_box(s, ui):
    """Where the open menu is: (col, row, width), kept on the screen."""
    items = _menu_items(s, ui)
    width = max(len(_item_label(s, item)) + len(item[1]) for item in items if item) + 5
    if s["menu"][0] == "bar":
        col, row = ui.titles[s["menu"][1]][0], MENU_ROWS
    else:
        col, row = s["menu"][2], s["menu"][3]
    # above the status lines, which are drawn after it
    return max(min(col, ui.cols - width), 0), max(min(row, ui.menu_bottom - len(items)), 0), width


def _menu_item_at(s, ui, col, row):
    start_col, start_row, width = _menu_box(s, ui)
    index = math.floor(row) - start_row
    items = _menu_items(s, ui)
    if start_col <= col < start_col + width and 0 <= index < len(items) and items[index]:
        return index
    return None


def _menu_bar(s, ui, colours):
    bar = f"\x1b[{colours['bar']};{colours['ink']}m"
    out = [ui.at(0, 0, f"{bar}{ui.erase()}")]
    for start, width, index in ui.titles:
        chosen = s["menu"] and s["menu"][0] == "bar" and s["menu"][1] == index
        style = f"\x1b[{colours['pick']};{colours['on_pick']}m" if chosen else bar
        out.append(ui.at(0, start, f"{style} {MENUS[index][0]} "))
    out.append("\x1b[0m")
    return "".join(out)


def _popups(s, ui, colours):
    """Whatever sits over the drawing right now, in cells with their own background."""
    bar = f"\x1b[{colours['bar']};{colours['ink']}m"
    out = []
    if s["menu"]:
        start_col, start_row, width = _menu_box(s, ui)
        for index, item in enumerate(_menu_items(s, ui)):
            if item is None:
                text, style = "─" * width, f"\x1b[{colours['bar']};{colours['dim']}m"
            else:
                label, key = _item_label(s, item), item[1]
                text = f" {label}{' ' * (width - len(label) - len(key) - 2)}{key} "
                style = f"\x1b[{colours['pick']};{colours['on_pick']}m" if index == s["menu_hover"] else bar
            out.append(ui.at(start_row + index, start_col, style + text))
    if s["label"]:
        col, row = ui.to_cell(s, *s["label"]["at"])
        text = f" {_fit_text(s['label']['text'], MAX_LABEL)}"
        col = max(min(int(col) - 1, ui.cols - len(text) - 2), ui.left)
        row = max(min(int(row), ui.top + ui.canvas_rows - 1), ui.top)
        out.append(ui.at(row, col, f"{bar}{text}\x1b[7m \x1b[27m "))
    if s["help"]:
        text = KEYS_HELP.replace("Ctrl-Q quit and print the SMILES", "Ctrl-Q done   Ctrl-C cancel") if ui.hosted else KEYS_HELP
        lines = text.split("\n")[:ui.canvas_rows]
        width = min(max(len(line) for line in lines) + 2, ui.canvas_cols)
        for index, line in enumerate(lines):
            out.append(ui.at(ui.top + index, ui.left, f"{bar} {_fit_text(line, width - 2):<{width - 2}} "))
    return "".join(out) + ("\x1b[0m" if out else "")


def _status(s, ui, smiles, problem, colours, cols=None):
    cols = cols or ui.cols
    dim, err, warn = (f"\x1b[{colours[name]}m" for name in ("dim", "err", "warn"))
    if s["prompt"] is not None:
        count = len(s["prompt"]["targets"])
        title, hint = _PROMPT_TEXT[s["prompt"].get("kind", "note")]
        if ui.inline and _draft(s):  # its host shows it in a bubble at the end of its leader, where it is typed
            return (f"\x1b[1mNote\x1b[0m{dim}: Enter keeps it · Shift+Enter a new line · Esc cancels\x1b[0m",
                    f"{dim}{_fit_text('An empty note takes it off.', cols)}\x1b[0m")
        shown = _fit_text(s["prompt"]["text"].replace("\n", " ⏎ "), cols - len(title) - 4)
        where = "this atom or bond" if count == 1 else f"these {count} atoms and bonds"
        if s["prompt"].get("group"):
            where = "the highlighted group"
        return f"\x1b[1m{title}:\x1b[0m {shown}\x1b[7m \x1b[0m", f"{dim}{_fit_text(hint.format(where=where) + ' Esc cancels.', cols)}\x1b[0m"
    if s["entry"] is not None:
        width = cols - 9
        shown = _fit_text(s["entry"][-width:], width)
        second = _fit_text(s["message"], cols) if s["message"] else "Type or paste, then Enter to load it. Esc cancels."
        return f"\x1b[1mSMILES:\x1b[0m {shown}\x1b[7m \x1b[0m", f"{dim}{second}\x1b[0m"
    if smiles:
        first, details = _fit_text(smiles, cols), facts(smiles)
    elif smiles is None:
        first = err + _fit_text(problem or "RDKit can't read this structure.", cols) + "\x1b[0m"
        details = f"{err}Atoms in red boxes need attention.\x1b[0m"
    else:
        first, details = f"{dim}Empty. Click to draw a bond, or press u for the cyclohexane tool and click. Press ? for all keys.\x1b[0m", ""
    if s["message"]:
        return first, warn + _fit_text(s["message"], cols) + "\x1b[0m"
    if s["label"]:
        return first, dim + _fit_text("Type an element or group, such as N, OMe, CF3 or Ph. Enter applies, Esc cancels.", cols) + "\x1b[0m"
    if s["tip"] is not None and s["tip"] < len(PALETTE):
        return first, dim + _fit_text(PALETTE[s["tip"]][2], cols) + "\x1b[0m"
    return first, details


def _bar(s, ui, smiles, problem, colours):
    """An inline editor's line, over the host's text: the one status line that matters most now, and the buttons."""
    bar, dim = f"\x1b[{colours['bar']};{colours['ink']}m", f"\x1b[{colours['dim']}m"
    buttons = ui.buttons()
    room = min(start for _, _, start in buttons) - ui.palette_left - 2
    first, second = _status(s, ui, smiles, problem, colours, room)
    if s["prompt"] is not None or s["entry"] is not None or smiles is None:
        text = first
    elif s["message"] or s["label"] or s["tip"] is not None:
        text = second
    else:
        text = "\x1b[1m" + _fit_text(f"✎ {ui.title or 'Editing'}", room // 2) + "\x1b[22m" + dim + _fit_text(
            "  right button: highlight, note", room - min(len(ui.title or "Editing") + 2, room // 2))
    text = text.replace("\x1b[0m", "\x1b[0m" + bar)
    out = [ui.at(ui.bar_row, ui.palette_left, bar + " " * ui.strip_width), ui.at(ui.bar_row, ui.palette_left + 1, bar + text)]
    for name, label, col in buttons:
        chosen = s["button"] == name
        out.append(ui.at(ui.bar_row, col, (f"\x1b[{colours['pick']};{colours['on_pick']}m" if chosen else bar + "\x1b[1m") + label))
    return "".join(out) + "\x1b[0m"


def _host_backdrop(ui):
    """For an inline editor: the host draws its screen again, then the drawing's place and the strip are cleared for the editor."""
    out = [HOST_REPAINT, "\x1b[?25l"]  # and its cursor stays hidden
    out.extend(ui.at(row, ui.left, " " * ui.canvas_cols) for row in range(ui.top, ui.top + ui.canvas_rows))
    out.extend(ui.at(row, ui.palette_left, " " * ui.strip_width) for row in ui.strip_rows())
    return "".join(out)


def _place(layer, s, ui, png, cols, rows, row, col):
    """Show a new image of a layer, then remove the old one, so that the place never shows empty."""
    old = _IDS[layer] + s["flips"].get(layer, 0)
    s["flips"][layer] = 1 - s["flips"].get(layer, 0)
    new = _IDS[layer] + s["flips"][layer]
    if placeholders.active.get():
        # sent only: its cells are printed with the text, see _grids. The old picture isn't removed: tmux shows the new cells only when
        # it next redraws, and until then they'd name nothing and flicker blank. The next change overwrites it under the same id.
        return placeholders.transmit(png, new, cols, rows)
    sequence = graphics.kitty_sequence(png, cols, rows=rows, image_id=new, move_cursor=False, z=_Z[layer]).rstrip("\n")
    # the new id is normally free, removed with the frame before; cleared anyway, as a terminal sent a picture under an id it still
    # has (left by a run that ended without tidying up) may keep its old placement on the screen beside the new one
    return f"\x1b_Ga=d,d=I,i={new},q=2\x1b\\" + ui.at(row, col) + sequence + f"\x1b_Ga=d,d=I,i={old},q=2\x1b\\"


def _grids(s, ui, only=None):
    """Through tmux, the cells that show the palette and the drawing, or `only` those layers: printed before the menus, which then
    cover them."""
    out = []
    for layer, cols, rows, top, left in (("palette", ui.per_row * ui.button_cols, ui.palette_rows, ui.palette_top, ui.palette_left),
                                         ("structure", ui.canvas_cols, ui.canvas_rows, ui.top, ui.left)):
        if layer in s["flips"] and (only is None or layer in only):
            image = _IDS[layer] + s["flips"][layer]
            out.append(placeholders.cells(image, cols, rows, lambda r, c, top=top, left=left: ui.at(top + r, left + c)))
    return "".join(out)


def _composite(*pngs):
    """Pictures laid one over another, for tmux, where a cell can show only one."""
    layers = [Image.open(BytesIO(png)).convert("RGBA") for png in pngs]
    combined = layers[0]
    for layer in layers[1:]:
        combined = Image.alpha_composite(combined, layer.resize(combined.size))
    buffer = BytesIO()
    combined.save(buffer, "PNG")
    return buffer.getvalue()


def _border(s, ui, colours):
    """The box's edge, with its title and buttons."""
    dim, ink = f"\x1b[{colours['dim']}m", f"\x1b[{colours['ink']}m"
    controls = ui.controls()
    room = ui.cols - 4 - sum(len(label) for _, label, _ in controls)
    title = f" {_fit_text(ui.title, room)} " if ui.title else ""
    out = [ui.at(-1, -1, f"{dim}╭─\x1b[1m{ink}{title}\x1b[22m{dim}{'─' * (ui.cols - 1 - len(title))}╮")]
    for row in range(ui.rows):
        out.append(ui.at(row, -1, f"{dim}│") + ui.at(row, ui.cols, f"{dim}│"))
    out.append(ui.at(ui.rows, -1, f"{dim}╰{'─' * ui.cols}{RESIZE_GRIP if ui.window else '╯'}"))
    for name, label, col in [*ui.buttons(), *controls]:
        chosen = s["button"] == name
        style = f"\x1b[{colours['pick']};{colours['on_pick']}m" if chosen else f"\x1b[{colours['bar']};{colours['ink']}m"
        out.append(ui.at(-1 if name in dict(WINDOW_BUTTONS) else ui.rows, col, style + label))
    out.append("\x1b[0m")
    return "".join(out)


def _backing(theme):
    """One pixel, stretched over the box, so that whatever a host has drawn below doesn't show through the drawing."""
    buffer = BytesIO()
    Image.new("RGBA", (1, 1), themes.panel(theme)).save(buffer, "PNG")
    return buffer.getvalue()


def images_gone():
    """Remove all of the editor's images, for a host closing the box."""
    return "".join(placeholders.delete_sequence(base + flip) for base in _IDS.values() for flip in (0, 1))


def frame(s, ui, theme):
    """What to print for the current state: only the layers that changed, or nothing if the screen already shows it."""
    smiles, problem, wrong = analyse(s)
    colours = themes.chrome(theme)
    placed = ui.inline and [ui.top, ui.left, ui.canvas_rows, ui.canvas_cols, ui.palette_top, ui.palette_left, ui.bar_row, ui.strip]
    screen = [ui.cols, ui.rows, ui.cell_width, ui.cell_height, theme, ui.box, ui.window, ui.compact, placed]

    def digest(*parts):
        return hashlib.sha256(json.dumps([screen, *parts]).encode()).hexdigest()[:16]

    active = next((i for i, entry in enumerate(PALETTE) if _is_active(s, entry[1])), None)
    marks = _marks(s, ui, wrong)
    popups = _popups(s, ui, colours)
    menu_bar = "" if ui.compact else _menu_bar(s, ui, colours)
    status = _bar(s, ui, smiles, problem, colours) if ui.inline else _status(s, ui, smiles, problem, colours)
    keys = {"palette": digest(active, s["tip"]), "structure": digest(s["atoms"], s["bonds"], s["view"], s["notes"], ui.inline and _draft(s)),
            "overlay": digest(marks, s["view"]), "text": digest(popups, menu_bar, status, ui.title, s["button"])}
    if ui.boxed:
        keys["backing"] = digest()
    notes_told = ""
    if ui.inline:
        told = {"edge": ui.left + ui.canvas_cols, "notes": [{"text": text, "row": row, "by": "user" if "user" in flags else "agent",
                                                         **({"draft": True} if "draft" in flags else {})}
                                                        for text, _, row, flags in inline_notes(s, ui)]}
        keys["notes"] = digest(told)
        if told["notes"] or s["shown"].get("notes"):
            notes_told = HOST_NOTES.format(base64.b64encode(json.dumps(told).encode()).decode())
    tmux = placeholders.active.get()
    if tmux:
        # one picture a cell: the marks go into the drawing, and the box's backing isn't needed, as its cells cover what's below
        keys["structure"] = digest(s["atoms"], s["bonds"], s["view"], s["notes"], marks)
        del keys["overlay"]
        keys.pop("backing", None)
    size = f"{ui.cols}x{ui.rows}+{ui.origin[0]}+{ui.origin[1]}" + (f"@{placed}" if placed else "")
    resized = s["size"] != size
    if not resized and all(s["shown"].get(name) == keys[name] for name in keys):
        return ""
    out = ["\x1b[?2026h"]  # terminals that know this show the whole frame at once
    # through tmux, only the pictures that changed have their cells printed again, so that tmux has less to redraw
    regrid = [name for name in ("palette", "structure") if s["shown"].get(name) != keys[name]]
    if tmux and regrid:
        s["shown"]["text"] = None  # the grids are printed with the text, and the menus over them
    if resized:
        if ui.boxed:  # what is beside the box is the host's
            out.append(images_gone())
            out.extend(ui.at(row, -1, " " * (ui.cols + 2)) for row in range(-1, ui.rows + 1))
        elif ui.inline:  # all but the editor's own places are
            out.append(images_gone() + notes_told + _host_backdrop(ui))
        else:
            out.append("\x1b[2J")  # which also removes the images, so all of them are sent again
        s["size"] = size
        s["shown"] = {}
    if not tmux and ui.boxed and s["shown"].get("backing") != keys["backing"]:
        out.append(_place("backing", s, ui, _backing(theme), ui.cols + 2, ui.rows + 2, -1, -1))
    if s["shown"].get("palette") != keys["palette"] and ui.tools:
        shown = tuple(_ICON_NAMES[i] for i in ui.tools)
        png = editor_icons.palette_image(shown, ui.per_row, ui.button_cols * ui.cell_width, 2 * ui.cell_height,
                                         ui.tools.index(active) if active in ui.tools else None,
                                         ui.tools.index(s["tip"]) if s["tip"] in ui.tools else None, theme)
        out.append(_place("palette", s, ui, png, ui.per_row * ui.button_cols, ui.palette_rows, ui.palette_top, ui.palette_left))
    if s["shown"].get("structure") != keys["structure"]:
        drawn = draw_structure(s, ui, theme)
        if tmux and marks:
            drawn = _composite(drawn, draw_overlay(s, ui, marks))
        out.append(_place("structure", s, ui, drawn, ui.canvas_cols, ui.canvas_rows, ui.top, ui.left))
    if not tmux and s["shown"].get("overlay") != keys["overlay"]:
        if marks:
            out.append(_place("overlay", s, ui, draw_overlay(s, ui, marks), ui.canvas_cols, ui.canvas_rows, ui.top, ui.left))
        else:
            out.append(f"\x1b_Ga=d,d=I,i={_IDS['overlay'] + s['flips'].get('overlay', 0)},q=2\x1b\\")
    notes_changed = ui.inline and s["shown"].get("notes") != keys["notes"]
    if s["shown"].get("text") != keys["text"] or notes_changed:
        covered = s["cover"]
        if notes_changed and not resized:  # the host draws its bubbles again, with the rest of its screen
            out.append(notes_told + _host_backdrop(ui))
        elif covered and ui.inline:  # a menu may have covered the host's text too
            out.append(_host_backdrop(ui))
        elif covered:  # plain cells again, so that the images show through where a menu was
            for row in range(ui.top, ui.top + ui.canvas_rows):
                out.append(ui.blank(row))
        s["cover"] = bool(popups)
        if tmux:  # everything after a resize or a menu, which cleared cells; otherwise only what changed
            out.append(_grids(s, ui, None if covered or resized else regrid))
        if ui.boxed:
            out.append(_border(s, ui, colours))
        out.append(menu_bar)
        if ui.inline:
            out.append(status)
        else:
            for index, text in enumerate(status):
                row = ui.rows - STATUS_ROWS + index
                out.append(ui.at(row, 0, ui.erase()) + ui.at(row, 0, text))
        out.append(popups)
    s["shown"] = keys
    out.append("\x1b[?2026l")
    return "".join(out)


# Input


_TOKEN = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[([0-9;]*)([A-Za-z~])|\x1bO([A-Za-z])|\x1b[\]P_^]([^\x07\x1b]*)(?:\x07|\x1b\\)", re.S)
_INCOMPLETE = re.compile(r"\x1b(\[[<0-9;]*|O|[\]P_^][^\x07\x1b]*\x1b?)?$")
_COPY_STATUS = re.compile(r"5522;.*type=write:status=([A-Z]+)")
_ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}
_SS3_KEYS = {"P": "f1", "Q": "f2", "R": "f3", "S": "f4"}
_TILDE_KEYS = {"3": "delete", "12": "f2", "15": "f5", "17": "f6", "18": "f7", "19": "f8", "21": "f10"}
_CONTROL_KEYS = {"\r": "enter", "\n": "enter", "\t": "tab", "\x7f": "backspace", "\b": "backspace", "\x03": "interrupt", "\x11": "quit",
                 "\x0c": "redraw", "\x1a": "undo", "\x19": "redo", "\x01": "select_all", "\x0b": "tidy"}


def parse_events(raw):
    """Split raw input into events, and return them with any unfinished sequence left at the end."""
    events, position = [], 0
    while position < len(raw):
        match = _TOKEN.match(raw, position)
        if match:
            position = match.end()
            if match[4]:
                events.append(("mouse", int(match[1]), int(match[2]), int(match[3]), match[4] == "m"))
            elif match[6]:
                modifier = match[5].split(";")[1] if ";" in match[5] else ""
                name = _ARROWS.get(match[6])
                if name:
                    events.append(("key", ("shift-" if modifier == "2" else "") + name))
                elif match[6] == "Z":
                    events.append(("key", "shift-tab"))
                elif (match[6], match[5]) in (("u", "13;2"), ("~", "27;2;13")):  # shift+enter (kitty's keys, xterm's modifyOtherKeys)
                    events.append(("key", "shift-enter"))
                elif match[6] == "~" and match[5].split(";")[0] in _TILDE_KEYS:
                    events.append(("key", _TILDE_KEYS[match[5].split(";")[0]]))
            elif match[7] in _ARROWS:
                events.append(("key", _ARROWS[match[7]]))
            elif match[7] in _SS3_KEYS:
                events.append(("key", _SS3_KEYS[match[7]]))
            elif match[8] is not None:  # a terminal's answer, as Kitty gives to a copy, which mustn't turn into keys
                answer = _COPY_STATUS.match(match[8])
                if answer:
                    events.append(("clipboard", answer[1]))
            continue
        if raw[position] == "\x1b":
            rest = raw[position:]
            if _INCOMPLETE.fullmatch(rest) and len(rest) > 1:
                return events, rest
            events.append(("key", "esc"))
            position += 1
            continue
        char = raw[position]
        position += 1
        if char in _CONTROL_KEYS:
            events.append(("key", _CONTROL_KEYS[char]))
        elif char.isprintable():
            events.append(("key", char))
    return events, ""


def _row_height(s, ui):
    """One terminal row in bond lengths. Mouse reports in cells can be half of this out, so targets must be at least that wide."""
    return ui.cell_height / ui.cell_width / s["view"][2]


def _atom_at(s, ui, x, y):
    best, nearest = None, max(0.35, 0.7 * _row_height(s, ui))
    for atom in s["atoms"]:
        distance = math.hypot(atom[2] - x, atom[3] - y)
        if distance < nearest:
            best, nearest = atom[0], distance
    return best


def _bond_at(s, ui, x, y):
    best, nearest = None, max(0.25, 0.7 * _row_height(s, ui))
    for bond in s["bonds"]:
        (ax, ay), (bx, by) = _bond_ends(s, bond)
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        along = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / span)) if span else 0
        distance = math.hypot(ax + along * dx - x, ay + along * dy - y)
        if distance < nearest:
            best, nearest = ["b", bond[0], bond[1]], distance
    return best


def _target_at(s, ui, x, y):
    atom = _atom_at(s, ui, x, y)
    return ["a", atom] if atom else _bond_at(s, ui, x, y)


def _target(s):
    """The hotspot, which keys act on: what the pointer is over, or else what the arrow keys or the last edit moved it to."""
    return s["hover"] or s["focus"]


def _new_bond(s, x, y):
    """A bond from a point, up and to the right, in the kind the tool has."""
    _save(s)
    first = _add_atom(s, "C", x, y)
    second = None if first is None else _add_atom(s, "C", x + math.cos(math.radians(30)), y + math.sin(math.radians(30)))
    if second is None:
        s["undo"].pop()
        return
    _set_bond(s, first, second, _bond_kind(s))
    s["focus"], s["last"] = ["a", second], ["b", first, second]


def _bond_kind(s):
    return s["bond"] if s["tool"] == "bond" else SINGLE


def _open_label(s, target, x, y):
    if target and target[0] != "a":
        s["message"] = "Point at an atom, or an empty space."
        return
    s["label"] = {"target": target, "at": [x, y], "text": ""}
    if target:
        s["label"]["at"] = _atom(s, target[1])[2:4]
        s["focus"] = target


def _erase(s, target):
    """The eraser takes a double or triple bond down one order, as ChemDraw's does, and anything else away."""
    bond = _bond(s, target[1], target[2]) if target and target[0] == "b" else None
    if bond and bond[2] in (DOUBLE, TRIPLE):
        _save(s)
        bond[2] = DOUBLE if bond[2] == TRIPLE else SINGLE
    else:
        _delete_target(s, target)


def _select_click(s, target, adding):
    if not target or target[0] != "a":
        s["focus"], s["selected"] = target, s["selected"] if adding else []
    elif adding and target[1] in s["selected"]:
        s["selected"] = [i for i in s["selected"] if i != target[1]]
    else:
        s["selected"] = [*s["selected"], target[1]] if adding else [target[1]]


def _click(s, ui, target, x, y, code=0):
    tool = s["tool"]
    if tool == "erase":
        _erase(s, target)
    elif tool == "ring":
        _ring_on_target(s, target, s["ring"], x, y, sprout=bool(code & 8))  # Alt
    elif tool == "select":
        _select_click(s, target, bool(code & 4))  # Shift
    elif tool == "text":
        _open_label(s, target, x, y)
    elif tool in ("plus", "minus"):
        _charge(s, target, 1 if tool == "plus" else -1)
    elif tool == "rlabel":
        _next_rgroup_label(s, target)
    elif tool == "pan":
        pass
    elif target is None:
        _new_bond(s, x, y)
    elif target[0] == "a":
        s["focus"] = target
        _grow(s, target[1], "C", _bond_kind(s))
    else:
        s["focus"], s["last"] = target, target
        _cycle_bond(s, target, _bond_kind(s))


def _cycle_bond(s, target, kind):
    """Set a bond. With the single bond tool, or the kind it already has, step on to the next: order or wedge direction."""
    bond = _bond(s, target[1], target[2])
    _save(s)
    if kind == SINGLE:
        bond[2] = {SINGLE: DOUBLE, DOUBLE: TRIPLE}.get(bond[2], SINGLE)
    elif bond[2] == kind and kind in (WEDGE, HASH):
        bond[0], bond[1] = bond[1], bond[0]
    elif bond[2] == kind:
        bond[2] = SINGLE
    else:
        _set_kind(s, bond, kind)


def _set_kind(s, bond, kind):
    """Change a bond's kind. A wedge's narrow end marks the stereocentre, which is more likely the more connected atom."""
    if kind in (WEDGE, HASH) and len(_neighbours(s, bond[1])) > len(_neighbours(s, bond[0])):
        bond[0], bond[1] = bond[1], bond[0]
    bond[2] = kind


def _finish_chain(s, ui, press):
    points = _chain_points(s, ui, press)
    if len(s["atoms"]) + len(points) > MAX_ATOMS:
        s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
        return
    _save(s)
    previous = press["target"][1] if press["target"] and press["target"][0] == "a" else _add_atom(s, "C", *points[0])
    for x, y in points[1:]:
        current = _vertex(s, x, y)
        if current != previous and not _bond(s, previous, current):
            _set_bond(s, previous, current, SINGLE)
        previous = current
    s["focus"], s["last"] = ["a", previous], None
    s["message"] = f"A chain of {len(points) - 1} bonds."


def _finish_drag(s, ui, press):
    if s["tool"] == "chain":
        _finish_chain(s, ui, press)
        return
    end_x, end_y, reached = _drag_end(s, ui, press)
    _save(s)
    if press["target"] and press["target"][0] == "a":
        start = press["target"][1]
    else:
        start = _add_atom(s, "C", *press["model"])
    if start is None:
        s["undo"].pop()
        return
    end = reached if reached else _add_atom(s, "C", end_x, end_y)
    if end is None:
        s["undo"].pop()
        return
    _set_bond(s, start, end, s["bond"])
    s["focus"], s["last"] = ["a", end], ["b", start, end]


def _finish_marquee(s, press):
    (x0, y0), (x1, y1) = press["model"], s["drag"]
    low_x, high_x, low_y, high_y = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    s["selected"] = [a[0] for a in s["atoms"] if low_x <= a[2] <= high_x and low_y <= a[3] <= high_y]
    s["focus"] = None


def _in_selection(s, x, y):
    atoms = [a for a in s["atoms"] if a[0] in s["selected"]]
    if not atoms:
        return False
    return (min(a[2] for a in atoms) - 0.3 <= x <= max(a[2] for a in atoms) + 0.3
            and min(a[3] for a in atoms) - 0.3 <= y <= max(a[3] for a in atoms) + 0.3)


def _charge(s, target, step):
    if not target or target[0] != "a":
        s["message"] = "Point at an atom first."
        return
    atom = _atom(s, target[1])
    if abs(atom[4] + step) <= 8:
        _save(s)
        atom[4] += step


def _set_element(s, target, symbol):
    atom = _atom(s, target[1])
    if atom and (atom[1] != symbol or atom[1] in ("R#", "L")):
        _save(s)
        _relabel(atom, symbol)


def _set_bond_kind(s, target, kind):
    bond = _bond(s, target[1], target[2])
    if not bond:
        return
    if bond[2] == kind:
        if kind in (WEDGE, HASH):
            _cycle_bond(s, target, kind)  # pressed again, it turns round
    else:
        _save(s)
        _set_kind(s, bond, kind)


def _next_rgroup_label(s, target):
    """Make an atom the lowest free R-group, or an R-group the next number up, as Ketcher's R-group label tool does from its dialog."""
    if not target or target[0] != "a":
        s["message"] = "Point at an atom to make it an R-group."
        return
    atom = _atom(s, target[1])
    _save(s)
    number = _extras(atom)["r"] % markush.MAX_RGROUP + 1 if atom[1] == "R#" else _free_rgroup(s)
    _relabel(atom, "R#")
    _set_extra(atom, "r", number)
    s["message"] = f"R{number}. Draw its definitions, select them and choose R-group Definition from the selection's menu."


def _cycle_extra(s, target, key, values, what):
    """Step an atom's property through `values`, None meaning without it."""
    if not target or target[0] != "a":
        s["message"] = "Point at an atom first."
        return
    atom = _atom(s, target[1])
    if key == "rad" and atom[1] not in _SYMBOLS:
        s["message"] = f"Only an element can be a radical, not {_symbol(atom)}."
        return
    current = _extras(atom).get(key)
    value = values[(values.index(current) + 1) % len(values)] if current in values else values[1]
    _save(s)
    _set_extra(atom, key, value)
    s["message"] = what.get(value, "")


_RADICALS = {None: "No radical.", 2: "A radical: one unpaired electron (doublet).", 1: "A carbene-like singlet: two paired electrons.",
             3: "A triplet: two unpaired electrons."}
_POINTS = {None: "No attachment point.", 1: "The first attachment point: where this R-group definition bonds to the core.",
           2: "The second attachment point, for a definition that bonds to the core twice.", 3: "Both attachment points on this atom."}


def _selected_or(s, target):
    """The atoms an action is for: the selection if the target is in it or there is no target, else the target's atom."""
    if s["selected"] and (not target or target[0] != "a" or target[1] in s["selected"]):
        return list(s["selected"])
    return [target[1]] if target and target[0] == "a" else []


def _vary(s, target):
    """Make a bond from a substituent a variable attachment: to any of the selected atoms."""
    bond = _bond(s, target[1], target[2]) if target and target[0] == "b" else None
    chosen = set(s["selected"])
    if not bond or not chosen:
        s["message"] = "Select the atoms the substituent may go to, as in a ring, then right-click its bond to one of them."
        return
    inside = [i for i in bond[:2] if i in chosen]
    if len(inside) != 1:
        s["message"] = "One end of the bond goes on a selected atom, and the substituent's end on one that isn't."
        return
    anchor, substituent = inside[0], bond[1] if bond[0] == inside[0] else bond[0]
    ends = [anchor] + [i for i in s["selected"] if i != anchor and i != substituent][:MAX_LIST - 1]
    if len(ends) < 2:
        s["message"] = "Select at least two atoms the substituent may go to."
        return
    _save(s)
    bond[0], bond[1] = substituent, anchor
    _set_bond_extra(bond, "ends", ends)
    s["selected"] = []
    s["message"] = f"The substituent may go on any of {len(ends)} atoms."


def _topology(s, target, value):
    bond = _bond(s, target[1], target[2]) if target and target[0] == "b" else None
    if bond:
        _save(s)
        _set_bond_extra(bond, "topo", value)


def _unbracket(s):
    chosen = set(s["selected"])
    groups = {_extras(a)["sru"][0] for a in s["atoms"] if a[0] in chosen and "sru" in _extras(a)}
    if not groups:
        s["message"] = "None of the selected atoms is in a repeating unit."
        return
    _save(s)
    for atom in s["atoms"]:
        if _extras(atom).get("sru", [0])[0] in groups:
            _set_extra(atom, "sru", None)


_PROMPT_TEXT = {"note": ("Note", "Enter puts it on {where}, Shift+Enter starts a new line, and an empty note takes it off."),
                "isotope": ("Mass number", "Such as 13 for carbon-13 or 2 for deuterium. Empty takes it off."),
                "rgroup": ("R-group number", f"1 to {markush.MAX_RGROUP}: the atom becomes R1 and so on."),
                "list": ("Atom list", "Elements with commas, such as C,N,O; !C,N or NOT C,N for any but those."),
                "link": ("Link node repeats", "Such as 1-4: the atom and its two bonds repeat that many times. Empty takes it off."),
                "bracket": ("Repeating unit", "n, or a range such as 1-3: how many times the selected atoms repeat."),
                "stereo": ("Stereo group", "abs, and1 to and9 (racemic), or1 to or9 (relative). Empty takes it off."),
                "fragment": ("R-group definition", "Its R number, such as 1: the selected atoms become one of R1's definitions. Empty undoes it."),
                "logic": ("R-group logic", "Such as R1 >0 H if R2: how often R1 occurs, H for rest hydrogen, and the R-group it needs.")}


def _open_prompt(s, kind, target):
    targets = [["a", i] for i in _selected_or(s, target)] if kind not in ("logic",) else [["a", 0]]
    if kind in ("bracket", "fragment") and not s["selected"]:
        s["message"] = "Select the atoms first, then right-click them."
        return
    if not targets:
        s["message"] = "Point at an atom, or select some, first."
        return
    text = ""
    if kind != "logic" and len(targets) == 1:
        extras = _extras(_atom(s, targets[0][1]))
        text = {"isotope": str(extras.get("iso", "")), "rgroup": str(extras.get("r", "")), "stereo": extras.get("st", ""),
                "list": ("!" if extras.get("not") else "") + ",".join(extras.get("list", [])),
                "link": "-".join(map(str, extras.get("link", [])))}.get(kind, "")
    s["prompt"] = {"targets": targets, "text": text, "kind": kind}


def _apply_prompt(s, kind, targets, text):
    """What a typed value does; sets a message, without changing anything, if it means nothing."""
    text = text.strip()
    atoms = [_atom(s, t[1]) for t in targets if t[0] == "a" and _atom(s, t[1])]
    if kind == "logic":
        return _apply_logic(s, text)
    if not atoms:
        return
    if kind == "isotope":
        if text and not (text.isdigit() and 1 <= int(text) <= 300):
            s["message"] = "A mass number is a whole number from 1 to 300."
            return
        real = [a for a in atoms if a[1] in _SYMBOLS]
        if not real:
            s["message"] = "Only an element has a mass number."
            return
        _save(s)
        for atom in real:
            _set_extra(atom, "iso", int(text) if text else None)
    elif kind == "rgroup":
        number = text.upper().removeprefix("R")
        if not (number.isdigit() and 1 <= int(number) <= markush.MAX_RGROUP):
            s["message"] = f"An R-group number goes from 1 to {markush.MAX_RGROUP}."
            return
        _save(s)
        for atom in atoms:
            _relabel(atom, "R#")
            _set_extra(atom, "r", int(number))
    elif kind == "list":
        listed = _parse_list(text if "," in text or text.startswith(("[", "!")) or text.lower().startswith("not") else f"[{text}]")
        if not listed or isinstance(listed, str):
            s["message"] = listed or "Write the elements with commas, such as C,N,O."
            return
        _save(s)
        for atom in atoms:
            _relabel(atom, "L")
            _set_extra(atom, "list", listed[0])
            _set_extra(atom, "not", True if listed[1] else None)
    elif kind == "link":
        found = re.fullmatch(r"(?:(\d{1,2})\s*-\s*)?(\d{1,2})", text)
        if text and (not found or not 1 <= int(found[1] or 1) < int(found[2]) <= 20):
            s["message"] = "Write how many times it repeats, as 1-4, up to 20."
            return
        if text and any(len(_neighbours(s, a[0])) != 2 for a in atoms):
            s["message"] = "A link node is an atom with two bonds, such as one in a chain."
            return
        _save(s)
        for atom in atoms:
            _set_extra(atom, "link", [int(found[1] or 1), int(found[2])] if text else None)
    elif kind == "bracket":
        if not _BRACKET_LABEL.fullmatch(text):
            s["message"] = "Write n, another letter, or a range such as 1-3."
            return
        used = {_extras(a)["sru"][0] for a in s["atoms"] if "sru" in _extras(a)}
        number = next(n for n in range(1, MAX_BRACKET + 2) if n not in used)
        if number > MAX_BRACKET:
            s["message"] = f"The editor holds at most {MAX_BRACKET} repeating units."
            return
        _save(s)
        for atom in atoms:
            _set_extra(atom, "sru", [number, text])
    elif kind == "stereo":
        value = text.lower().replace(" ", "")
        if value and not _STEREO_GROUP.fullmatch(value):
            s["message"] = "A stereo group is abs, and1 to and9, or or1 to or9."
            return
        _save(s)
        for atom in atoms:
            _set_extra(atom, "st", value or None)
    elif kind == "fragment":
        number = text.upper().removeprefix("R")
        if text and not (number.isdigit() and 1 <= int(number) <= markush.MAX_RGROUP):
            s["message"] = f"An R-group number goes from 1 to {markush.MAX_RGROUP}."
            return
        # all of each fragment the selection touches, as a definition is a whole fragment
        chosen, todo = set(), [a[0] for a in atoms]
        while todo:
            current = todo.pop()
            if current not in chosen:
                chosen.add(current)
                todo.extend(_neighbours(s, current))
        if text and any(a[1] == "R#" and a[0] in chosen for a in s["atoms"]) and len(chosen) == len(s["atoms"]):
            s["message"] = "A definition is drawn apart from the structure, not bonded to it."
            return
        _save(s)
        for atom in s["atoms"]:
            if atom[0] in chosen:
                _set_extra(atom, "rg", int(number) if text else None)
        s["selected"] = []
        if text and not any(_extras(_atom(s, i)).get("ap") for i in chosen):
            s["message"] = f"A definition of R{number}. Press . on the atom it attaches by; until then it's its first atom."


def _apply_logic(s, text):
    found = re.fullmatch(r"R?(\d{1,2})\s*:?\s*([<>]?\d{1,2}(?:-\d{1,2})?(?:,[<>]?\d{1,2}(?:-\d{1,2})?)*)?\s*(H)?\s*(?:(?:if|then)\s*R?(\d{1,2}))?",
                         text, re.I)
    if not found or not 1 <= int(found[1]) <= markush.MAX_RGROUP or (found[4] and not 1 <= int(found[4]) <= markush.MAX_RGROUP):
        s["message"] = "Write it as R1 >0 H if R2: an R-group, then any of how often it occurs, H, and if another."
        return
    number, then = int(found[1]), int(found[4] or 0)
    if then == number:
        s["message"] = "An R-group can't depend on itself."
        return
    _save(s)
    rule = {"then": then, "rest_h": bool(found[3]), "occurrence": found[2] or ""}
    if rule["then"] or rule["rest_h"] or rule["occurrence"]:
        s["rlogic"][str(number)] = rule
    else:
        s["rlogic"].pop(str(number), None)
    s["message"] = f"R{number}: " + (markush.write_logic({number: rule}) or "no logic")


def _transform(s, flip_x, flip_y, rotate):
    """Flip or turn the selection, or the whole drawing, about its middle."""
    ids = set(s["selected"]) or {a[0] for a in s["atoms"]}
    atoms = [a for a in s["atoms"] if a[0] in ids]
    if not atoms:
        return
    _save(s)
    cx, cy = sum(a[2] for a in atoms) / len(atoms), sum(a[3] for a in atoms) / len(atoms)
    for atom in atoms:
        dx, dy = atom[2] - cx, atom[3] - cy
        if rotate:
            dx, dy = -dy, dx
        atom[2], atom[3] = round(cx + (-dx if flip_x else dx), 4), round(cy + (-dy if flip_y else dy), 4)
    if flip_x != flip_y:  # a mirror image: its wedges point the other way, so that the stereochemistry stays as it was
        for bond in s["bonds"]:
            if bond[0] in ids and bond[1] in ids and bond[2] in (WEDGE, HASH):
                bond[2] = HASH if bond[2] == WEDGE else WEDGE


def _aromatize(s, aromatic):
    """Ketcher's Aromatize and Dearomatize: aromatic rings drawn with aromatic bonds, or with alternating single and double ones."""
    mol = _mol_with_ids(s["atoms"], s["bonds"])
    if mol is None:
        s["message"] = "That needs a valid structure."
        return
    if not aromatic:
        try:
            Chem.Kekulize(mol, clearAromaticFlags=True)
        except (Chem.KekulizeException, RuntimeError, ValueError):
            s["message"] = "RDKit can't place the double bonds in these rings."
            return
    changes = []
    for rd in mol.GetBonds():
        first, second = (rd.GetBeginAtom(), rd.GetEndAtom())
        if not (first.HasProp("molterm_id") and second.HasProp("molterm_id")):
            continue
        bond = _bond(s, first.GetIntProp("molterm_id"), second.GetIntProp("molterm_id"))
        if not bond:
            continue
        if aromatic and rd.GetIsAromatic() and bond[2] in (SINGLE, DOUBLE):
            changes.append((bond, AROMATIC))
        elif not aromatic and bond[2] == AROMATIC:
            changes.append((bond, DOUBLE if rd.GetBondType() == Chem.BondType.DOUBLE else SINGLE))
    if not changes:
        s["message"] = "There is nothing to " + ("aromatize." if aromatic else "dearomatize.")
        return
    _save(s)
    for bond, kind in changes:
        bond[2] = kind


def _add_hydrogens(s):
    mol = _mol_with_ids(s["atoms"], s["bonds"])
    if mol is None:
        s["message"] = "That needs a valid structure."
        return
    with rdBase.BlockLogs():
        full = Chem.AddHs(mol, addCoords=True)
    conformer = full.GetConformer()
    added = [atom for atom in full.GetAtoms() if not atom.HasProp("molterm_id") and atom.GetAtomicNum() == 1]
    if not added:
        s["message"] = "No hydrogens to add."
        return
    if len(s["atoms"]) + len(added) > MAX_ATOMS:
        s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
        return
    _save(s)
    for atom in added:
        point = conformer.GetAtomPosition(atom.GetIdx())
        parent = atom.GetNeighbors()[0]
        ident = _add_atom(s, "H", round(point.x / 1.5, 4), round(point.y / 1.5, 4))
        if parent.HasProp("molterm_id"):
            _set_bond(s, parent.GetIntProp("molterm_id"), ident, SINGLE)
    s["message"] = f"{len(added)} hydrogens added."


def _remove_hydrogens(s):
    plain = [a[0] for a in s["atoms"] if a[1] == "H" and not a[4] and not _extras(a) and len(_neighbours(s, a[0])) == 1
             and _bond(s, a[0], _neighbours(s, a[0])[0])[2] == SINGLE]
    if not plain:
        s["message"] = "No plain hydrogens to remove."
        return
    _save(s)
    gone = set(plain)
    s["atoms"] = [a for a in s["atoms"] if a[0] not in gone]
    s["bonds"] = [b for b in s["bonds"] if b[0] not in gone and b[1] not in gone]
    _forget_missing(s)
    s["message"] = f"{len(plain)} hydrogens removed."


def _check(s):
    """Ketcher's Check Structure, as one line: RDKit's problems, and what makes a Markush structure incomplete."""
    found = []
    if s["atoms"]:
        with rdBase.BlockLogs():
            raw = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]), sanitize=False, removeHs=False)
            found += [p.Message() for p in Chem.DetectChemistryProblems(raw)] if raw is not None else ["RDKit can't read it."]
    labels = {_extras(a)["r"] for a in s["atoms"] if a[1] == "R#"}
    defined = set(_fragments(s))
    found += [f"R{n} has no definition" for n in sorted(labels - defined)]
    found += [f"R{n} is defined but not used" for n in sorted(defined - labels)]
    found += [f"the link node {_symbol(a)} needs two bonds" for a in s["atoms"] if "link" in _extras(a) and len(_neighbours(s, a[0])) != 2]
    s["message"] = ("Check: " + "; ".join(found[:4]) + ("; ..." if len(found) > 4 else "") + ".") if found else "Check: no problems found."


def _calculated(s):
    """Ketcher's Calculated Values: formula, weights and elemental analysis of the structure, or its core if it's a Markush structure."""
    atoms, bonds = _core(s)
    mol = _mol_with_ids(atoms, bonds) if atoms else None
    if mol is None:
        s["message"] = "Calculated values need a valid structure."
        return
    weight = Descriptors.MolWt(mol)
    counts = {}
    for atom in Chem.AddHs(mol).GetAtoms():
        if atom.GetAtomicNum():
            counts[atom.GetSymbol()] = counts.get(atom.GetSymbol(), 0) + Chem.GetPeriodicTable().GetAtomicWeight(atom.GetAtomicNum())
    analysis = " ".join(f"{symbol} {mass / weight * 100:.2f}%" for symbol, mass in sorted(counts.items(), key=lambda p: (p[0] != "C", p[0] != "H", p[0])))
    core = " (the core)" if any(a[1] in MARKUSH for a in atoms) else ""
    s["message"] = (f"{rdMolDescriptors.CalcMolFormula(mol)}{core}   MW {weight:.2f}   exact {Descriptors.ExactMolWt(mol):.4f}   {analysis}")


def _copy_molfile(s):
    atoms, bonds = _core(s)
    if not atoms:
        s["message"] = "Nothing to copy: the drawing is empty."
        return
    s["clipboard"] = clipboard_sequence(to_molblock(atoms, bonds))
    s["message"] = "Molfile (V3000) sent to the clipboard" + (", without the R-group definitions." if _fragments(s) else ".")


def _zoom(s, factor):
    s["view"][2] = min(max(s["view"][2] * factor, ZOOM_RANGE[0]), ZOOM_RANGE[1])


def clipboard_sequence(text):
    """Ask the terminal to put text on the system clipboard (OSC 52). It may refuse without saying so, as tmux does unless set to allow it."""
    return annotate.clipboard_sequence([("text/plain", text.encode())])


def take_clipboard(s):
    """The sequence for what was just copied, once: it's not part of the drawing, so it mustn't go on in the state."""
    return s.pop("clipboard", None) or ""


COPIED = {"smiles": "SMILES sent to the clipboard.",
          "svg": "SVG sent to the clipboard, as text and as a picture.",
          "png": "PNG sent to the clipboard, with the SMILES as its text. Terminals without Kitty's clipboard protocol take only the SMILES.",
          "all": "SMILES, SVG and PNG sent to the clipboard. Terminals without Kitty's clipboard protocol take only the SMILES."}


def _copy(s, kind="smiles", selection=False):
    """Copy the drawing, or the selection, as text and pictures with its annotations."""
    if selection and not s["selected"]:
        s["message"] = "Select some atoms first."
        return
    found = written(s, selection)
    if found:
        smiles, mol, notes = found
        s["clipboard"] = annotate.clipboard_sequence(annotate.clipboard(smiles, mol, notes, kind))
        s["message"] = ("The selection: " if selection else "") + COPIED[kind]
        return
    if not s["atoms"]:
        s["message"] = "Nothing to copy: the drawing is empty."
    else:
        problem = analyse(s)[1] if not selection else "the selection isn't valid chemistry on its own."
        s["message"] = f"Nothing to copy: {problem}"


def _annotation_targets(s, target):
    """What an annotation goes on: the atom or bond a menu was opened on, or else the selection with the bonds inside it."""
    if target:
        return [target]
    chosen = set(s["selected"])
    return [["a", ident] for ident in s["selected"]] + [["b", b[0], b[1]] for b in s["bonds"] if b[0] in chosen and b[1] in chosen]


def _entry(s, target):
    """The annotation list entry for an atom or bond, as [*ids, highlight, note], or None."""
    ids = target[1:2] if target[0] == "a" else sorted(target[1:])
    found = s["notes"]["atoms" if target[0] == "a" else "bonds"]
    return next((entry for entry in found if entry[:-2] == ids), None)


def _group(s, target):
    """The atoms of the highlighted group an atom or bond is in (see annotate.group)."""
    return annotate.group(s["notes"], [b[:2] for b in s["bonds"]], target[1:2] if target[0] == "a" else sorted(target[1:]))


def _annotate(s, targets, highlight=None, note=None, group=False):
    """Set the highlight or the note, or with both None clear them, on each target. "" takes one off. A note is the person's own,
    and with `group` it is about the highlighted group its target is in."""
    _save(s)
    for target in targets:
        entry = _entry(s, target)
        ids = target[1:2] if target[0] == "a" else sorted(target[1:])
        if entry is None:
            entry = [*ids, "", ""]
            s["notes"]["atoms" if target[0] == "a" else "bonds"].append(entry)
        if highlight is None and note is None:
            entry[-2:] = ["", ""]
        if highlight is not None:
            entry[-2] = highlight
        if note is not None:
            entry[-1] = annotate.clean_note(note)
            annotate.set_flags(s["notes"], ids, {"user", "group"} if group else {"user"})
    for name in ("atoms", "bonds"):
        s["notes"][name] = sorted(entry for entry in s["notes"][name] if entry[-2] or entry[-1])
    if len(s["notes"]["atoms"]) + len(s["notes"]["bonds"]) > annotate.MAX_NOTES:
        _restore(s, "undo", "redo")
        s["redo"].pop()
        s["message"] = f"The editor holds at most {annotate.MAX_NOTES} annotations."


def _annotation(s, kind, value, target):
    targets = _annotation_targets(s, target)
    if not targets:
        s["message"] = "Select atoms first, or right-click an atom or bond for its own menu."
    elif kind == "highlight":
        _annotate(s, targets, highlight=value)
    elif kind == "clear":
        _annotate(s, targets)
    elif kind == "note":
        known = _entry(s, targets[0]) if len(targets) == 1 else None
        s["prompt"] = {"targets": targets, "text": known[-1] if known else "", "kind": "note"}
        if value == "group" and len(targets) == 1:
            s["prompt"]["group"] = True


def _prompt_key(s, name):
    prompt = s["prompt"]
    if name == "esc":
        s["prompt"] = None
    elif name == "enter":
        kind = prompt.get("kind", "note")
        s["prompt"] = None
        if kind == "logic":
            _apply_prompt(s, kind, [], prompt["text"])
            return
        targets = [t for t in prompt["targets"] if _target_points(s, t)]
        if targets and kind == "note":
            _annotate(s, targets, note=prompt["text"], group=bool(prompt.get("group")))
        elif targets:
            _apply_prompt(s, kind, targets, prompt["text"])
    elif name == "backspace":
        prompt["text"] = prompt["text"][:-1]
    elif name == "shift-enter" and prompt.get("kind", "note") == "note":  # a note can have a few lines
        if len(prompt["text"]) < MAX_PROMPT and prompt["text"].count("\n") < annotate.MAX_NOTE_LINES - 1:
            prompt["text"] += "\n"
    elif len(name) == 1 and len(prompt["text"]) < MAX_PROMPT and name.isprintable():
        prompt["text"] += name


def _command(s, ui, name):
    """Run a command; returns what ends the editor, if it does: see `finish`."""
    if name == "quit":
        return True
    if name == "copy":
        _copy(s)
        return False
    if name == "clear_notes":
        if not annotate.is_empty(s["notes"]):
            _save(s)
            s["notes"] = annotate.empty()
        return False
    if name == "cancel":
        return CANCEL
    if name in ("undo", "redo"):
        _restore(s, name, "redo" if name == "undo" else "undo")
        if s["pointer"]:  # the pointer hasn't moved, but what is under it may have changed
            s["hover"] = _target_at(s, ui, *s["pointer"])
    elif name == "tidy":
        _tidy(s, ui)
    elif name == "fit":
        _fit(s, ui)
    elif name == "entry":
        s["entry"], s["help"] = "", False
    elif name == "help":
        s["help"] = not s["help"]
    elif name == "select_all":
        s["selected"] = [a[0] for a in s["atoms"]]
    elif name == "delete":
        _delete(s, _target(s))
    elif name == "zoom_in":
        _zoom(s, 1.2)
    elif name == "zoom_out":
        _zoom(s, 1 / 1.2)
    elif name == "actual":
        s["view"][2] = DEFAULT_ZOOM
    elif name in ("flip_h", "flip_v", "rotate"):
        _transform(s, name == "flip_h", name == "flip_v", name == "rotate")
    elif name in ("aromatize", "dearomatize"):
        _aromatize(s, name == "aromatize")
    elif name == "add_h":
        _add_hydrogens(s)
    elif name == "remove_h":
        _remove_hydrogens(s)
    elif name == "check":
        _check(s)
    elif name == "calc":
        _calculated(s)
    elif name == "logic":
        _open_prompt(s, "logic", None)
    elif name == "copy_molfile":
        _copy_molfile(s)
    return False


def _act(s, ui, action, target):
    """Run what a menu item says; returns True to quit."""
    kind, value = action
    if kind == "command":
        return _command(s, ui, value)
    if kind == "element":
        _set_element(s, target, value)
    elif kind == "kind":
        _set_bond_kind(s, target, value)
    elif kind == "charge":
        _charge(s, target, value)
    elif kind == "delete":
        _delete_target(s, target)
    elif kind == "label":
        _open_label(s, target, 0.0, 0.0)
    elif kind == "copy":
        _copy(s, *value)
    elif kind in ("highlight", "clear", "note"):
        _annotation(s, kind, value, target)
    elif kind == "show":
        _save(s)
        shown = s["notes"]["show"]
        s["notes"]["show"] = [name for name in annotate.SHOWN if (name in shown) != (name == value)]
    elif kind == "submenu":
        col, row = ui.to_cell(s, *_target_centre(s, target)) if target else (ui.left + 2, ui.top + 1)
        s["menu"], s["menu_hover"], s["menu_target"] = ["context", value, max(int(col), 0), max(int(row), 0)], None, target
    elif kind == "prompt":
        _open_prompt(s, value, target)
    elif kind == "radical":
        _cycle_extra(s, target, "rad", [None, 2, 1, 3], _RADICALS)
    elif kind == "point":
        _cycle_extra(s, target, "ap", [None, 1, 2, 3], _POINTS)
    elif kind == "vary":
        _vary(s, target)
    elif kind == "topo":
        _topology(s, target, value)
    elif kind == "unbracket":
        _unbracket(s)
    return False


def _delete(s, target):
    if s["selected"]:
        _delete_selection(s)
    elif not _delete_target(s, target):
        s["message"] = "Point at an atom or bond first."


def _tidy(s, ui):
    if not s["atoms"]:
        return
    mol = _mol_with_ids(s["atoms"], s["bonds"])
    if mol is None:
        s["message"] = "Clean Up needs a valid structure."
        return
    _save(s)
    AllChem.Compute2DCoords(mol)
    kept = Chem.Mol(mol)
    renamed = {atom.GetIntProp("molterm_id"): atom.GetIdx() + 1 for atom in mol.GetAtoms() if atom.HasProp("molterm_id")}  # _from_mol numbers them afresh
    notes = annotate.renumber(s["notes"], renamed)
    _from_mol(s, kept)
    s["notes"] = notes
    s["focus"], s["hover"], s["selected"] = None, None, []
    _fit(s, ui)


def _fit(s, ui):
    if not s["atoms"]:
        s["view"][:2] = [0.0, 0.0]
        return
    xs, ys = [a[2] for a in s["atoms"]], [a[3] for a in s["atoms"]]
    span_x, span_y = max(xs) - min(xs) + 2, max(ys) - min(ys) + 2
    ratio = ui.cell_height / ui.cell_width
    zoom = min(ui.canvas_cols / span_x, ui.canvas_rows * ratio / span_y, DEFAULT_ZOOM)
    s["view"] = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(zoom, ZOOM_RANGE[0])]


def _keep_corner(s, ui):
    """When a host makes an inline editor's drawing larger or smaller, what was at its top left corner stays there, so that the
    molecule stays put on the screen while the drawing's edges move."""
    place = [ui.top, ui.left, ui.canvas_rows, ui.canvas_cols]
    old = s.get("canvas")
    if old and old != place:
        ratio = ui.cell_height / ui.cell_width
        x, y, zoom = s["view"]
        corner_x, corner_y = x - old[3] / 2 / zoom, y + old[2] / 2 * ratio / zoom
        s["view"][:2] = [corner_x + ui.canvas_cols / 2 / zoom, corner_y - ui.canvas_rows / 2 * ratio / zoom]
    s["canvas"] = place


def _fit_to(s, ui, at):
    """Draw the molecule over a picture of it at `at` (row, col, rows, cols on the screen), at the picture's scale as near as can be:
    molterm draws a picture with graphics.BOND_CELLS columns to a bond, smaller if it must fit."""
    if not s["atoms"]:
        return _fit(s, ui)
    row, col, rows, cols = at
    xs, ys = [a[2] for a in s["atoms"]], [a[3] for a in s["atoms"]]
    ratio = ui.cell_height / ui.cell_width
    zoom = min(cols / (max(xs) - min(xs) + 0.8), rows * ratio / (max(ys) - min(ys) + 0.8), graphics.BOND_CELLS)
    zoom = min(max(zoom, ZOOM_RANGE[0]), ZOOM_RANGE[1])
    centre_col, centre_row = col + cols / 2, row + rows / 2
    s["view"] = [(min(xs) + max(xs)) / 2 - (centre_col - ui.left - ui.canvas_cols / 2) / zoom,
                 (min(ys) + max(ys)) / 2 + (centre_row - ui.top - ui.canvas_rows / 2) * ratio / zoom, zoom]


def _keep_visible(s, ui):
    """Pan just enough to keep the chosen atom on the canvas, since new atoms can grow off it."""
    if not s["focus"] or s["focus"][0] != "a":
        return
    atom = _atom(s, s["focus"][1])
    if not atom:
        return
    zoom = s["view"][2]
    half_x = ui.canvas_cols / (2 * zoom) - 1
    half_y = ui.canvas_rows * ui.cell_height / ui.cell_width / (2 * zoom) - 1
    for axis, position, half in ((0, atom[2], half_x), (1, atom[3], half_y)):
        half = max(half, 0.5)
        if position > s["view"][axis] + half:
            s["view"][axis] = position - half
        elif position < s["view"][axis] - half:
            s["view"][axis] = position + half


def _targets_in_order(s):
    return [["a", a[0]] for a in s["atoms"]] + [["b", b[0], b[1]] for b in s["bonds"]]


def _target_centre(s, target):
    points = _target_points(s, target)
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _move_hotspot(s, dx, dy, same_kind):
    """Go from atom to bond to atom, or with same_kind from atom to atom or bond to bond: to the nearest in a direction, by how far along
    it and how far off to the side."""
    if not s["atoms"]:
        return
    current = _target(s)
    s["hover"] = None
    if not current:
        x, y = s["view"][:2]
        s["focus"] = ["a", min(s["atoms"], key=lambda a: math.hypot(a[2] - x, a[3] - y))[0]]
        return
    x, y = _target_centre(s, current)
    best, score = current, float("inf")
    for candidate in _targets_in_order(s):
        if candidate == current or (candidate[0] == current[0]) != same_kind:
            continue
        other_x, other_y = _target_centre(s, candidate)
        along = (other_x - x) * dx + (other_y - y) * dy
        across = abs((other_x - x) * dy - (other_y - y) * dx)
        if along > 0.05 and across <= along * 2.5 and along + across * 1.5 < score:
            best, score = candidate, along + across * 1.5
    s["focus"] = best


def _nudge(s, dx, dy):
    _save(s)
    for atom in s["atoms"]:
        if atom[0] in s["selected"]:
            atom[2] += dx * 0.25
            atom[3] += dy * 0.25


def _set_elements(s, target, symbol):
    """Label the atom at the hotspot or, if it is one of them or there is no hotspot, every selected atom."""
    chosen = bool(s["selected"]) and (not target or target[1] in s["selected"])
    ids = s["selected"] if chosen else [target[1]]
    atoms = [_atom(s, ident) for ident in ids]
    if any(atom[1] != symbol for atom in atoms):
        _save(s)
        for atom in atoms:
            _relabel(atom, symbol)


def _open_context(s, ui, target):
    """The nearest this editor has to ChemDraw's properties dialogs: the menu of what is at the hotspot."""
    col, row = ui.to_cell(s, *_target_centre(s, target))
    kind = {"a": "atom", "b": "bond"}[target[0]]
    s["menu"], s["menu_hover"], s["menu_target"] = ["context", kind, max(int(col), 0), max(int(row), 0)], None, target


def _let_go(s):
    """After a ring or group, keys do nothing until the pointer moves, so that holding one down doesn't scatter copies."""
    s["hover"], s["focus"] = None, None


def _atom_class(s, ident):
    """The kinds of carbon that ChemDraw's sprouting hotkeys tell apart."""
    around = _neighbours(s, ident)
    if len(around) <= 1:
        return "primary"
    if any(_bond(s, ident, other)[2] == DOUBLE for other in around):
        return "aromatic"
    return "secondary" if len(around) == 2 else "tertiary"


def _sprout_ring(s, target, size, sprout):
    _ring_on_target(s, target, size, *_atom(s, target[1])[2:4], sprout=sprout)
    _let_go(s)


def _sprout_group(s, atom, group):
    _attach_group(s, atom, group, 0.0, 0.0)
    _let_go(s)


def _sprout_wedge(s, ident, kind, joined):
    """A wedge or hash. From an aromatic carbon it goes on the end of a new bond, which only makes sense as one undo."""
    if joined:
        ident = _grow(s, ident, "C", SINGLE)
    if ident:
        _grow(s, ident, "C", kind, save=not joined)


def _sprout_dimethyl(s, ident, kind):
    if kind in ("tertiary", "aromatic"):  # no room for two: the table has an isopropyl group here, and doesn't list aromatic carbons
        _sprout_group(s, _atom(s, ident), "*C(C)C")
        return
    # either side of straight on at the end of a chain, so that the three bonds are 120 degrees apart; at a carbon with two bonds the
    # two new ones share the gap between them, 80 degrees from each other and from those
    middle = _straight_on(s, ident) if kind == "primary" else _direction(s, ident)
    spread = math.radians(40 if kind == "secondary" else 60)
    if _grow(s, ident, "C", SINGLE, angle=middle - spread):
        _grow(s, ident, "C", SINGLE, angle=middle + spread, save=False)
    _let_go(s)


def _sprout_alkyne(s, ident):
    near = _grow(s, ident, "C", SINGLE)
    if near:
        _grow(s, near, "C", TRIPLE, save=False)


def _sprout(s, target, name):
    """What the digits and z do on an atom, which depends on the kind of carbon it is."""
    ident = target[1]
    atom, kind = _atom(s, ident), _atom_class(s, ident)
    branched = kind in ("tertiary", "aromatic")
    if name == "1":
        _grow(s, ident, "C", SINGLE)
    elif name == "0":
        _grow(s, ident, "C", SINGLE, cyclic=True)
    elif name == "2":
        if kind == "secondary":
            _grow(s, ident, "O", DOUBLE)
        else:
            _sprout_group(s, atom, "*C(C)=O")
    elif name in ("3", "a"):
        _sprout_ring(s, target, "benzene", kind != "primary")
    elif name in ("4", "5"):
        _sprout_wedge(s, ident, WEDGE if name == "4" else HASH, kind == "aromatic")
    elif name == "6":
        _sprout_ring(s, target, 6, branched)
    elif name == "7":
        _sprout_ring(s, target, 5, branched)
    elif name == "8":
        _grow(s, ident, "C", DOUBLE)
    elif name == "9":
        _sprout_dimethyl(s, ident, kind)
    else:
        _sprout_alkyne(s, ident)
    if s["hover"]:  # the hotspot goes on to the new atom, so that pressing 1 again carries on from there
        s["hover"] = None


# ChemDraw 17.1's tables. Each key means what it means for what is at the hotspot, and only keys for things this editor lacks say so.
_ATOM_ELEMENTS = {"f": "F", "p": "P", "h": "H", "b": "Br", "B": "B", "i": "I", "k": "K", "s": "S", "S": "Si", "n": "N", "w": "N", "N": "Na",
                  "c": "C", "l": "Cl", "C": "Cl", "o": "O", "q": "O"}
_ATOM_GROUPS = {"P": "ph", "A": "ac", "m": "me", "e": "et", "E": "co2me", "F": "cf3"}
_ATOM_SPROUTS = frozenset("0123456789az")
_ATOM_MISSING = {"'": "the atom number"}
_BOND_KINDS = {"1": SINGLE, "2": DOUBLE, "3": TRIPLE, "w": WEDGE, "h": HASH, "y": WAVY}
_BOND_RINGS = {"a": "benzene", "z": "cyclopentadiene", "v": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8}
_BOND_MISSING = {"d": "the dashed bond", "b": "the bold bond", "B": "the bold double bond", "H": "the plain hashed bond",
                 "l": "the double bond's position", "c": "the double bond's position", "r": "the double bond's position",
                 "f": "bringing a bond to the front", "9": "the chair cyclohexane", "0": "the chair cyclohexane"}
_GENERIC_TOOLS = {" ": ("tool", "select"), "x": ("bond", SINGLE), "t": ("tool", "text"), "j": ("ring", "benzene"), "J": ("ring", "cyclopentadiene"),
                  "u": ("ring", 6), "U": ("ring", 5), "X": ("tool", "chain")}
_GENERIC_MISSING = {"T": "the bracket tool", "e": "the arrow tool", "E": "the chemical symbols tool", "g": "the chromatography tool",
                    "G": "the orbital tool", "l": "the pen tool", "L": "the drawing elements tool", "k": "the fragmentation tool", "K": "the query tool"}
_COMMAND_KEYS = {"undo": "undo", "redo": "redo", "tidy": "tidy", "select_all": "select_all", "[": "zoom_out", "]": "zoom_in",
                 "f5": "actual", "f6": "fit", "f7": "zoom_in", "f8": "zoom_out"}
_ARROW_STEPS = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}
_DELETE_KEYS = ("backspace", "delete")


def _missing(s, what):
    s["message"] = f"ChemDraw has {what} on this key, but this editor doesn't."


def _remove_label(s, atom):
    """Back to plain carbon. Returns False if it already was."""
    extras = _extras(atom)
    if atom[1] == "C" and not atom[4] and not ({"iso", "rad"} & set(extras)):
        return False
    _save(s)
    _relabel(atom, "C", 0)
    for key in ("iso", "rad"):
        _set_extra(atom, key, None)
    return True


def _atom_key(s, ui, target, name):
    atom = _atom(s, target[1])
    if name in _DELETE_KEYS and target[1] in s["selected"]:
        _delete_selection(s)
    elif name in _DELETE_KEYS + (" ",):
        # a plain carbon has no label to remove, so here, beyond ChemDraw's table, the delete keys remove the atom itself
        if not _remove_label(s, atom) and name != " ":
            _delete_target(s, target)
    elif name in _ATOM_ELEMENTS:
        _set_elements(s, target, _ATOM_ELEMENTS[name])
    elif name in _ATOM_GROUPS:
        _sprout_group(s, atom, GROUPS[_ATOM_GROUPS[name]])
    elif name in _ATOM_SPROUTS:
        _sprout(s, target, name)
    elif name in ("+", "-"):
        _charge(s, target, 1 if name == "+" else -1)
    elif name == "r":
        _next_rgroup_label(s, target)
    elif name == "x":
        _set_elements(s, target, "X")
    elif name == "d":
        _save(s)
        _relabel(atom, "H")
        _set_extra(atom, "iso", 2)
    elif name == ".":
        _cycle_extra(s, target, "ap", [None, 1, 2, 3], _POINTS)
    elif name in ("enter", "="):
        _open_label(s, target, 0.0, 0.0)
    elif name in ("/", "?"):
        _open_context(s, ui, target)
    elif name in _ATOM_MISSING:
        _missing(s, _ATOM_MISSING[name])


def _bond_key(s, ui, target, name):
    if name in _DELETE_KEYS and all(ident in s["selected"] for ident in target[1:]):
        _delete_selection(s)
    elif name in _DELETE_KEYS:
        _delete_target(s, target)
    elif name in _BOND_KINDS:
        _set_bond_kind(s, target, _BOND_KINDS[name])
    elif name in _BOND_RINGS:
        pointer = s["pointer"] or _target_centre(s, target)
        _ring_on_target(s, target, _BOND_RINGS[name], *pointer)
        _let_go(s)
    elif name in ("/", "?"):
        _open_context(s, ui, target)
    elif name == "enter":
        s["message"] = "A label goes on an atom: point at one."
    elif name in _BOND_MISSING:
        _missing(s, _BOND_MISSING[name])


def _generic_key(s, ui, name):
    """With no hotspot, keys choose tools. Above all they don't draw, since there is nothing to say where."""
    if name in _DELETE_KEYS:
        _delete(s, None)
    elif name in _GENERIC_TOOLS:
        _choose(s, _GENERIC_TOOLS[name])
    elif s["selected"] and name in _ATOM_ELEMENTS:
        _set_elements(s, None, _ATOM_ELEMENTS[name])
    elif name == "enter":
        _open_label(s, None, *(s["pointer"] or s["view"][:2]))
    elif name == "?":
        s["help"] = True
    elif name == "/":
        s["entry"] = ""
    elif name == "v":
        _fit(s, ui)
    elif name in _GENERIC_MISSING:
        _missing(s, _GENERIC_MISSING[name])
    elif len(name) == 1 and (name in _ATOM_ELEMENTS or name in _ATOM_GROUPS or name in _ATOM_SPROUTS or name in _BOND_RINGS or name in "+-="):
        s["message"] = f"{name} acts on the atom or bond under the pointer: point at one. u, then a click, gives a cyclohexane."


def _entry_key(s, name):
    if name == "esc":
        s["entry"] = None
    elif name == "enter":
        text = s["entry"].strip()
        if not text or load_smiles(s, text):
            s["entry"] = None
    elif name == "backspace":
        s["entry"] = s["entry"][:-1]
    elif len(name) == 1 and len(s["entry"]) < MAX_ENTRY:
        s["entry"] += name


def _label_key(s, name):
    label = s["label"]
    if name == "esc":
        s["label"] = None
    elif name == "enter":
        if not label["text"].strip():
            s["label"] = None
        elif _apply_label(s, label["target"], label["text"], *label["at"]):
            s["label"] = None
    elif name == "backspace":
        label["text"] = label["text"][:-1]
    elif len(name) == 1 and len(label["text"]) < MAX_LABEL:
        label["text"] += name


def _menu_key(s, ui, name):
    """Handle a key while a menu is open; returns what ends the editor, if it does."""
    items = _menu_items(s, ui)
    choices = [i for i, item in enumerate(items) if item]
    if name in ("up", "down"):
        position = choices.index(s["menu_hover"]) if s["menu_hover"] in choices else (-1 if name == "down" else 0)
        s["menu_hover"] = choices[(position + (1 if name == "down" else -1)) % len(choices)]
    elif name in ("left", "right") and s["menu"][0] == "bar":
        s["menu"] = ["bar", (s["menu"][1] + (1 if name == "right" else -1)) % len(MENUS)]
        s["menu_hover"] = None
    elif name == "enter" and s["menu_hover"] in choices:
        action, target = items[s["menu_hover"]][2], s["menu_target"]
        s["menu"], s["menu_hover"], s["menu_target"] = None, None, None
        return _act(s, ui, action, target)
    elif name not in ("enter",):
        s["menu"], s["menu_hover"], s["menu_target"] = None, None, None
    return False


def _key(s, ui, name):
    """Handle one key; returns what ends the editor, if it does."""
    if name == "quit":
        return True
    if name == "interrupt":
        return CANCEL if ui.hosted else INTERRUPT
    if s["entry"] is not None:
        _entry_key(s, name)
        return False
    if s["prompt"] is not None:
        _prompt_key(s, name)
        return False
    if s["label"] is not None:
        _label_key(s, name)
        return False
    if s["menu"] is not None:
        return _menu_key(s, ui, name)
    if s["help"]:
        s["help"] = False
        return False
    if name in _COMMAND_KEYS:
        return _command(s, ui, _COMMAND_KEYS[name])
    if name in _ARROW_STEPS:
        if s["selected"]:
            _nudge(s, *_ARROW_STEPS[name])
        else:
            _move_hotspot(s, *_ARROW_STEPS[name], same_kind=False)
    elif name.startswith("shift-") and name[6:] in _ARROW_STEPS:
        _move_hotspot(s, *_ARROW_STEPS[name[6:]], same_kind=True)
    elif name in ("tab", "shift-tab"):
        order = _targets_in_order(s)
        if order:
            step = 1 if name == "tab" else -1
            position = order.index(s["focus"]) if s["focus"] in order else (-1 if step == 1 else 0)
            s["focus"], s["hover"] = order[(position + step) % len(order)], None
    elif name == "esc":
        if s["press"] or s["selected"] or s["focus"]:
            s["focus"], s["press"], s["drag"], s["selected"] = None, None, None, []
        else:
            s["tool"] = "select"
    elif name == "f2" and ui.inline:
        _toggle_tools(s)
    elif name == "f10" and not ui.compact:
        s["menu"], s["menu_hover"], s["menu_target"] = ["bar", 0], 0, None
    elif name == "redraw":
        s["shown"], s["size"] = {}, None
    else:
        target = _target(s)
        if target and target[0] == "a":
            _atom_key(s, ui, target, name)
        elif target:
            _bond_key(s, ui, target, name)
        else:
            _generic_key(s, ui, name)
    if not s["hover"]:
        _keep_visible(s, ui)
    return False


def _toggle_tools(s):
    """Put an inline editor's tools away, or show them again; its host is told, to remember it for the next edit."""
    s["strip"] = not s["strip"]
    s["notices"] = s.get("notices", "") + HOST_TOOLS.format(int(s["strip"]))


def _choose(s, action):
    """A palette button was pressed."""
    kind, value = action
    if kind == "bond":
        s["tool"], s["bond"] = "bond", value
    elif kind == "ring":
        s["tool"], s["ring"] = "ring", value
    else:
        s["tool"] = value
    s["selected"] = [] if s["tool"] != "select" else s["selected"]


def _mouse_in_menu(s, ui, col, row, motion, release, wheel):
    """A mouse report while a menu is open; returns what ends the editor, if it does."""
    if wheel or release:
        return False
    title = ui.title_at(col, row)
    if motion:
        if s["menu"][0] == "bar" and title is not None:
            s["menu"] = ["bar", title]
        s["menu_hover"] = _menu_item_at(s, ui, col, row)
        return False
    item = _menu_item_at(s, ui, col, row)
    action = _menu_items(s, ui)[item][2] if item is not None else None
    same = s["menu"] == ["bar", title]
    target = s["menu_target"]
    s["menu"], s["menu_hover"], s["menu_target"] = None, None, None
    if action:
        return _act(s, ui, action, target)
    if title is not None and not same:
        s["menu"] = ["bar", title]
    return False


def _drag_motion(s, ui, press, col, row, x, y):
    s["drag"] = [x, y]
    start_x, start_y = press["model"]
    if not press["moved"] and math.hypot(x - start_x, y - start_y) > DRAG_START:
        press["moved"] = True
        if press["mode"] == "move":
            _save(s)
    if press["moved"] and press["mode"] == "move":
        dx, dy = x - press["last"][0], y - press["last"][1]
        for atom in s["atoms"]:
            if atom[0] in press["ids"]:
                atom[2] += dx
                atom[3] += dy
        press["last"] = [x, y]
    elif press["moved"] and press["mode"] == "pan":
        view_x, view_y, zoom = press["view"]
        s["view"][0] = view_x - (col - press["cell"][0]) / zoom
        s["view"][1] = view_y + (row - press["cell"][1]) * ui.cell_height / ui.cell_width / zoom
    s["hover"] = None


def _start_press(s, target, col, row, x, y, button):
    mode, ids = "draw", []
    if button == 1 or s["tool"] == "pan":
        mode = "pan"
    elif s["tool"] == "select":
        if _in_selection(s, x, y) and (not target or all(i in s["selected"] for i in target[1:])):
            mode, ids = "move", list(s["selected"])
        elif target:
            mode, ids = "move", list(target[1:])
        else:
            mode = "marquee"
    elif s["tool"] not in ("bond", "chain"):
        mode = "none"
    s["press"] = {"cell": [col, row], "view": list(s["view"]), "model": [x, y], "last": [x, y], "target": target, "moved": False,
                  "mode": mode, "ids": ids}
    s["drag"] = None


def _mouse(s, ui, code, x_cell, y_cell, release, pixels):
    """Handle one mouse report; returns what ends the editor, if it does."""
    col, row = ui.from_report(x_cell, y_cell, pixels)
    button, motion, wheel = code & 3, bool(code & 32), bool(code & 64)
    if s["menu"]:
        return _mouse_in_menu(s, ui, col, row, motion, release, wheel)
    x, y = ui.to_model(s, col, row)
    inside = ui.in_canvas(col, row) and not s["help"]
    if wheel:
        if inside:
            _zoom(s, 1.15 if button == 0 else 1 / 1.15)
            after_x, after_y = ui.to_model(s, col, row)  # the point under the pointer stays there
            s["view"][0] += x - after_x
            s["view"][1] += y - after_y
        return False
    if motion:
        s["pointer"] = [x, y] if inside else None
        s["tip"] = ui.palette_at(col, row)
        s["button"] = ui.button_at(col, row)
        if s["press"]:
            _drag_motion(s, ui, s["press"], col, row, x, y)
        else:
            s["hover"] = _target_at(s, ui, x, y) if inside else None
            s["focus"] = None  # the mouse has the hotspot now, and a key over nothing must not edit what it left behind
        return False
    if release:
        press, s["press"] = s["press"], None
        if press and (not press["moved"] or press["mode"] == "none"):
            if button == 0:  # a ring or a charge still lands if the hand wobbles
                _click(s, ui, press["target"], *press["model"], code)
        elif press and press["mode"] == "draw":
            s["drag"] = s["drag"] or [x, y]
            _finish_drag(s, ui, press)
        elif press and press["mode"] == "marquee" and s["drag"]:
            _finish_marquee(s, press)
        s["drag"] = None
        s["hover"] = _target_at(s, ui, x, y) if inside else None
        return False
    # a press
    if s["help"]:
        s["help"] = False
        return False
    if s["label"]:
        _label_key(s, "enter")
        s["label"] = None
        return False
    if s["entry"] is not None or s["prompt"] is not None:
        s["entry"], s["prompt"] = None, None
        return False
    pressed = ui.button_at(col, row) if button == 0 else None
    if pressed == "copy":
        _copy(s)
        return False
    if pressed == "tools":
        _toggle_tools(s)
        return False
    if pressed in dict(WINDOW_BUTTONS):  # the host's, which takes them before they come here
        return False
    if pressed:  # a stray click outside the box does nothing, but these end it
        return True if pressed == "done" else CANCEL
    title = ui.title_at(col, row)
    if title is not None and button == 0:
        s["menu"], s["menu_hover"], s["menu_target"] = ["bar", title], None, None
        return False
    index = ui.palette_at(col, row)
    if index is not None and button == 0:
        _choose(s, PALETTE[index][1])
        return False
    if not inside:
        return False
    target = _target_at(s, ui, x, y)
    if button == 2:
        kind = {"a": "atom", "b": "bond"}[target[0]] if target else "empty"
        on_selection = all(i in s["selected"] for i in target[1:]) if target else _in_selection(s, x, y)
        if s["selected"] and on_selection:
            kind, target = "selection", None
        s["menu"], s["menu_hover"], s["menu_target"] = ["context", kind, int(col), int(row)], None, target
    else:
        _start_press(s, target, col, row, x, y, button)
    return False


def handle(s, ui, raw, pixels):
    """Apply a run of raw input to the state; returns what ends the editor, if it does."""
    events, s["partial"] = parse_events(s["partial"] + raw)
    if len(s["partial"]) > 64:
        s["partial"] = ""
    return handle_events(s, ui, events, pixels)


def handle_events(s, ui, events, pixels):
    """Apply parsed input, for a host that parses it first. Stops at what ends the editor, and returns it, for `finish`."""
    for event in events:
        _settle(s)
        if event[0] == "key":
            s["message"] = ""
            ended = _key(s, ui, event[1])
            if ended:
                return ended
        elif event[0] == "clipboard":
            s["message"] = copy_answer(event[1])
        else:
            if not (event[1] & 32) and not event[4]:  # moving the pointer or letting go doesn't dismiss a message
                s["message"] = ""
            ended = _mouse(s, ui, event[1], event[2], event[3], event[4], pixels)
            if ended:
                return ended
    _settle(s)
    return False


def copy_answer(status):
    """What to say when the terminal answers a copy through Kitty's clipboard protocol."""
    if status == "DONE":
        return "The terminal took the copy, pictures and all."
    return f"The terminal refused the pictures ({status}), so only the text was copied."


def _settle(s):
    """Let go of anything that points at what is gone: undo or delete can come in the middle of a drag."""
    _forget_missing(s)
    press = s["press"]
    if press and press["target"] and not _target_points(s, press["target"]):
        s["press"], s["drag"] = None, None


def finish(s, ended):
    """What the editor's end comes to: (status, text), or None to stay open, because Done can't save a drawing RDKit can't read."""
    if ended == CANCEL:
        return CANCELLED, ""
    answer, problem, _ = analyse(s)
    if answer is not None:
        return QUIT, annotated(s)
    if ended == INTERRUPT:
        return FAILED, f"The drawing isn't valid chemistry, so there is no SMILES: {problem}"
    s["message"] = f"Not saved, because the drawing isn't valid: {problem}"
    return None


def step(state_text, smiles, raw, cols, rows, cell_width=None, cell_height=None, pixels=False, theme=graphics.DEFAULT_THEME, box=None, title="",
         compact=False, inline=None, at=None, strip=True):
    """Run one request: return (status, new state as text, what to print). With a `box`, see Layout, the editor stays inside it, and a
    `compact` one has a strip of tools and no menu bar. An `inline` one draws in that place on the screen, with its tools floating
    beside it (`strip` false puts them away to begin with), and `at`, the place of the picture it edits, which the drawing then
    matches as closely as it can."""
    for place in (box, inline, at):
        if place and (place[0] + place[2] > rows or place[1] + place[3] > cols):
            return FAILED, "", "The box doesn't fit in the window."
    if state_text:
        s = decode(state_text)
        first = False
    else:
        s = new_state()
        s["strip"] = bool(strip)
        first = True
    pixels = pixels and bool(cell_width) and bool(cell_height)

    def layout():
        return Layout(cols, rows, cell_width, cell_height, pixels, box, title, compact=compact, inline=inline, strip=s["strip"])

    ui = layout()
    if ui.inline:
        _keep_corner(s, ui)
    if box and not fits(ui):
        return FAILED, "", "The box is too small for the editor."
    if ui.canvas_rows < 4 or ui.canvas_cols < 20:
        return CONTINUE, encode(s), "\x1b[2J\x1b[H" + "This window is too small for the editor."
    if first and smiles:
        if not load_smiles(s, smiles):
            return FAILED, "", s["message"]
        if at and ui.inline:
            _fit_to(s, ui, at)
        else:
            _fit(s, ui)
    ended = handle(s, ui, raw, pixels)
    result = finish(s, ended) if ended else None
    if result:
        return result[0], "", result[1]
    if ui.inline and ui.strip != s["strip"]:  # the tools were put away or shown again
        ui = layout()
    notices = s.pop("notices", "")
    copied = take_clipboard(s)
    output = notices + copied + frame(s, ui, theme)
    return CONTINUE, encode(s), output


def fits(ui):
    """Is a box big enough to use?"""
    if ui.inline:
        return (ui.canvas_rows >= 4 and ui.canvas_cols >= 20 and ui.strip_width >= max(STRIP_COLS, ui.per_row * ui.button_cols)
                and ui.palette_left + ui.strip_width <= ui.cols and ui.palette_top >= 0
                and max(ui.strip_rows()) < ui.rows)
    return ui.canvas_rows >= 4 and ui.canvas_cols >= 20 and ui.cols >= max(MIN_BOX_COLS, ui.per_row * ui.button_cols if ui.compact else 0)
