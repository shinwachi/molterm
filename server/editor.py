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

from rdkit import Chem, rdBase
from rdkit.Chem import AllChem, Crippen, Descriptors, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D
from rdkit.Geometry import Point2D

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
TOOLS = ("atom", "bond", "ring", "move", "erase")
ELEMENTS = ("C", "N", "O", "S", "P", "F", "Cl", "Br", "I", "H")
RINGS = (3, 4, 5, 6, 7, "benzene")
STATUS_ROWS = 3
ZOOM_RANGE = (1.5, 16.0)  # cell widths per bond
DEFAULT_ZOOM = 6.0
DRAG_START = 0.35  # bond lengths the pointer must travel before a press counts as a drag
MERGE = 0.25  # a new ring atom this close to an existing one becomes that atom

CYAN, RED = (0.37, 0.89, 0.89), (1.0, 0.35, 0.3)
CONTINUE, QUIT, FAILED = 0, 1, 2

KEYS_HELP = """Mouse
  Click an empty space      place an atom, or a ring with a ring tool
  Click an atom             set its element (atom tool), grow a bond (bond tool), add a ring (ring tool)
  Click a bond              change its order (bond tool), or fuse a ring onto it (ring tool)
  Drag from an atom         draw a bond, at 30 degree steps, or onto another atom
  Right-click               delete what is under the pointer
  Wheel                     zoom
  Move tool                 drag an atom to move it, or an empty space to pan

Keyboard
  Arrows, Tab               move between atoms and bonds       Shift+arrows  pan        [ ]  zoom
  Enter, Space              grow a bond from the chosen atom   Esc           choose nothing, so Enter starts a new piece
  c n o s p f i h           element, also l for Cl and b for Br
  - = #  w d                single, double, triple, wedge, hash; changes the bond just drawn or chosen
  3 4 5 6 7, a              add a ring, a is benzene           + ~           charge up, down
  x, Backspace, Delete      delete                             m e           move and erase tools
  u r                       undo, redo                         t v           tidy the layout, fit to window
  /                         type a SMILES                      q             quit and print the SMILES
  ?                         this help"""


class StateError(ValueError):
    """The client sent a drawing that can't have come from here."""


# The drawing as text


def new_state():
    return {"atoms": [], "bonds": [], "next": 1, "tool": "bond", "element": "C", "bond": SINGLE, "ring": "benzene",
            "view": [0.0, 0.0, DEFAULT_ZOOM], "focus": None, "hover": None, "last": None, "press": None, "drag": None,
            "undo": [], "redo": [], "entry": None, "help": False, "message": "", "frame": 0, "shown": "", "size": None,
            "partial": ""}


def encode(state):
    packed = zlib.compress(json.dumps(state, separators=(",", ":")).encode(), 6)
    return base64.urlsafe_b64encode(packed).decode()


def decode(text):
    if len(text) > MAX_STATE:
        raise StateError("The drawing is too large.")
    try:
        unpacker = zlib.decompressobj()
        raw = unpacker.decompress(base64.urlsafe_b64decode(text), MAX_STATE_JSON)
        if unpacker.unconsumed_tail:
            raise StateError("The drawing is too large.")
        state = json.loads(raw)
        _check_state(state)
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


