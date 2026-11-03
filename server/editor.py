"""A molecular editor for terminals with Kitty graphics, used with the mouse and the keyboard.

The client only passes on raw input and prints what comes back, so everything happens here. The server keeps no sessions: the
drawing travels with each request as an opaque string, and is checked on the way back in because the client could send anything.

The molecule is kept as a plain list of atoms and bonds rather than an RDKit object, because a drawing in progress is often not
valid chemistry. RDKit reads it as a molblock, which also works out charges, wedges and double bond geometry, and draws each frame.
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

import editor_icons
import graphics
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
_ORDER = {SINGLE: 1, DOUBLE: 2, TRIPLE: 3, WEDGE: 1, HASH: 1}
TOOLS = ("select", "erase", "pan", "text", "bond", "ring", "plus", "minus")
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
LAYERS = ("palette", "structure", "overlay", "text", "backing")
MIN_BOX_COLS = 36  # room for the buttons on the bottom edge

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

On an atom   c n o s p f i h k  elements   b Br   l C Cl   w N   q O   S Si   N Na   B boron
             m Me   e Et   A Ac   P Ph   E CO2Me   F CF3   + -  charge   = Enter  label box
  sprout     1 bond, zigzag   0 bond, curling round   2 acetyl or C=O   3 a phenyl
             4 wedge   5 hash   6 cyclohexane   7 cyclopentane   8 =CH2   9 dimethyl   z alkyne
  remove     Space Delete Backspace  the label, leaving carbon. Delete on carbon removes the atom.
On a bond    1 2 3  order   w wedge   h hash   Delete  remove
  fuse       a benzene   z cyclopentadiene   v cyclopropane   4 5 6 7 8  ring of that size
On nothing   x bond tool   u cyclohexane   U cyclopentane   j benzene   J cyclopentadiene
             t text   Space selection   ? this help   / load a SMILES   v fit

Ring tool    click a bond or atom to fuse, Alt+click a ring atom to sprout, or click empty space
Bond tool    click to draw or to step a bond's order, drag to aim or to join
Text tool    click an atom and type: N, Cl, OMe, CF3, CO2H, NO2, Ph, Boc, Ts...
Selection    drag to move, drag across empty space to select, Shift+click to add; a letter labels them all
Right-click, or / or ? on an atom or bond, for its menu. The wheel zooms. The middle button pans.

Anywhere     Ctrl-Z undo   Ctrl-Y redo   Ctrl-A select all   Ctrl-K clean up   Tab next   Esc back out
             F5 actual size   F6 fit   F7 F8 ] [ zoom   F10 menus   Ctrl-Q quit and print the SMILES"""


class StateError(ValueError):
    """The client sent a drawing that can't have come from here."""


# The drawing as text


def new_state():
    return {"atoms": [], "bonds": [], "next": 1, "tool": "bond", "bond": SINGLE, "ring": "benzene",
            "view": [0.0, 0.0, DEFAULT_ZOOM], "focus": None, "hover": None, "last": None, "selected": [], "pointer": None,
            "press": None, "drag": None, "undo": [], "redo": [], "entry": None, "label": None, "help": False, "message": "",
            "menu": None, "menu_target": None, "menu_hover": None, "tip": None, "button": None, "cover": False, "shown": {}, "flips": {}, "size": None,
            "partial": ""}


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


def _check_structure(atoms, bonds, next_id):
    if not isinstance(atoms, list) or not isinstance(bonds, list) or len(atoms) > MAX_ATOMS or len(bonds) > MAX_BONDS:
        raise StateError("The drawing is too large.")
    table = Chem.GetPeriodicTable()
    ids = set()
    for ident, element, x, y, charge in atoms:
        _integer(ident)
        _number(x)
        _number(y)
        _integer(charge, 8)
        if not isinstance(element, str) or len(element) > 2 or table.GetAtomicNumber(element) < 1 or ident in ids:
            raise StateError("An atom in the drawing is not valid.")
        ids.add(ident)
    pairs = set()
    for begin, end, kind in bonds:
        pair = frozenset((begin, end))
        if begin not in ids or end not in ids or begin == end or kind not in _ORDER or pair in pairs or isinstance(kind, bool):
            raise StateError("A bond in the drawing is not valid.")
        pairs.add(pair)
    _integer(next_id)
    if ids and next_id <= max(ids):
        raise StateError("An atom in the drawing is not valid.")


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


