"""The container provides what the renderer needs: a venv, RDKit and its raster drawer."""
import os
import sys

import rdkit
from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from app import app  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_runs_in_the_venv():
    assert sys.prefix == "/opt/venv"
    assert sys.prefix != sys.base_prefix


def test_rdkit_parses_smiles():
    mol = Chem.MolFromSmiles("c1ccccc1O")
    assert mol.GetNumAtoms() == 7
    assert Chem.MolToSmiles(mol) == "Oc1ccccc1"


def test_rdkit_rejects_bad_smiles():
    assert Chem.MolFromSmiles("not a molecule") is None


def test_rdkit_draws_png():
    drawer = rdMolDraw2D.MolDraw2DCairo(200, 200)
    drawer.DrawMolecule(Chem.MolFromSmiles("c1ccccc1O"))
    drawer.FinishDrawing()
    assert drawer.GetDrawingText().startswith(PNG_MAGIC)


def test_health_reports_rdkit():
    assert app.test_client().get("/health").get_json()["rdkit"] == rdkit.__version__