def _check_state(s):
    _check_structure(s["atoms"], s["bonds"], s["next"])
    if s["tool"] not in TOOLS or s["element"] not in ELEMENTS or s["bond"] not in _ORDER or isinstance(s["bond"], bool):
        raise StateError("A tool in the drawing is not valid.")
    if s["ring"] not in RINGS or isinstance(s["ring"], bool):
        raise StateError("A tool in the drawing is not valid.")
    x, y, zoom = s["view"]
    _number(x)
    _number(y)
    _number(zoom)
    if not ZOOM_RANGE[0] <= zoom <= ZOOM_RANGE[1]:
        raise StateError("A number in the drawing is out of range.")
    for name in ("focus", "hover", "last"):
        _check_target(s[name])
    for stack in (s["undo"], s["redo"]):
        if not isinstance(stack, list) or len(stack) > MAX_UNDO:
            raise StateError("The drawing is too large.")
        for atoms, bonds, next_id in stack:
            _check_structure(atoms, bonds, next_id)
    if s["press"] is not None:
        press = s["press"]
        for value in (*press["cell"], *press["view"], *press["model"]):
            _number(value)
        if len(press["cell"]) != 2 or len(press["view"]) != 3 or len(press["model"]) != 2:
            raise StateError("A choice in the drawing is not valid.")
        _check_target(press["target"])
        if press["moved"] not in (True, False):
            raise StateError("A choice in the drawing is not valid.")
    if s["drag"] is not None:
        if len(s["drag"]) != 2:
            raise StateError("A choice in the drawing is not valid.")
        _number(s["drag"][0])
        _number(s["drag"][1])
    if s["entry"] is not None and (not isinstance(s["entry"], str) or len(s["entry"]) > MAX_ENTRY):
        raise StateError("The text in the drawing is too long.")
    if s["help"] not in (True, False) or not isinstance(s["message"], str) or not isinstance(s["shown"], str):
        raise StateError("A choice in the drawing is not valid.")
    _integer(s["frame"])
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
    for name in ("focus", "hover", "last"):
        target = s[name]
        if target and ((target[0] == "a" and not _atom(s, target[1])) or (target[0] == "b" and not _bond(s, target[1], target[2]))):
            s[name] = None


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


def _facts(smiles):
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


def _direction(s, ident, kind=SINGLE):
    """The angle for a new bond from an atom: into the largest gap, and straight on beside a triple bond."""
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
        # both turns are 120 degrees from the bond; the one away from the rest of the molecule makes a zigzag
        return max((back + math.radians(120), back - math.radians(120)), key=room)
    gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % (2 * math.pi) or 2 * math.pi for i in range(len(angles))]
    widest = gaps.index(max(gaps))
    return angles[widest] + gaps[widest] / 2


def _grow(s, ident, element, kind):
    angle = _direction(s, ident, kind)
    atom = _atom(s, ident)
    _save(s)
    new = _add_atom(s, element, atom[2] + math.cos(angle), atom[3] + math.sin(angle))
    if new is None:
        s["undo"].pop()
        return None
    _set_bond(s, ident, new, kind)
    s["focus"] = ["a", new]
    s["last"] = ["b", ident, new]
    return new


def _free_spot(s, ui):
    if not s["atoms"]:
        return s["view"][0], s["view"][1]
    return max(a[2] for a in s["atoms"]) + 3.0, sum(a[3] for a in s["atoms"]) / len(s["atoms"])


def _vertex(s, x, y):
    """The atom at a position, made if there isn't one yet."""
    for atom in s["atoms"]:
        if math.hypot(atom[2] - x, atom[3] - y) < MERGE:
            return atom[0]
    return _add_atom(s, "C", x, y)


def _add_ring(s, size, centre_x, centre_y, start_angle, shared=()):
    """Make a regular ring, reusing atoms already at its corners, with alternating double bonds if it is benzene."""
    atoms = len(s["atoms"])
    sides = 6 if size == "benzene" else size
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
    if size == "benzene":
        _alternate(s, edges)
    return True


def _alternate(s, edges):
    def has_double(ident, skip):
        return any(b[2] in (DOUBLE, TRIPLE) and ident in b[:2] and b is not skip for b in s["bonds"])

    def doubles_from(offset):
        return [edges[(offset + 2 * i) % 6] for i in range(3)]

    def usable(edge):
        bond = _bond(s, *edge)
        return bond[2] == DOUBLE or (bond[2] == SINGLE and not has_double(edge[0], bond) and not has_double(edge[1], bond))

    best = max((0, 1), key=lambda offset: sum(usable(edge) for edge in doubles_from(offset)))
    for edge in doubles_from(best):
        if usable(edge):
            _bond(s, *edge)[2] = DOUBLE