def check_state(s):
    _check_structure(s["atoms"], s["bonds"], s["next"])
    if s["tool"] not in TOOLS or s["bond"] not in _ORDER or isinstance(s["bond"], bool):
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
        for atoms, bonds, next_id in stack:
            _check_structure(atoms, bonds, next_id)
    if s["press"] is not None:
        _check_press(s["press"])
    for name in ("drag", "pointer"):
        if s[name] is not None:
            _check_point(s[name])
    if s["entry"] is not None and (not isinstance(s["entry"], str) or len(s["entry"]) > MAX_ENTRY):
        raise StateError("The text in the drawing is too long.")
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
    if s["button"] not in (None, "done", "cancel", "copy"):
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


def _save(s):
    """Remember the structure for undo. Call this before changing it."""
    s["undo"] = (s["undo"] + [[json.loads(json.dumps(s["atoms"])), json.loads(json.dumps(s["bonds"])), s["next"]]])[-MAX_UNDO:]
    s["redo"] = []


def _restore(s, source, target):
    if not s[source]:
        s["message"] = f"Nothing to {source}."
        return
    s[target] = (s[target] + [[s["atoms"], s["bonds"], s["next"]]])[-MAX_UNDO:]
    s["atoms"], s["bonds"], s["next"] = s[source].pop()
    _forget_missing(s)


def _forget_missing(s):
    for name in ("focus", "hover", "last", "menu_target"):
        target = s[name]
        if target and ((target[0] == "a" and not _atom(s, target[1])) or (target[0] == "b" and not _bond(s, target[1], target[2]))):
            s[name] = None
    s["selected"] = [ident for ident in s["selected"] if _atom(s, ident)]


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


def to_molblock(atoms, bonds):
    index = {a[0]: i + 1 for i, a in enumerate(atoms)}
    rows = ["", "  molterm", "", f"{len(atoms):3d}{len(bonds):3d}  0  0  0  0  0  0  0  0999 V2000"]
    for _, element, x, y, _charge in atoms:
        rows.append(f"{x * 1.5:10.4f}{y * 1.5:10.4f}{0:10.4f} {element:<3} 0  0")
    stereo = {WEDGE: 1, HASH: 6}
    for begin, end, kind in bonds:
        rows.append(f"{index[begin]:3d}{index[end]:3d}{_ORDER[kind]:3d}{stereo.get(kind, 0):3d}")
    charged = [(index[a[0]], a[4]) for a in atoms if a[4]]
    for start in range(0, len(charged), 8):
        group = charged[start:start + 8]
        rows.append(f"M  CHG{len(group):3d}" + "".join(f"{i:4d}{c:4d}" for i, c in group))
    return "\n".join(rows + ["M  END", ""])


def _raw_mol(s):
    """The drawing as an RDKit molecule, whether or not it is valid chemistry."""
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]), sanitize=False, removeHs=False)
    mol.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(mol)
    return mol


def analyse(s):
    """Return (SMILES or None, what's wrong or None, indices of atoms that are wrong)."""
    if not s["atoms"]:
        return "", None, []
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]))
        if mol is not None:
            return Chem.MolToSmiles(mol), None, []
        problems = Chem.DetectChemistryProblems(Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]), sanitize=False, removeHs=False))
    atoms, text = [], "RDKit can't read this structure."
    for problem in problems:
        text = problem.Message()
        if hasattr(problem, "GetAtomIdx"):
            atoms.append(problem.GetAtomIdx())
        elif hasattr(problem, "GetAtomIndices"):
            atoms.extend(problem.GetAtomIndices())
    return None, text, atoms


def facts(smiles):
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return ""
    return f"{rdMolDescriptors.CalcMolFormula(mol)}   MW {Descriptors.MolWt(mol):.1f}   cLogP {Crippen.MolLogP(mol):.1f}"


