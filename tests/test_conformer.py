"""3D coordinates for molterm-3d: /api/v1/conformer.sdf."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from rdkit import Chem  # noqa: E402

import conformer  # noqa: E402
from app import app  # noqa: E402


def read(sdf):
    return Chem.MolFromMolBlock(sdf, removeHs=False)


def test_a_smiles_comes_back_in_3d_with_its_hydrogens():
    sdf = conformer.embed("CC(=O)Oc1ccccc1C(=O)O")
    mol = read(sdf)
    assert sdf.endswith("$$$$\n") and mol.GetNumAtoms() == 21  # 13 heavy atoms and 8 hydrogens
    assert mol.GetConformer().Is3D() and any(abs(z) > 0.1 for *_, z in mol.GetConformer().GetPositions())
    assert mol.GetProp("_Name") == "CC(=O)Oc1ccccc1C(=O)O"


def test_the_same_input_gives_the_same_shape():
    assert conformer.embed("CCN(CC)CC") == conformer.embed("CCN(CC)CC")


def test_a_molblock_in_3d_keeps_its_coordinates():
    given = conformer.embed("c1ccccc1O")
    assert conformer.embed(given) == given


def test_queries_markush_and_large_molecules_are_refused():
    for text, code in (("[#6;R]~[#7]", "query"), ("c1ccccc1[*:1] |$;;;;;;_R1$|", "markush"), ("C" * 160, "too_large")):
        try:
            conformer.embed(text)
        except conformer.ConformerError as e:
            assert e.code == code, text
        else:
            raise AssertionError(text)


def test_the_endpoint():
    client = app.test_client()
    answer = client.get("/api/v1/conformer.sdf", query_string={"input": "CCO"})
    assert answer.status_code == 200 and answer.mimetype == "chemical/x-mdl-sdfile" and read(answer.get_data(as_text=True)).GetNumAtoms() == 9
    assert client.post("/api/v1/conformer.sdf", data={"input": "CCO"}).status_code == 200
    bad = client.get("/api/v1/conformer.sdf", query_string={"input": "C1CC"})
    assert bad.status_code == 400 and bad.mimetype == "text/plain"