def _ring_on_target(s, target, size, x, y):
    """Add a ring: fused onto a bond, attached to an atom, or free in an empty space."""
    sides = 6 if size == "benzene" else size
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
        if _neighbours(s, target[1]):  # joined by a bond, as for a phenyl group
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


# The screen


class Layout:
    """Where everything is on the screen. Columns and rows here count from 0."""

    def __init__(self, cols, rows, cell_width=None, cell_height=None):
        self.cols, self.rows = cols, rows
        self.cell_width, self.cell_height = cell_width or DEFAULT_CELL[0], cell_height or DEFAULT_CELL[1]
        self.buttons = []
        col = row = 0
        for group in _BUTTONS:
            width = sum(len(label) + 3 for label, _ in group)
            if col and col + width > cols:
                row += 1
                col = 0
            for label, action in group:
                self.buttons.append((row, col, len(label) + 2, label, action))
                col += len(label) + 3
            col += 1
        self.top = row + 1
        self.canvas_rows = rows - self.top - STATUS_ROWS
        self.canvas_cols = cols

    def button_at(self, col, row):
        for button_row, start, width, _, action in self.buttons:
            if row == button_row and start <= col < start + width:
                return action
        return None

    def to_model(self, s, col, row):
        """Canvas position in cells, as floats, to the molecule's coordinates. y runs upwards there."""
        x, y, zoom = s["view"]
        return (x + (col - self.canvas_cols / 2) / zoom,
                y - (row - self.top - self.canvas_rows / 2) * self.cell_height / self.cell_width / zoom)

    def in_canvas(self, row):
        return self.top <= row < self.top + self.canvas_rows


_BUTTONS = [
    [(e, ("element", e)) for e in ELEMENTS],
    [("-", ("bond", SINGLE)), ("=", ("bond", DOUBLE)), ("#", ("bond", TRIPLE)), ("Wedge", ("bond", WEDGE)), ("Hash", ("bond", HASH))],
    [(str(n), ("ring", n)) for n in RINGS[:-1]] + [("Benzene", ("ring", "benzene"))],
    [("+", ("charge", 1)), ("~", ("charge", -1)), ("Move", ("tool", "move")), ("Erase", ("tool", "erase"))],
    [("Undo", ("command", "undo")), ("Redo", ("command", "redo")), ("Tidy", ("command", "tidy")), ("Fit", ("command", "fit")),
     ("SMILES", ("command", "entry")), ("Help", ("command", "help")), ("Quit", ("command", "quit"))],
]


def _is_active(s, action):
    kind, value = action
    return ((kind == "element" and s["tool"] == "atom" and s["element"] == value)
            or (kind == "bond" and s["tool"] == "bond" and s["bond"] == value)
            or (kind == "ring" and s["tool"] == "ring" and s["ring"] == value)
            or (kind == "tool" and s["tool"] == value)
            or (kind == "command" and value == "help" and s["help"]))


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
    angle = round(math.atan2(y - origin_y, x - origin_x) / math.radians(30)) * math.radians(30)
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


def _mark(drawer, points, colour, alpha, zoom_px, outline=False):
    drawer.SetColour((*colour, alpha))
    if len(points) == 1:
        # an outline leaves the atom's label readable under it
        drawer.SetFillPolys(not outline)
        drawer.SetLineWidth(max(int(zoom_px * 0.05), 2))
        drawer.DrawArc(Point2D(*points[0]), 0.42, 0, 360)
    elif len(points) == 2:
        drawer.SetLineWidth(max(int(zoom_px * 0.2), 4))
        drawer.DrawLine(Point2D(*points[0]), Point2D(*points[1]))