def _from_mol(s, mol):
    """Replace the drawing with a molecule that already has 2D coordinates, scaled so that its bonds are about 1 long."""
    plain = Chem.Mol(mol)
    conformer = plain.GetConformer()
    Chem.WedgeMolBonds(plain, conformer)
    wedges = {b.GetIdx(): b.GetBondDir() for b in plain.GetBonds()}
    Chem.Kekulize(plain, clearAromaticFlags=True)
    lengths = [(conformer.GetAtomPosition(b.GetBeginAtomIdx()) - conformer.GetAtomPosition(b.GetEndAtomIdx())).Length()
               for b in plain.GetBonds()]
    scale = (sum(lengths) / len(lengths)) if lengths else 1.0
    atoms = []
    for atom in plain.GetAtoms():
        point = conformer.GetAtomPosition(atom.GetIdx())
        atoms.append([atom.GetIdx() + 1, atom.GetSymbol(), round(point.x / scale, 4), round(point.y / scale, 4), atom.GetFormalCharge()])
    orders = {Chem.BondType.DOUBLE: DOUBLE, Chem.BondType.TRIPLE: TRIPLE}
    directions = {Chem.BondDir.BEGINWEDGE: WEDGE, Chem.BondDir.BEGINDASH: HASH}
    bonds = []
    for bond in plain.GetBonds():
        kind = orders.get(bond.GetBondType()) or directions.get(wedges[bond.GetIdx()], SINGLE)
        bonds.append([bond.GetBeginAtomIdx() + 1, bond.GetEndAtomIdx() + 1, kind])
    s["atoms"], s["bonds"], s["next"] = atoms, bonds, len(atoms) + 1
    s["selected"] = []
    _forget_missing(s)
    s["last"] = None


def load_smiles(s, text):
    """Replace the drawing with a SMILES. Returns False and sets a message if it can't."""
    try:
        with rdBase.BlockLogs():
            parsed = molecule.parse(text, "smiles")
    except molecule.ParseError as error:
        s["message"] = error.message
        return False
    mol = parsed.mol
    if mol.GetNumAtoms() > MAX_ATOMS or mol.GetNumBonds() > MAX_BONDS:
        s["message"] = f"The editor holds at most {MAX_ATOMS} atoms."
        return False
    if s["atoms"]:
        _save(s)
    before = Chem.MolToSmiles(mol)
    _from_mol(s, mol)
    s["focus"] = None
    if analyse(s)[0] != before:
        s["message"] = "Some details, such as isotopes or unusual bonds, aren't kept by this editor."
    return True


# Where new atoms go


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
    # the group first: in a drawing Ac, Pr and Ts are acetyl, propyl and tosyl, not actinium, praseodymium and tennessine
    group = GROUPS.get(text.lower())
    symbol = None if group else element(text)
    if not symbol and not group and text.islower():
        symbol = element(text.capitalize())
    if symbol:
        _save(s)
        if atom:
            atom[1] = symbol
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
            atom[1], atom[4] = fragment_atom.GetSymbol(), fragment_atom.GetFormalCharge()
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

# (label, key, action), with None for a dividing line
MENUS = [("File", [("Load SMILES...", "/", ("command", "entry")), ("Copy SMILES", "", ("command", "copy")), ("Quit and print SMILES", "Ctrl-Q", ("command", "quit"))]),
         ("Edit", [("Undo", "Ctrl-Z", ("command", "undo")), ("Redo", "Ctrl-Y", ("command", "redo")), None,
                   ("Select All", "Ctrl-A", ("command", "select_all")), ("Delete", "Del", ("command", "delete"))]),
         ("View", [("Actual Size", "F5", ("command", "actual")), ("Fit to Window", "F6", ("command", "fit")), ("Magnify", "F7", ("command", "zoom_in")), ("Reduce", "F8", ("command", "zoom_out"))]),
         ("Structure", [("Clean Up", "Ctrl-K", ("command", "tidy"))]),
         ("Help", [("Keys and Mouse", "?", ("command", "help"))])]
CONTEXT_MENUS = {
    "atom": [(symbol, key, ("element", symbol)) for symbol, key in (("C", "c"), ("N", "n"), ("O", "o"), ("S", "s"), ("P", "p"), ("F", "f"), ("Cl", "l"), ("Br", "b"))]
    + [None, ("Label...", "Enter", ("label", None)), ("Charge +", "+", ("charge", 1)), ("Charge -", "-", ("charge", -1)), ("Delete", "Del", ("delete", None))],
    "bond": [("Single", "1", ("kind", SINGLE)), ("Double", "2", ("kind", DOUBLE)), ("Triple", "3", ("kind", TRIPLE)), ("Wedged", "w", ("kind", WEDGE)),
             ("Hashed", "h", ("kind", HASH)), None, ("Delete", "Del", ("delete", None))],
    "empty": [("Undo", "Ctrl-Z", ("command", "undo")), ("Redo", "Ctrl-Y", ("command", "redo")), None, ("Clean Up", "Ctrl-K", ("command", "tidy")),
              ("Fit to Window", "F6", ("command", "fit")), ("Select All", "Ctrl-A", ("command", "select_all"))],
}


