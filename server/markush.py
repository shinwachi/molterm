"""Markush structures as text: the parts of ChemAxon's extended SMILES (CXSMILES) that RDKit doesn't read or write.

A Markush structure is a core with variable parts. RDKit reads most of them from CXSMILES itself: R-group labels and generic atoms as
`$...$` labels (`_R1`, `Q_e`, `A_p`), position variation (`m:`), repeating units (`Sg:`) and link nodes (`LN:`). What it can't read is
the definitions of the R-groups and their logic, which ChemAxon writes after `RG:` and `LOG=`:

    *C1CCCCC1* |$_R1;;;;;;;_R2$,RG:_R1={CCC},{N},_R2={O},LOG={_R1:;;>0._R2:_R1;H;0,1}|

Here R1 is propyl or an amino group, and R2 is a hydroxyl; R1 occurs at least once; R2 only if R1 does, its rest are hydrogen, and it
occurs 0 or 1 times. A definition's first atom is where it attaches. One that attaches twice, as a bridge does, marks its two
attachment points with atom maps 1 and 2: {[CH2:1]C[CH2:2]}.

This module takes those fields off before RDKit reads a structure, and puts them back when one is written. It also writes `m:`, which
RDKit reads but doesn't write.
"""
import re

from rdkit import Chem, rdBase
from rdkit.Chem import rdDepictor

MAX_RGROUP = 32
MAX_FRAGMENTS = 12  # definitions of one R-group
# the generic atoms, by the editor's symbol: (ChemAxon's CXSMILES label, what they stand for)
GENERICS = {"A": ("A_p", "any atom but hydrogen"), "AH": ("AH_p", "any atom"), "Q": ("Q_e", "any atom but carbon or hydrogen"),
            "QH": ("QH_p", "any atom but carbon"), "X": ("X_p", "a halogen"), "XH": ("XH_p", "a halogen or hydrogen"),
            "M": ("M_p", "a metal"), "MH": ("MH_p", "a metal or hydrogen"), "*": ("star_e", "any atom, as a star")}
LABELS = {label: symbol for symbol, (label, _) in GENERICS.items()}
_RANGE = re.compile(r"(?:[<>]?\d{1,2}|\d{1,2}-\d{1,2})(?:,(?:[<>]?\d{1,2}|\d{1,2}-\d{1,2}))*")


class MarkushError(ValueError):
    """Text that looks like a Markush structure but can't be read as one."""


def split(text):
    """A SMILES with an extension, as (the SMILES, [the extension's fields]). The fields are as written, separated at the top level only:
    commas inside $...$ labels, {...} definitions and the like stay in their field."""
    text = text.strip()
    match = re.match(r"(\S+)\s+\|", text)
    if not match:
        return text, []
    body, depth, in_label, position = [], 0, False, match.end()  # depth: inside {...} or (...), whose commas are their own
    field = ""
    while position < len(text):
        char = text[position]
        position += 1
        if char == "$" and depth == 0:
            in_label = not in_label
        elif char in "{(" and not in_label:
            depth += 1
        elif char in "})" and not in_label:
            depth -= 1
        elif char == "|" and depth == 0 and not in_label:
            if field:
                body.append(field)
            if text[position:].strip():
                raise MarkushError(f"Text after the extension: {text[position:][:30]}")
            return match[1], body
        if char == "," and depth == 0 and not in_label:
            body.append(field)
            field = ""
        else:
            field += char
    raise MarkushError("The extension isn't closed with |.")


def join(smiles, fields):
    fields = [f for f in fields if f]
    return f"{smiles} |{','.join(fields)}|" if fields else smiles


def _fragments(text):
    """{CCC},{N} as ["CCC", "N"]."""
    found = re.findall(r"\{([^{}]*)\}", text)
    if not found or re.sub(r"\{[^{}]*\}", "", text).replace(",", ""):
        raise MarkushError(f"An R-group definition should be fragments in braces: {text[:40]}")
    return found


def read_rgroups(field):
    """RG:_R1={CCC},{N},_R2={O} as {1: ["CCC", "N"], 2: ["O"]}."""
    definitions = {}
    for match in re.finditer(r"_R(\d{1,2})=((?:\{[^{}]*\},?)+)", field[3:]):
        number = int(match[1])
        if not 1 <= number <= MAX_RGROUP:
            raise MarkushError(f"R-groups go from R1 to R{MAX_RGROUP}.")
        definitions[number] = _fragments(match[2].rstrip(","))[:MAX_FRAGMENTS]
    if not definitions:
        raise MarkushError("The R-group definitions can't be read.")
    return definitions