def draw(s, ui, theme, problems):
    """One frame of the canvas, as (PNG bytes)."""
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
    options.prepareMolsBeforeDrawing = False
    options.padding = 0
    options.setAtomPalette(graphics.palette(theme))
    options.bondLineWidth = 2
    options.useMolBlockWedging = True
    options.fixedFontSize = max(int(per_unit * 0.42), 8)
    # model units are 1.5 per bond in the molblock, as RDKit expects
    drawer.SetScale(width_px, height_px, Point2D((x - half_x) * 1.5, (y - half_y) * 1.5), Point2D((x + half_x) * 1.5, (y + half_y) * 1.5))
    mol = _raw_mol(s)
    with rdBase.BlockLogs():
        Chem.ReapplyMolBlockWedging(mol)
        drawer.DrawMolecule(mol)

    def scaled(points):
        return [(px * 1.5, py * 1.5) for px, py in points]

    for index in problems:
        if index < len(s["atoms"]):
            atom = s["atoms"][index]
            _mark(drawer, scaled([(atom[2], atom[3])]), RED, 0.45, per_unit)
    if s["hover"] and s["hover"] != s["focus"]:
        _mark(drawer, scaled(_target_points(s, s["hover"])), CYAN, 0.25, per_unit)
    if s["focus"]:
        _mark(drawer, scaled(_target_points(s, s["focus"])), CYAN, 0.9 if s["focus"][0] == "a" else 0.5, per_unit, outline=True)
    press = s["press"]
    if press and press["moved"] and s["drag"] and s["tool"] in ("atom", "bond"):
        end_x, end_y, _ = _drag_end(s, ui, press)
        start = _atom(s, press["target"][1]) if press["target"] and press["target"][0] == "a" else None
        begin = (start[2], start[3]) if start else tuple(press["model"])
        drawer.SetColour((*CYAN, 0.9))
        drawer.SetLineWidth(3)
        drawer.DrawLine(*(Point2D(*p) for p in scaled([begin, (end_x, end_y)])))
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def _ansi(row, col, text=""):
    return f"\x1b[{row + 1};{col + 1}H\x1b[0m{text}"


def _fit_text(text, width):
    text = "".join(c if c.isprintable() else " " for c in text)
    return text if len(text) <= width else text[:max(width - 1, 0)] + "…"


def _status(s, ui, smiles, problem):
    if s["entry"] is not None:
        width = ui.cols - 8
        return f"\x1b[1mSMILES:\x1b[0m {s['entry'][-width:]}\x1b[7m \x1b[0m", "\x1b[90mType or paste, then Enter to load it. Esc cancels.\x1b[0m", ""
    if smiles:
        first = _fit_text(smiles, ui.cols)
        second = _facts(smiles)
    elif smiles is None:
        first = "\x1b[31m" + _fit_text(problem or "RDKit can't read this structure.", ui.cols) + "\x1b[0m"
        second = "\x1b[31mThe atoms marked in red need attention.\x1b[0m"
    else:
        first = "\x1b[90mEmpty. Click anywhere, or press Enter, to begin.\x1b[0m"
        second = ""
    if s["message"]:
        second = "\x1b[33m" + _fit_text(s["message"], ui.cols) + "\x1b[0m"
    hints = {"atom": "Atom: click to place or set an element, drag from an atom to draw a bond",
             "bond": "Bond: click an atom to grow, a bond to change it, or drag from an atom",
             "ring": "Ring: click a bond to fuse, an atom to attach, or an empty space to place",
             "move": "Move: drag an atom to move it, or an empty space to pan",
             "erase": "Erase: click an atom or bond to delete it"}
    third = "\x1b[90m" + _fit_text(hints[s["tool"]] + "   ? help   q quit", ui.cols) + "\x1b[0m"
    return first, second, third