class Layout:
    """Where everything is. Columns and rows here count from 0 at the top left of the editor, which is the screen's unless the editor
    is in a box: then `box` is (row, col, rows, cols) on the screen, counting its border, and `title` goes in the border."""

    def __init__(self, cols, rows, cell_width=None, cell_height=None, pixels=False, box=None, title=""):
        self.box, self.title = box, title
        self.boxed = box is not None
        self.origin = (box[0] + 1, box[1] + 1) if box else (0, 0)
        if box:
            rows, cols = box[2] - 2, box[3] - 2
        self.cols, self.rows = cols, rows
        self.cell_width, self.cell_height = cell_width or DEFAULT_CELL[0], cell_height or DEFAULT_CELL[1]
        self.pixels = pixels
        self.top = MENU_ROWS
        self.canvas_rows = rows - MENU_ROWS - STATUS_ROWS
        # square buttons two rows high, in as few columns as the height of the window allows
        self.button_cols = min(max(round(2 * self.cell_height / self.cell_width), 3), 6)
        self.per_row = 2
        while math.ceil(len(PALETTE) / self.per_row) * 2 > self.canvas_rows and self.per_row < len(PALETTE):
            self.per_row += 1
        self.palette_rows = math.ceil(len(PALETTE) / self.per_row) * 2
        self.left = self.per_row * self.button_cols + 1
        self.canvas_cols = cols - self.left
        self.titles = []
        col = 1
        for index, (title, _) in enumerate(MENUS):
            self.titles.append((col, len(title) + 2, index))
            col += len(title) + 2

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
        """On the bottom edge of the box: (name, label, column)."""
        col, found = self.cols, []
        for name, label in (("done", " Done Ctrl-Q "), ("cancel", " Cancel Ctrl-C "), ("copy", " Copy SMILES ")):
            col -= len(label) + 1
            found.append((name, label, col))
        return found

    def button_at(self, col, row):
        if not self.boxed or math.floor(row) != self.rows:
            return None
        return next((name for name, label, start in self.buttons() if start <= col < start + len(label)), None)

    def title_at(self, col, row):
        if not 0 <= row < MENU_ROWS:
            return None
        return next((index for start, width, index in self.titles if start <= col < start + width), None)

    def palette_at(self, col, row):
        if not (self.top <= row < self.top + self.palette_rows and 0 <= col < self.per_row * self.button_cols):
            return None
        index = (int(row) - self.top) // 2 * self.per_row + int(col) // self.button_cols
        return index if index < len(PALETTE) else None

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


def _target_points(s, target):
    """The atom's position, or both ends of the bond, for marking a target on the canvas."""
    if target[0] == "a":
        atom = _atom(s, target[1])
        return [(atom[2], atom[3])] if atom else []
    bond = _bond(s, target[1], target[2])
    if not bond:
        return []
    return [(_atom(s, bond[0])[2], _atom(s, bond[0])[3]), (_atom(s, bond[1])[2], _atom(s, bond[1])[3])]


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
    for begin, end, _ in s["bonds"]:
        if begin in chosen and end in chosen:
            marks.append(["line", _atom(s, begin)[2:4], _atom(s, end)[2:4], ACCENT, 0.22, 0.14])
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
        if press["mode"] == "draw":
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
    mol = _raw_mol(s)
    with rdBase.BlockLogs():
        Chem.ReapplyMolBlockWedging(mol)
        drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


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
_BACKING = {"dark": (32, 32, 30, 255), "light": (250, 249, 245, 255), "mono": (255, 255, 255, 255)}
_CHROME = {"dark": {"bar": "48;2;42;42;40", "ink": "38;2;240;239;236", "dim": "38;2;143;142;134", "pick": "48;2;57;135;229", "on_pick": "38;2;255;255;255",
                    "err": "38;2;239;83;80", "warn": "38;2;240;165;0", "pick_ink": "38;2;57;135;229"},
           "light": {"bar": "48;2;238;237;232", "ink": "38;2;29;29;27", "dim": "38;2;92;91;85", "pick": "48;2;42;120;214", "on_pick": "38;2;255;255;255",
                     "err": "38;2;198;40;40", "warn": "38;2;180;83;9", "pick_ink": "38;2;42;120;214"},
           "mono": {"bar": "48;2;235;235;235", "ink": "38;2;0;0;0", "dim": "38;2;70;70;70", "pick": "48;2;205;205;205", "on_pick": "38;2;0;0;0;4",
                    "err": "38;2;198;40;40", "warn": "38;2;0;0;0", "pick_ink": "38;2;0;0;0"}}


