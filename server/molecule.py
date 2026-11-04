"""Parse SMILES and molblocks into sanitized RDKit molecules with 2D coordinates."""
from dataclasses import dataclass, field

from rdkit import Chem, rdBase
from rdkit.Chem import rdDepictor, rdMolDescriptors

import markush

FORMATS = ("auto", "smiles", "molblock")  # a SMILES may also be a CXSMARTS, which a query with atom lists is written as

# Layout time grows steeply with size (about 1 s at 500 ring atoms, 5 s at 1000), so size is capped before layout.
MAX_ATOMS = 500
MAX_SMILES_CHARS = 10_000
MAX_MOLBLOCK_CHARS = 100_000


class ParseError(ValueError):
    """Input that cannot become a molecule. `code` is stable for API clients, the message is for people."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Molecule:
    mol: Chem.Mol  # sanitized, with exactly one 2D conformer; a Markush structure's R-group definitions are drawn beside its core
    format: str  # "smiles", "smarts" or "molblock"
    coords: str  # "input" if the molblock's layout was kept, otherwise "computed"
    core: Chem.Mol = None  # without the R-group definitions, which are kept as text in `rgroups`, with their logic
    rgroups: dict = field(default_factory=dict)
    logic: dict = field(default_factory=dict)

    def to_dict(self):
        conformer = self.mol.GetConformer()
        atoms = []
        for atom in self.mol.GetAtoms():
            position = conformer.GetAtomPosition(atom.GetIdx())
            atoms.append({
                "index": atom.GetIdx(),
                "symbol": atom.GetSymbol(),
                "x": round(position.x, 4),
                "y": round(position.y, 4),
                "charge": atom.GetFormalCharge(),
                "aromatic": atom.GetIsAromatic(),
            })
        return {
            "format": self.format,
            "coords": self.coords,
            "name": self.mol.GetProp("_Name") if self.mol.HasProp("_Name") else "",
            "smiles": Chem.MolToSmiles(self.mol),
            "formula": rdMolDescriptors.CalcMolFormula(self.mol),
            "atoms": atoms,
            "bonds": [
                {"begin": b.GetBeginAtomIdx(), "end": b.GetEndAtomIdx(), "order": b.GetBondTypeAsDouble()}
                for b in self.mol.GetBonds()
            ],
        }


def detect_format(text):
    """A molblock always carries a version tag or an end marker; anything else is treated as SMILES."""
    return "molblock" if any(tag in text for tag in ("M  END", "V2000", "V3000")) else "smiles"


def _read(text, fmt, **options):
    if fmt == "smiles":
        return Chem.MolFromSmiles(text, **options)
    return Chem.MolFromMolBlock(text, **options)


def _rejection(text, fmt):
    """RDKit returns None without a reason, so re-read unsanitized to tell syntax from chemistry."""
    label = "SMILES" if fmt == "smiles" else "molblock"
    raw = _read(text, fmt, sanitize=False)
    if raw is None:
        return ParseError("syntax", f"Not valid {label}: check the input for typos or truncation.")
    try:
        Chem.SanitizeMol(raw)
    except Chem.MolSanitizeException as e:
        return ParseError("chemistry", f"The {label} parses but is not chemically valid: {e}")
    return ParseError("chemistry", f"The {label} parses but could not be processed.")


def _has_usable_layout(mol):
    if not mol.GetNumConformers() or mol.GetConformer().Is3D():
        return False
    return mol.GetConformer().GetPositions().any()


def parse(text, fmt="auto"):
    """Return a Molecule from SMILES or a molblock, or raise ParseError.

    From an SDF, only the first record is read. A molblock's own 2D layout is kept; 3D or all-zero
    coordinates are replaced by a computed layout.
    """
    if fmt not in FORMATS:
        raise ParseError("format", f"Unknown format {fmt!r}: use one of {', '.join(FORMATS)}.")
    if not isinstance(text, str) or not text.strip():
        raise ParseError("empty", "Enter a SMILES string or a molblock.")
    if "\0" in text:
        raise ParseError("syntax", "The input contains a null character.")

    if fmt == "auto":
        fmt = detect_format(text)
    if fmt == "smiles":
        text = text.strip()  # never strip a molblock: its first three lines are positional
        if "\n" in text or "\r" in text:
            raise ParseError("syntax", "Enter one SMILES string at a time.")

    limit = MAX_SMILES_CHARS if fmt == "smiles" else MAX_MOLBLOCK_CHARS
    if len(text) > limit:
        raise ParseError("too_large", f"The input is {len(text):,} characters; the limit is {limit:,}.")

    definitions, logic = {}, {}
    if fmt == "smiles":
        try:
            text, definitions, logic = markush.separate(text)
        except markush.MarkushError as e:
            raise ParseError("syntax", str(e)) from e
    with rdBase.BlockLogs():  # failures are reported through ParseError, not stderr
        mol = _read(text, fmt)
        if mol is None and fmt == "smiles" and _read(text, fmt, sanitize=False) is None:
            mol = _query(text)
            fmt = "smarts" if mol is not None else fmt
        if mol is None:
            raise _rejection(text, fmt)

    if mol.GetNumAtoms() == 0:
        raise ParseError("empty", "The input contains no atoms.")
    if mol.GetNumAtoms() > MAX_ATOMS:
        raise ParseError("too_large", f"The molecule has {mol.GetNumAtoms():,} atoms; the limit is {MAX_ATOMS:,}.")

    coords = "input"
    if not _has_usable_layout(mol):
        rdDepictor.Compute2DCoords(mol)
        coords = "computed"
    core = mol
    if definitions:
        try:
            mol = markush.displayed(core, definitions)
        except markush.MarkushError as e:
            raise ParseError("syntax", str(e)) from e
        coords = "computed"
    return Molecule(mol=mol, format=fmt, coords=coords, core=core, rgroups=definitions, logic=logic)


def _query(text):
    """A SMARTS, as the editor writes a query with atom lists or query bonds: a molecule that can be drawn, or None."""
    mol = Chem.MolFromSmarts(text)
    if mol is None:
        return None
    mol.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(mol)
    return mol