def frame(s, ui, theme):
    """What to print for the current state, or an empty string if the screen already shows it."""
    smiles, problem, wrong = analyse(s)
    visual = json.dumps([s["atoms"], s["bonds"], s["tool"], s["element"], s["bond"], s["ring"], s["view"], s["focus"], s["hover"],
                         s["entry"], s["help"], s["message"], s["drag"] if s["press"] and s["press"]["moved"] else None,
                         ui.cols, ui.rows, ui.cell_width, ui.cell_height, theme])
    key = hashlib.sha256(visual.encode()).hexdigest()[:16]
    if key == s["shown"]:
        return ""
    s["shown"] = key
    size = f"{ui.cols}x{ui.rows}"
    out = ["\x1b[?2026h"]  # terminals that know this show the whole frame at once
    if s["size"] != size:
        out.append("\x1b[2J")
        s["size"] = size
    for row in range(ui.top):
        out.append(_ansi(row, 0, "\x1b[K"))
    for row, col, _, label, action in ui.buttons:
        style = "\x1b[7m" if _is_active(s, action) else ""
        out.append(_ansi(row, col, f"{style} {label} \x1b[0m"))
    old = _IMAGE_ID + s["frame"] % 2
    s["frame"] = (s["frame"] + 1) % 2
    new = _IMAGE_ID + s["frame"] % 2
    if s["help"]:
        out.append(f"\x1b_Ga=d,d=I,i={old},q=2\x1b\\")
        for row in range(ui.top, ui.top + ui.canvas_rows):
            out.append(_ansi(row, 0, "\x1b[K"))
        for index, line in enumerate(KEYS_HELP.split("\n")[:ui.canvas_rows]):
            out.append(_ansi(ui.top + index, 0, _fit_text(line, ui.cols)))
        s["size"] = "help"  # so that the next frame of the canvas clears this text away
    else:
        out.append(_ansi(ui.top, 0))
        png = draw(s, ui, theme, wrong)
        # the new image goes up before the old one comes down, so the canvas never shows empty
        out.append(graphics.kitty_sequence(png, ui.canvas_cols, rows=ui.canvas_rows, image_id=new, move_cursor=False).rstrip("\n"))
        out.append(f"\x1b_Ga=d,d=I,i={old},q=2\x1b\\")
    for index, text in enumerate(_status(s, ui, smiles, problem)):
        out.append(_ansi(ui.rows - STATUS_ROWS + index, 0, "\x1b[K" + text))
    out.append("\x1b[?2026l")
    return "".join(out)


_IMAGE_ID = 7001


# Input


_TOKEN = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[([0-9;]*)([A-Za-z~])|\x1bO([A-Za-z])", re.S)
_INCOMPLETE = re.compile(r"\x1b(\[[<0-9;]*|O)?$")
_ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}


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
                elif match[6] == "~" and match[5].split(";")[0] == "3":
                    events.append(("key", "delete"))
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
        named = {"\r": "enter", "\n": "enter", "\t": "tab", "\x7f": "backspace", "\b": "backspace", "\x03": "quit", "\x0c": "redraw"}
        if char in named:
            events.append(("key", named[char]))
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


def _active_kind(s):
    return s["bond"] if s["tool"] == "bond" else SINGLE


def _click(s, ui, target, x, y):
    tool = s["tool"]
    if tool == "erase":
        _delete_target(s, target)
    elif tool == "ring":
        _ring_on_target(s, target, s["ring"], x, y)
    elif tool == "move":
        s["focus"] = target
    elif target is None:
        _save(s)
        new = _add_atom(s, s["element"] if tool == "atom" else "C", x, y)
        if new is None:
            s["undo"].pop()
        else:
            s["focus"], s["last"] = ["a", new], None
    elif target[0] == "a":
        atom = _atom(s, target[1])
        s["focus"] = target
        if tool == "atom":
            if atom[1] != s["element"]:
                _save(s)
                atom[1] = s["element"]
        else:
            _grow(s, target[1], "C", s["bond"])
    else:
        s["focus"] = target
        s["last"] = target
        if tool == "bond":
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
    if s["tool"] not in ("atom", "bond"):
        return
    end_x, end_y, reached = _drag_end(s, ui, press)
    element = s["element"] if s["tool"] == "atom" else "C"
    _save(s)
    if press["target"] and press["target"][0] == "a":
        start = press["target"][1]
    else:
        start = _add_atom(s, "C", *press["model"])
    if start is None:
        s["undo"].pop()
        return
    end = reached if reached else _add_atom(s, element, end_x, end_y)
    if end is None:
        s["undo"].pop()
        return
    _set_bond(s, start, end, _active_kind(s))
    s["focus"], s["last"] = ["a", end], ["b", start, end]