def _ansi(row, col, text=""):
    return f"\x1b[{row + 1};{col + 1}H\x1b[0m{text}"


def _fit_text(text, width):
    text = "".join(c if c.isprintable() else " " for c in text)
    return text if len(text) <= width else text[:max(width - 1, 0)] + "…"


def _menu_items(s, ui=None):
    menu = s["menu"]
    if menu[0] == "context":
        return CONTEXT_MENUS[menu[1]]
    if menu[1] == 0 and ui and ui.boxed:
        return [*MENUS[0][1][:2], ("Done", "Ctrl-Q", ("command", "quit")), ("Cancel", "Ctrl-C", ("command", "cancel"))]
    return MENUS[menu[1]][1]


def _menu_box(s, ui):
    """Where the open menu is: (col, row, width), kept on the screen."""
    items = _menu_items(s, ui)
    width = max(len(item[0]) + len(item[1]) for item in items if item) + 5
    if s["menu"][0] == "bar":
        col, row = ui.titles[s["menu"][1]][0], MENU_ROWS
    else:
        col, row = s["menu"][2], s["menu"][3]
    return max(min(col, ui.cols - width), 0), max(min(row, ui.rows - len(items)), 0), width


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
                label, key, _ = item
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
        text = KEYS_HELP.replace("Ctrl-Q quit and print the SMILES", "Ctrl-Q done   Ctrl-C cancel") if ui.boxed else KEYS_HELP
        lines = text.split("\n")[:ui.canvas_rows]
        width = min(max(len(line) for line in lines) + 2, ui.canvas_cols)
        for index, line in enumerate(lines):
            out.append(ui.at(ui.top + index, ui.left, f"{bar} {_fit_text(line, width - 2):<{width - 2}} "))
    return "".join(out) + ("\x1b[0m" if out else "")


def _status(s, ui, smiles, problem, colours):
    dim, err, warn = (f"\x1b[{colours[name]}m" for name in ("dim", "err", "warn"))
    if s["entry"] is not None:
        width = ui.cols - 9
        shown = _fit_text(s["entry"][-width:], width)
        second = _fit_text(s["message"], ui.cols) if s["message"] else "Type or paste, then Enter to load it. Esc cancels."
        return f"\x1b[1mSMILES:\x1b[0m {shown}\x1b[7m \x1b[0m", f"{dim}{second}\x1b[0m"
    if smiles:
        first, details = _fit_text(smiles, ui.cols), facts(smiles)
    elif smiles is None:
        first = err + _fit_text(problem or "RDKit can't read this structure.", ui.cols) + "\x1b[0m"
        details = f"{err}Atoms in red boxes need attention.\x1b[0m"
    else:
        first, details = f"{dim}Empty. Click to draw a bond, or press u for the cyclohexane tool and click. Press ? for all keys.\x1b[0m", ""
    if s["message"]:
        return first, warn + _fit_text(s["message"], ui.cols) + "\x1b[0m"
    if s["label"]:
        return first, dim + _fit_text("Type an element or group, such as N, OMe, CF3 or Ph. Enter applies, Esc cancels.", ui.cols) + "\x1b[0m"
    if s["tip"] is not None and s["tip"] < len(PALETTE):
        return first, dim + _fit_text(PALETTE[s["tip"]][2], ui.cols) + "\x1b[0m"
    return first, details


def _place(layer, s, ui, png, cols, rows, row, col):
    """Show a new image of a layer, then remove the old one, so that the place never shows empty."""
    old = _IDS[layer] + s["flips"].get(layer, 0)
    s["flips"][layer] = 1 - s["flips"].get(layer, 0)
    new = _IDS[layer] + s["flips"][layer]
    sequence = graphics.kitty_sequence(png, cols, rows=rows, image_id=new, move_cursor=False, z=_Z[layer]).rstrip("\n")
    return ui.at(row, col) + sequence + f"\x1b_Ga=d,d=I,i={old},q=2\x1b\\"


