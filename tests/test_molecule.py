import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import molecule  # noqa: E402
from app import app  # noqa: E402
from molecule import ParseError, parse  # noqa: E402

# ethanol, in the positional V2000 layout: a blank name line is significant
MOLBLOCK = """{name}
     test           {dim}

  3  2  0  0  0  0  0  0  0  0999 V2000
{a1} C   0  0  0  0  0  0  0  0  0  0  0  0
{a2} C   0  0  0  0  0  0  0  0  0  0  0  0
{a3} O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""
FLAT = dict(a1="    1.0000    2.0000    0.0000", a2="    2.0000    3.0000    0.0000", a3="    3.0000    2.0000    0.0000")
DEEP = dict(a1="    1.0000    2.0000    1.0000", a2="    2.0000    3.0000   -1.0000", a3="    3.0000    2.0000    0.5000")
ZERO = dict(a1="    0.0000    0.0000    0.0000", a2="    0.0000    0.0000    0.0000", a3="    0.0000    0.0000    0.0000")


def block(coords=FLAT, dim="2D", name=""):
    return MOLBLOCK.format(name=name, dim=dim, **coords)


def positions(result):
    return result.mol.GetConformer().GetPositions().tolist()


# SMILES


@pytest.mark.parametrize("text, canonical, formula", [
    ("c1ccccc1O", "Oc1ccccc1", "C6H6O"),
    ("  CCO\n", "CCO", "C2H6O"),
    ("CCO.O", "CCO.O", "C2H8O2"),
    ("[Na+].[Cl-]", "[Cl-].[Na+]", "ClNa"),
    ("C[C@H](N)O", "C[C@H](N)O", "C2H7NO"),
])
def test_smiles(text, canonical, formula):
    result = parse(text).to_dict()
    assert result["format"] == "smiles"
    assert result["smiles"] == canonical
    assert result["formula"] == formula
    assert result["coords"] == "computed"


def test_smiles_gets_a_real_2d_layout():
    points = positions(parse("c1ccccc1O"))
    assert len(points) == 7
    assert all(z == 0 for _, _, z in points)
    assert len({(round(x, 3), round(y, 3)) for x, y, _ in points}) == 7


def test_smiles_title_becomes_the_name():
    assert parse("CCO ethanol").to_dict()["name"] == "ethanol"


def test_explicit_hydrogens_are_removed():
    assert parse("[H]C([H])([H])[H]").mol.GetNumAtoms() == 1


def test_dict_describes_atoms_and_bonds():
    result = parse("C=O").to_dict()
    assert [a["symbol"] for a in result["atoms"]] == ["C", "O"]
    assert result["bonds"] == [{"begin": 0, "end": 1, "order": 2.0}]


# molblock


def test_molblock_keeps_its_2d_layout():
    result = parse(block())
    assert result.format == "molblock"
    assert result.coords == "input"
    assert positions(result)[0][:2] == pytest.approx([1.0, 2.0])
    assert result.to_dict()["smiles"] == "CCO"


def test_molblock_name_and_blank_first_line():
    assert parse(block(name="ethanol")).to_dict()["name"] == "ethanol"
    assert parse(block()).to_dict()["name"] == ""


def test_molblock_with_windows_line_endings():
    assert parse(block().replace("\n", "\r\n")).to_dict()["smiles"] == "CCO"


@pytest.mark.parametrize("coords, dim", [(DEEP, "3D"), (ZERO, "2D")])
def test_molblock_unusable_coordinates_are_replaced(coords, dim):
    result = parse(block(coords, dim))
    assert result.coords == "computed"
    points = positions(result)
    assert all(z == 0 for _, _, z in points)
    assert len({(round(x, 3), round(y, 3)) for x, y, _ in points}) == 3


def test_sdf_reads_the_first_record():
    assert parse(block() + "$$$$\n" + block(name="second") + "$$$$\n").to_dict()["name"] == ""


# rejections


@pytest.mark.parametrize("text, fmt, code", [
    ("", "auto", "empty"),
    ("  \n ", "auto", "empty"),
    (None, "auto", "empty"),
    (42, "auto", "empty"),
    ("CCO", "inchi", "format"),
    ("not a molecule", "auto", "syntax"),
    ("C(C", "auto", "syntax"),
    ("C\0C", "auto", "syntax"),
    ("CCO\nCCN", "auto", "syntax"),
    ("nonsense\nM  END\n", "auto", "syntax"),
    ("CCO", "molblock", "syntax"),
    ("C(C)(C)(C)(C)C", "auto", "chemistry"),
    ("c1cccc1", "auto", "chemistry"),
])
def test_rejected(text, fmt, code):
    with pytest.raises(ParseError) as e:
        parse(text, fmt)
    assert e.value.code == code
    assert e.value.message


def test_chemistry_error_carries_rdkit_reason():
    with pytest.raises(ParseError, match="valence"):
        parse("C(C)(C)(C)(C)C")


def test_valence_error_in_a_molblock():
    pentavalent = """
     test           2D

  6  5  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000    1.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000   -1.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.0000    1.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  1  3  1  0
  1  4  1  0
  1  5  1  0
  1  6  1  0
M  END
"""
    with pytest.raises(ParseError) as e:
        parse(pentavalent)
    assert e.value.code == "chemistry"


def test_molblock_without_atoms():
    empty = "\n     test\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"
    with pytest.raises(ParseError) as e:
        parse(empty)
    assert e.value.code == "empty"


def test_size_limits():
    for text in ["C" * (molecule.MAX_ATOMS + 1), "C" * (molecule.MAX_SMILES_CHARS + 1)]:
        with pytest.raises(ParseError) as e:
            parse(text)
        assert e.value.code == "too_large"
    with pytest.raises(ParseError) as e:
        parse("M  END" + " " * molecule.MAX_MOLBLOCK_CHARS)
    assert e.value.code == "too_large"
    assert parse("C" * molecule.MAX_ATOMS).mol.GetNumAtoms() == molecule.MAX_ATOMS


# API


def post(**kwargs):
    return app.test_client().post("/api/v1/parse", **kwargs)


def test_api_parses():
    r = post(json={"input": "CCO"})
    assert r.status_code == 200
    assert r.get_json()["smiles"] == "CCO"


def test_api_honours_format():
    assert post(json={"input": "CCO", "format": "molblock"}).status_code == 400


def test_api_reports_parse_errors():
    r = post(json={"input": "C(C"})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "syntax"


@pytest.mark.parametrize("kwargs", [
    {"data": "not json", "content_type": "application/json"},
    {"data": "CCO", "content_type": "text/plain"},
    {"json": ["CCO"]},
])
def test_api_rejects_bad_bodies(kwargs):
    r = post(**kwargs)
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "bad_request"


def test_api_missing_input():
    assert post(json={}).get_json()["error"]["code"] == "empty"