def _button(s, ui, action):
    kind, value = action
    if kind == "element":
        s["tool"], s["element"] = "atom", value
    elif kind == "bond":
        s["tool"], s["bond"] = "bond", value
    elif kind == "ring":
        s["tool"], s["ring"] = "ring", value
    elif kind == "tool":
        s["tool"] = value
    elif kind == "charge":
        _charge(s, value)
    elif kind == "command":
        return _command(s, ui, value)
    return False


def _charge(s, step):
    if not s["focus"] or s["focus"][0] != "a":
        s["message"] = "Choose an atom first."
        return
    atom = _atom(s, s["focus"][1])
    if abs(atom[4] + step) <= 8:
        _save(s)
        atom[4] += step


def _command(s, ui, name):
    """Run a command; returns True to quit."""
    if name == "quit":
        return True
    if name == "undo":
        _restore(s, "undo", "redo")
    elif name == "redo":
        _restore(s, "redo", "undo")
    elif name == "tidy":
        _tidy(s, ui)
    elif name == "fit":
        _fit(s, ui)
    elif name == "entry":
        s["entry"], s["help"] = "", False
    elif name == "help":
        s["help"] = not s["help"]
    return False


def _tidy(s, ui):
    if not s["atoms"]:
        return
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(to_molblock(s["atoms"], s["bonds"]))
    if mol is None:
        s["message"] = "Tidy needs a valid structure."
        return
    _save(s)
    AllChem.Compute2DCoords(mol)
    kept = Chem.Mol(mol)
    _from_mol(s, kept)
    s["focus"] = None
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


def _focus_point(s):
    points = _target_points(s, s["focus"]) if s["focus"] else []
    if not points:
        return s["view"][0], s["view"][1]
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _move_focus(s, dx, dy):
    """Go to the nearest atom in a direction, by how far along it and how far off to the side."""
    if not s["atoms"]:
        return
    x, y = _focus_point(s)
    current = s["focus"][1] if s["focus"] and s["focus"][0] == "a" else None
    if not s["focus"]:
        s["focus"] = ["a", min(s["atoms"], key=lambda a: math.hypot(a[2] - x, a[3] - y))[0]]
        return
    best, score = None, float("inf")
    for atom in s["atoms"]:
        if atom[0] == current:
            continue
        along = (atom[2] - x) * dx + (atom[3] - y) * dy
        across = abs((atom[2] - x) * dy - (atom[3] - y) * dx)
        if along > 0.05 and across <= along * 2.5:
            value = along + across * 1.5
            if value < score:
                best, score = atom[0], value
    if best is not None:
        s["focus"] = ["a", best]


def _key_bond(s, kind):
    target = s["focus"] if s["focus"] and s["focus"][0] == "b" else None
    if not target and s["last"] and s["focus"] and s["focus"][1] in s["last"][1:]:
        target = s["last"]
    bond = _bond(s, target[1], target[2]) if target else None
    if not bond:  # nothing to change, so it is for the bonds to come
        s["tool"], s["bond"] = "bond", kind
    elif bond[2] == kind:
        if kind in (WEDGE, HASH):
            _cycle_bond(s, target, kind)
    else:  # only this bond: the next one is still whatever the tool says
        _save(s)
        _set_kind(s, bond, kind)


def _key_ring(s, ui, size):
    s["tool"], s["ring"] = "ring", size
    x, y = _free_spot(s, ui)
    _ring_on_target(s, s["focus"], size, x, y)


def _key_element(s, ui, element):
    s["tool"], s["element"] = "atom", element
    if s["focus"] and s["focus"][0] == "a":
        atom = _atom(s, s["focus"][1])
        if atom[1] != element:
            _save(s)
            atom[1] = element
    elif not s["atoms"]:
        _save(s)
        s["focus"] = ["a", _add_atom(s, element, s["view"][0], s["view"][1])]