def _border(s, ui, colours):
    """The box's edge, with its title and buttons."""
    dim, ink = f"\x1b[{colours['dim']}m", f"\x1b[{colours['ink']}m"
    title = f" {_fit_text(ui.title, ui.cols - 4)} " if ui.title else ""
    out = [ui.at(-1, -1, f"{dim}╭─\x1b[1m{ink}{title}\x1b[22m{dim}{'─' * (ui.cols - 1 - len(title))}╮")]
    for row in range(ui.rows):
        out.append(ui.at(row, -1, f"{dim}│") + ui.at(row, ui.cols, f"{dim}│"))
    out.append(ui.at(ui.rows, -1, f"{dim}╰{'─' * ui.cols}╯"))
    for name, label, col in ui.buttons():
        chosen = s["button"] == name
        style = f"\x1b[{colours['pick']};{colours['on_pick']}m" if chosen else f"\x1b[{colours['bar']};{colours['ink']}m"
        out.append(ui.at(ui.rows, col, style + label))
    out.append("\x1b[0m")
    return "".join(out)


def _backing(theme):
    """One pixel, stretched over the box, so that whatever a host has drawn below doesn't show through the drawing."""
    buffer = BytesIO()
    Image.new("RGBA", (1, 1), _BACKING.get(theme, _BACKING["dark"])).save(buffer, "PNG")
    return buffer.getvalue()


def images_gone():
    """Remove all of the editor's images, for a host closing the box."""
    return "".join(f"\x1b_Ga=d,d=I,i={base + flip},q=2\x1b\\" for base in _IDS.values() for flip in (0, 1))


def frame(s, ui, theme):
    """What to print for the current state: only the layers that changed, or nothing if the screen already shows it."""
    smiles, problem, wrong = analyse(s)
    colours = _CHROME.get(theme, _CHROME["dark"])
    screen = [ui.cols, ui.rows, ui.cell_width, ui.cell_height, theme, ui.box]

    def digest(*parts):
        return hashlib.sha256(json.dumps([screen, *parts]).encode()).hexdigest()[:16]

    active = next((i for i, entry in enumerate(PALETTE) if _is_active(s, entry[1])), None)
    marks = _marks(s, ui, wrong)
    popups = _popups(s, ui, colours)
    menu_bar = _menu_bar(s, ui, colours)
    status = _status(s, ui, smiles, problem, colours)
    keys = {"palette": digest(active, s["tip"]), "structure": digest(s["atoms"], s["bonds"], s["view"]),
            "overlay": digest(marks, s["view"]), "text": digest(popups, menu_bar, status, ui.title, s["button"])}
    if ui.boxed:
        keys["backing"] = digest()
    size = f"{ui.cols}x{ui.rows}+{ui.origin[0]}+{ui.origin[1]}"
    resized = s["size"] != size
    if not resized and all(s["shown"].get(name) == keys[name] for name in keys):
        return ""
    out = ["\x1b[?2026h"]  # terminals that know this show the whole frame at once
    if resized:
        if ui.boxed:  # what is beside the box is the host's
            out.append(images_gone())
            out.extend(ui.at(row, -1, " " * (ui.cols + 2)) for row in range(-1, ui.rows + 1))
        else:
            out.append("\x1b[2J")  # which also removes the images, so all of them are sent again
        s["size"] = size
        s["shown"] = {}
    if ui.boxed and s["shown"].get("backing") != keys["backing"]:
        out.append(_place("backing", s, ui, _backing(theme), ui.cols + 2, ui.rows + 2, -1, -1))
    if s["shown"].get("palette") != keys["palette"]:
        png = editor_icons.palette_image(_ICON_NAMES, ui.per_row, ui.button_cols * ui.cell_width, 2 * ui.cell_height, active, s["tip"], theme)
        out.append(_place("palette", s, ui, png, ui.per_row * ui.button_cols, ui.palette_rows, ui.top, 0))
    if s["shown"].get("structure") != keys["structure"]:
        out.append(_place("structure", s, ui, draw_structure(s, ui, theme), ui.canvas_cols, ui.canvas_rows, ui.top, ui.left))
    if s["shown"].get("overlay") != keys["overlay"]:
        if marks:
            out.append(_place("overlay", s, ui, draw_overlay(s, ui, marks), ui.canvas_cols, ui.canvas_rows, ui.top, ui.left))
        else:
            out.append(f"\x1b_Ga=d,d=I,i={_IDS['overlay'] + s['flips'].get('overlay', 0)},q=2\x1b\\")
    if s["shown"].get("text") != keys["text"]:
        if s["cover"]:  # plain cells again, so that the images show through where a menu was
            for row in range(ui.top, ui.top + ui.canvas_rows):
                out.append(ui.blank(row))
        s["cover"] = bool(popups)
        if ui.boxed:
            out.append(_border(s, ui, colours))
        out.append(menu_bar)
        out.append(popups)
        for index, text in enumerate(status):
            row = ui.rows - STATUS_ROWS + index
            out.append(ui.at(row, 0, ui.erase()) + ui.at(row, 0, text))
    s["shown"] = keys
    out.append("\x1b[?2026l")
    return "".join(out)


