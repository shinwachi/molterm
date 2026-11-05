"""3D coordinates for a structure, for viewers that show molecules in space (molterm-3d): RDKit's ETKDG, then a force field.

A molblock that already has 3D coordinates keeps them. Everything else is embedded with hydrogens added, from a fixed seed, so the same
input always gives the same shape, and tidied with MMFF94 (UFF where MMFF has no parameters for an atom).
"""
from rdkit import Chem, rdBase
from rdkit.Chem import AllChem

import molecule

MAX_HEAVY_ATOMS = 150  # embedding grows quickly with size; a protein comes as a PDB entry instead
SEED = 0xF00D
EMBED_SECONDS = 10


class ConformerError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _has_3d(mol):
    if mol.GetNumConformers() == 0:
        return False
    conformer = mol.GetConformer()
    return conformer.Is3D() and any(abs(conformer.GetAtomPosition(i).z) > 1e-4 for i in range(mol.GetNumAtoms()))


def _given_3d(text):
    """A molblock's own 3D structure, hydrogens and all, or None when it has none."""
    if molecule.detect_format(text) != "molblock":
        return None
    with rdBase.BlockLogs():
        mol = Chem.MolFromMolBlock(text, removeHs=False)
    return mol if mol is not None and _has_3d(mol) else None


def embed(text, fmt="auto"):
    """An SDF record of the structure in 3D, named as the input names it (or by its SMILES). Raises molecule.ParseError for input that
    can't be read, and ConformerError for structures that can't be put in 3D: queries, Markush structures, too many atoms, or ones the
    embedding gives up on."""
    given = _given_3d(text) if isinstance(text, str) else None
    if given is not None:
        mol = given
    else:
        parsed = molecule.parse(text, fmt)
        if parsed.format == "smarts":
            raise ConformerError("query", "A query has no single shape to show in 3D.")
        if parsed.rgroups:
            raise ConformerError("markush", "A Markush structure has no single shape to show in 3D: give one of its members.")
        mol = Chem.Mol(parsed.core)
        if any(atom.GetAtomicNum() == 0 for atom in mol.GetAtoms()):
            raise ConformerError("markush", "Attachment points and R groups have no place in 3D: give a whole molecule.")
        if mol.GetNumHeavyAtoms() > MAX_HEAVY_ATOMS:
            raise ConformerError("too_large", f"The molecule has {mol.GetNumHeavyAtoms():,} heavy atoms; 3D takes at most {MAX_HEAVY_ATOMS}.")
        name = mol.GetProp("_Name") if mol.HasProp("_Name") else ""
        mol.RemoveAllConformers()
        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = SEED
        params.timeout = EMBED_SECONDS
        with rdBase.BlockLogs():
            if AllChem.EmbedMolecule(mol, params) != 0:
                params.useRandomCoords = True
                if AllChem.EmbedMolecule(mol, params) != 0:
                    raise ConformerError("embedding", "RDKit couldn't find a shape for this structure in 3D.")
            if AllChem.MMFFHasAllMoleculeParams(mol):
                AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
            elif AllChem.UFFHasAllMoleculeParams(mol):
                AllChem.UFFOptimizeMolecule(mol, maxIters=500)
        mol.SetProp("_Name", name or Chem.MolToSmiles(Chem.RemoveHs(mol)))
    return Chem.MolToMolBlock(mol) + "$$$$\n"