_ELEMENT_KEYS = {"c": "C", "n": "N", "o": "O", "s": "S", "p": "P", "f": "F", "i": "I", "h": "H", "l": "Cl", "b": "Br"}
_BOND_KEYS = {"-": SINGLE, "=": DOUBLE, "#": TRIPLE, "w": WEDGE, "d": HASH}
_RING_KEYS = {"3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "a": "benzene"}
_COMMAND_KEYS = {"u": "undo", "r": "redo", "t": "tidy", "v": "fit", "/": "entry", "?": "help", "q": "quit", "quit": "quit"}
_ARROW_STEPS = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}


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


def _key(s, ui, name):
    """Handle one key; returns True to quit."""
    if name == "quit":
        return True
    if s["entry"] is not None:
        _entry_key(s, name)
        return False
    if s["help"] and name not in ("?", "q"):
        s["help"] = False
        return False
    if name in _COMMAND_KEYS:
        return _command(s, ui, _COMMAND_KEYS[name])
    if name in _ELEMENT_KEYS:
        _key_element(s, ui, _ELEMENT_KEYS[name])
    elif name in _BOND_KEYS:
        _key_bond(s, _BOND_KEYS[name])
    elif name in _RING_KEYS:
        _key_ring(s, ui, _RING_KEYS[name])
    elif name in ("enter", " "):
        element = s["element"] if s["tool"] == "atom" else "C"
        if s["focus"] and s["focus"][0] == "a":
            _grow(s, s["focus"][1], element, _active_kind(s))
        else:
            _save(s)
            new = _add_atom(s, element, *_free_spot(s, ui))
            if new is None:
                s["undo"].pop()
            else:
                s["focus"], s["last"] = ["a", new], None
    elif name in _ARROW_STEPS:
        dx, dy = _ARROW_STEPS[name]
        if s["tool"] == "move" and s["focus"] and s["focus"][0] == "a":
            _save(s)
            atom = _atom(s, s["focus"][1])
            atom[2] += dx * 0.25
            atom[3] += dy * 0.25
        else:
            _move_focus(s, dx, dy)
    elif name.startswith("shift-") and name[6:] in _ARROW_STEPS:
        dx, dy = _ARROW_STEPS[name[6:]]
        s["view"][0] += dx
        s["view"][1] += dy
    elif name in ("tab", "shift-tab"):
        order = _targets_in_order(s)
        if order:
            step = 1 if name == "tab" else -1
            position = order.index(s["focus"]) if s["focus"] in order else (-1 if step == 1 else 0)
            s["focus"] = order[(position + step) % len(order)]
    elif name in ("x", "backspace", "delete"):
        if not _delete_target(s, s["focus"]):
            s["message"] = "Choose an atom or bond first."
    elif name == "esc":
        s["focus"], s["press"], s["drag"] = None, None, None
    elif name == "m":
        s["tool"] = "move"
    elif name == "e":
        s["tool"] = "erase"
    elif name == "+":
        _charge(s, 1)
    elif name == "~":
        _charge(s, -1)
    elif name == "[":
        s["view"][2] = max(s["view"][2] / 1.2, ZOOM_RANGE[0])
    elif name == "]":
        s["view"][2] = min(s["view"][2] * 1.2, ZOOM_RANGE[1])
    elif name == "redraw":
        s["shown"], s["size"] = "", None
    if name in ("enter", " ") or name in _RING_KEYS or name in _ARROW_STEPS:
        _keep_visible(s, ui)
    return False