# Input


_TOKEN = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[([0-9;]*)([A-Za-z~])|\x1bO([A-Za-z])", re.S)
_INCOMPLETE = re.compile(r"\x1b(\[[<0-9;]*|O)?$")
_ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}
_TILDE_KEYS = {"3": "delete", "15": "f5", "17": "f6", "18": "f7", "19": "f8", "21": "f10"}
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
                elif match[6] == "~" and match[5].split(";")[0] in _TILDE_KEYS:
                    events.append(("key", _TILDE_KEYS[match[5].split(";")[0]]))
            elif match[7] in _ARROWS:
                events.append(("key", _ARROWS[match[7]]))
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
    for begin, end, _ in s["bonds"]:
        a, b = _atom(s, begin), _atom(s, end)
        dx, dy = b[2] - a[2], b[3] - a[3]
        span = dx * dx + dy * dy
        along = max(0, min(1, ((x - a[2]) * dx + (y - a[3]) * dy) / span)) if span else 0
        distance = math.hypot(a[2] + along * dx - x, a[3] + along * dy - y)
        if distance < nearest:
            best, nearest = ["b", begin, end], distance
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
    elif tool == "pan":
        pass
    elif target is None:
        _new_bond(s, x, y)
    elif target[0] == "a":
        s["focus"] = target
        _grow(s, target[1], "C", s["bond"])
    else:
        s["focus"], s["last"] = target, target
        _cycle_bond(s, target, s["bond"])


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


def _finish_drag(s, ui, press):
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
    if atom and atom[1] != symbol:
        _save(s)
        atom[1] = symbol


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


def _zoom(s, factor):
    s["view"][2] = min(max(s["view"][2] * factor, ZOOM_RANGE[0]), ZOOM_RANGE[1])


def clipboard_sequence(text):
    """Ask the terminal to put text on the system clipboard (OSC 52). It may refuse without saying so, as tmux does unless set to allow it."""
    return f"\x1b]52;c;{base64.b64encode(text.encode()).decode('ascii')}\x1b\\"


def take_clipboard(s):
    """The sequence for what was just copied, once: it's not part of the drawing, so it mustn't go on in the state."""
    text = s.pop("clipboard", None)
    return clipboard_sequence(text) if text else ""


def _copy(s):
    smiles, problem, _ = analyse(s)
    if smiles:
        s["clipboard"], s["message"] = smiles, "SMILES sent to the clipboard."
    else:
        s["message"] = f"Nothing to copy: {problem}" if problem else "Nothing to copy: the drawing is empty."


def _command(s, ui, name):
    """Run a command; returns what ends the editor, if it does: see `finish`."""
    if name == "quit":
        return True
    if name == "copy":
        _copy(s)
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
    return False


def _delete(s, target):
    if s["selected"]:
        _delete_selection(s)
    elif not _delete_target(s, target):
        s["message"] = "Point at an atom or bond first."