def read_logic(field):
    """LOG={_R1:;;>0._R2:_R1;H;0,1} as {1: {"then": 0, "rest_h": False, "occurrence": ">0"}, 2: {"then": 1, ...}}."""
    inside = re.fullmatch(r"LOG=\{(.*)\}", field)
    if not inside:
        raise MarkushError("The R-group logic can't be read.")
    logic = {}
    for item in inside[1].split("."):
        parts = re.fullmatch(r"_R(\d{1,2}):(?:_R(\d{1,2}))?;(H?);(.*)", item)
        if not parts or (parts[4] and not _RANGE.fullmatch(parts[4])):
            raise MarkushError(f"The R-group logic can't be read: {item[:30]}")
        logic[int(parts[1])] = {"then": int(parts[2] or 0), "rest_h": bool(parts[3]), "occurrence": parts[4]}
    return logic


def write_rgroups(definitions):
    if not definitions:
        return ""
    return "RG:" + ",".join(f"_R{n}=" + ",".join("{" + f + "}" for f in fragments) for n, fragments in sorted(definitions.items()) if fragments)


def write_logic(logic):
    items = [f"_R{n}:{'_R' + str(rule['then']) if rule['then'] else ''};{'H' if rule['rest_h'] else ''};{rule['occurrence']}"
             for n, rule in sorted(logic.items()) if rule["then"] or rule["rest_h"] or rule["occurrence"]]
    return "LOG={" + ".".join(items) + "}" if items else ""


def valid_occurrence(text):
    return not text or bool(_RANGE.fullmatch(text))


def separate(text):
    """(what RDKit reads, R-group definitions, logic) from a SMILES or SMARTS with an extension."""
    smiles, fields = split(text)
    definitions, logic, kept = {}, {}, []
    # RG's own commas, between its fragments and its R-groups, are at the top level: put its pieces back together
    merged = []
    for field in fields:
        if merged and merged[-1].startswith("RG:") and (field.startswith("{") or re.match(r"_R\d{1,2}=\{", field)):
            merged[-1] += "," + field
        else:
            merged.append(field)
    for field in merged:
        if field.startswith("RG:"):
            definitions = read_rgroups(field)
        elif field.startswith("LOG="):
            logic = read_logic(field)
        else:
            kept.append(field)
    return join(smiles, kept), definitions, logic


def fragment_mol(text):
    """An R-group definition as a molecule with 2D coordinates, and the indices of its attachment points: (mol, first, second or None).
    Atom maps 1 and 2 mark them, and are taken off; without them the first atom is the one."""
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(text)
    if mol is None or not mol.GetNumAtoms():
        raise MarkushError(f"An R-group definition isn't valid SMILES: {text[:40]}")
    points = {}
    for atom in mol.GetAtoms():
        if atom.GetAtomMapNum() in (1, 2):
            points[atom.GetAtomMapNum()] = atom.GetIdx()
            atom.SetAtomMapNum(0)
            # written in brackets, as [CH2:1], it seems to be missing a bond, which is the one to the core
            atom.SetNumRadicalElectrons(0)
            atom.SetNoImplicit(False)
            atom.SetNumExplicitHs(0)
    mol.UpdatePropertyCache(strict=False)
    rdDepictor.Compute2DCoords(mol)
    return mol, points.get(1, 0), points.get(2)


def fragment_smiles(mol, first, second=None):
    """An R-group definition as written: from its attachment point, and with atom maps on both if there are two."""
    mol = Chem.Mol(mol)
    if second is not None:
        for number, index in ((1, first), (2, second)):
            atom = mol.GetAtomWithIdx(index)
            hydrogens = atom.GetTotalNumHs()
            atom.SetAtomMapNum(number)
            atom.SetNumExplicitHs(max(hydrogens - 1, 0))  # one fewer, for the bond to the core: [CH2:1], not [CH3:1]
            atom.SetNoImplicit(True)
    return Chem.MolToSmiles(mol, rootedAtAtom=first)


def position_variation(entries):
    """m: from [(index of the star, [indices of the atoms it may be bonded to]), ...], as numbered in the SMILES."""
    if not entries:
        return ""
    return "m:" + ",".join(f"{star}:{'.'.join(map(str, ends))}" for star, ends in entries)


def displayed(mol, definitions):
    """The core with its R-group definitions beside it, for drawing: each definition's attachment point noted as R1 and so on, and laid
    out by RDKit along with the core. Returns the core if there are none."""
    if not definitions:
        return mol
    combined = Chem.RWMol(mol)
    for number, fragments in sorted(definitions.items()):
        for text in fragments:
            fragment, first, _ = fragment_mol(text)
            offset = combined.GetNumAtoms()
            combined.InsertMol(fragment)
            combined.GetAtomWithIdx(offset + first).SetProp("atomNote", f"R{number}")
    result = combined.GetMol()
    rdDepictor.Compute2DCoords(result)
    return result