def _mouse(s, ui, code, x_cell, y_cell, release, pixels):
    """Handle one mouse report; returns True to quit."""
    if pixels:
        col, row = x_cell / ui.cell_width, y_cell / ui.cell_height
    else:
        col, row = x_cell - 0.5, y_cell - 0.5
    button, motion, wheel = code & 3, bool(code & 32), bool(code & 64)
    x, y = ui.to_model(s, col, row)
    inside = ui.in_canvas(int(row)) and not s["help"]
    if wheel:
        if inside:
            factor = 1.15 if button == 0 else 1 / 1.15
            zoom = min(max(s["view"][2] * factor, ZOOM_RANGE[0]), ZOOM_RANGE[1])
            s["view"][2] = zoom
            after_x, after_y = ui.to_model(s, col, row)  # the point under the pointer stays there
            s["view"][0] += x - after_x
            s["view"][1] += y - after_y
        return False
    if motion:
        press = s["press"]
        if press:
            s["drag"] = [x, y]
            start_x, start_y = press["model"]
            moved = math.hypot(x - start_x, y - start_y) > DRAG_START
            if moved and not press["moved"]:
                press["moved"] = True
                if s["tool"] == "move" and press["target"] and press["target"][0] == "a":
                    _save(s)
            if press["moved"] and s["tool"] == "move":
                if press["target"] and press["target"][0] == "a":
                    atom = _atom(s, press["target"][1])
                    if atom:
                        atom[2], atom[3] = x, y
                elif not press["target"]:
                    view_x, view_y, zoom = press["view"]
                    s["view"][0] = view_x - (col - press["cell"][0]) / zoom
                    s["view"][1] = view_y + (row - press["cell"][1]) * ui.cell_height / ui.cell_width / zoom
            s["hover"] = None
        else:
            s["hover"] = _target_at(s, ui, x, y) if inside else None
        return False
    if release:
        press, s["press"] = s["press"], None
        if button == 0 and press:
            if press["moved"]:
                if s["tool"] in ("atom", "bond"):
                    s["drag"] = s["drag"] or [x, y]
                    _finish_drag(s, ui, press)
            elif s["entry"] is None:
                _click(s, ui, press["target"], *press["model"])
        s["drag"] = None
        s["hover"] = _target_at(s, ui, x, y) if inside else None
        return False
    # a press
    if button == 0 and int(row) < ui.top:
        action = ui.button_at(int(col), int(row))
        if action:
            s["help"] = False if action != ("command", "help") else s["help"]
            s["entry"] = None
            return _button(s, ui, action)
        return False
    if not inside or s["entry"] is not None:
        return False
    target = _target_at(s, ui, x, y)
    if button == 2:
        _delete_target(s, target)
    elif button == 0:
        s["press"] = {"cell": [col, row], "view": list(s["view"]), "model": [x, y], "target": target, "moved": False}
        s["drag"] = None
    return False


def handle(s, ui, raw, pixels):
    """Apply a run of raw input to the state; returns True to quit."""
    events, s["partial"] = parse_events(s["partial"] + raw)
    if len(s["partial"]) > 64:
        s["partial"] = ""
    for event in events:
        if event[0] == "key":
            s["message"] = ""
            if _key(s, ui, event[1]):
                return True
        else:
            if not (event[1] & 32):  # moving the pointer doesn't dismiss a message
                s["message"] = ""
            if _mouse(s, ui, event[1], event[2], event[3], event[4], pixels):
                return True
    return False


def step(state_text, smiles, raw, cols, rows, cell_width=None, cell_height=None, pixels=False, theme=graphics.DEFAULT_THEME):
    """Run one request: return (status, new state as text, what to print)."""
    if state_text:
        s = decode(state_text)
        first = False
    else:
        s = new_state()
        first = True
    ui = Layout(cols, rows, cell_width, cell_height)
    if ui.canvas_rows < 4:
        return CONTINUE, encode(s), "\x1b[2J\x1b[H" + "This window is too small for the editor."
    if first and smiles:
        if not load_smiles(s, smiles):
            return FAILED, "", s["message"]
        _fit(s, ui)
    if handle(s, ui, raw, pixels and bool(cell_width) and bool(cell_height)):
        answer, problem, _ = analyse(s)
        if answer is None:
            return FAILED, "", f"The drawing isn't valid chemistry, so there is no SMILES: {problem}"
        return QUIT, "", answer
    output = frame(s, ui, theme)
    return CONTINUE, encode(s), output