def _tidy(s, ui):
    if not s["atoms"]:
        return
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]))
    if mol is None:
        s["message"] = "Clean Up needs a valid structure."
        return
    _save(s)
    AllChem.Compute2DCoords(mol)
    kept = Chem.Mol(mol)
    _from_mol(s, kept)
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
            atom[1] = symbol


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
    middle = _direction(s, ident)
    spread = math.radians(35 if kind == "secondary" else 60)
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
_ATOM_MISSING = {"r": "the R label", "x": "the X label", "d": "the D label", ".": "the attachment point", "'": "the atom number"}
_BOND_KINDS = {"1": SINGLE, "2": DOUBLE, "3": TRIPLE, "w": WEDGE, "h": HASH}
_BOND_RINGS = {"a": "benzene", "z": "cyclopentadiene", "v": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8}
_BOND_MISSING = {"d": "the dashed bond", "b": "the bold bond", "B": "the bold double bond", "y": "the wavy bond", "H": "the plain hashed bond",
                 "l": "the double bond's position", "c": "the double bond's position", "r": "the double bond's position",
                 "f": "bringing a bond to the front", "9": "the chair cyclohexane", "0": "the chair cyclohexane"}
_GENERIC_TOOLS = {" ": ("tool", "select"), "x": ("bond", SINGLE), "t": ("tool", "text"), "j": ("ring", "benzene"), "J": ("ring", "cyclopentadiene"),
                  "u": ("ring", 6), "U": ("ring", 5)}
_GENERIC_MISSING = {"X": "the chain tool", "T": "the bracket tool", "e": "the arrow tool", "E": "the chemical symbols tool", "g": "the chromatography tool",
                    "G": "the orbital tool", "l": "the pen tool", "L": "the drawing elements tool", "k": "the fragmentation tool", "K": "the query tool"}
_COMMAND_KEYS = {"undo": "undo", "redo": "redo", "tidy": "tidy", "select_all": "select_all", "[": "zoom_out", "]": "zoom_in",
                 "f5": "actual", "f6": "fit", "f7": "zoom_in", "f8": "zoom_out"}
_ARROW_STEPS = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}
_DELETE_KEYS = ("backspace", "delete")


def _missing(s, what):
    s["message"] = f"ChemDraw has {what} on this key, but this editor doesn't."


def _remove_label(s, atom):
    """Back to plain carbon. Returns False if it already was."""
    if atom[1] == "C" and not atom[4]:
        return False
    _save(s)
    atom[1], atom[4] = "C", 0
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
        return CANCEL if ui.boxed else INTERRUPT
    if s["entry"] is not None:
        _entry_key(s, name)
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
    elif name == "f10":
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
    elif s["tool"] != "bond":
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
    if s["entry"] is not None:
        s["entry"] = None
        return False
    pressed = ui.button_at(col, row) if button == 0 else None
    if pressed == "copy":
        _copy(s)
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
        else:
            if not (event[1] & 32) and not event[4]:  # moving the pointer or letting go doesn't dismiss a message
                s["message"] = ""
            ended = _mouse(s, ui, event[1], event[2], event[3], event[4], pixels)
            if ended:
                return ended
    _settle(s)
    return False


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
        return QUIT, answer
    if ended == INTERRUPT:
        return FAILED, f"The drawing isn't valid chemistry, so there is no SMILES: {problem}"
    s["message"] = f"Not saved, because the drawing isn't valid: {problem}"
    return None


def step(state_text, smiles, raw, cols, rows, cell_width=None, cell_height=None, pixels=False, theme=graphics.DEFAULT_THEME, box=None, title=""):
    """Run one request: return (status, new state as text, what to print). With a `box`, see Layout, the editor stays inside it."""
    if box and (box[0] + box[2] > rows or box[1] + box[3] > cols):
        return FAILED, "", "The box doesn't fit in the window."
    if state_text:
        s = decode(state_text)
        first = False
    else:
        s = new_state()
        first = True
    pixels = pixels and bool(cell_width) and bool(cell_height)
    ui = Layout(cols, rows, cell_width, cell_height, pixels, box, title)
    if box and not fits(ui):
        return FAILED, "", "The box is too small for the editor."
    if ui.canvas_rows < 4 or ui.canvas_cols < 20:
        return CONTINUE, encode(s), "\x1b[2J\x1b[H" + "This window is too small for the editor."
    if first and smiles:
        if not load_smiles(s, smiles):
            return FAILED, "", s["message"]
        _fit(s, ui)
    ended = handle(s, ui, raw, pixels)
    result = finish(s, ended) if ended else None
    if result:
        return result[0], "", result[1]
    copied = take_clipboard(s)
    output = copied + frame(s, ui, theme)
    return CONTINUE, encode(s), output


def fits(ui):
    """Is a box big enough to use?"""
    return ui.canvas_rows >= 4 and ui.canvas_cols >= 20 and ui.cols >= MIN_BOX_COLS
