import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import braille  # noqa: E402
import molecule  # noqa: E402
import render  # noqa: E402
from app import app  # noqa: E402

BLANK = "⠀"
ANSI = re.compile(r"\x1b\[\d+m")


def block(atoms):
    """A V2000 molblock for a chain of (symbol, x, y) atoms joined by single bonds."""
    lines = ["", "     test           2D", "", f"{len(atoms):3}{len(atoms) - 1:3}  0  0  0  0  0  0  0  0999 V2000"]
    lines += [f"{x:10.4f}{y:10.4f}    0.0000 {symbol:<3} 0  0  0  0  0  0  0  0  0  0  0  0" for symbol, x, y in atoms]
    lines += [f"{i:3}{i + 1:3}  1  0" for i in range(1, len(atoms))]
    return "\n".join(lines + ["M  END", ""])


def draw(smiles, cols=80, rows=30):
    return render.render(molecule.parse(smiles).mol, cols, rows)


def texts(result):
    return sorted(label["text"] for label in result.labels)


# the dot mapping


@pytest.mark.parametrize("row, col, char", [
    (0, 0, "⠁"), (1, 0, "⠂"), (2, 0, "⠄"), (3, 0, "⡀"),
    (0, 1, "⠈"), (1, 1, "⠐"), (2, 1, "⠠"), (3, 1, "⢀"),
])
def test_each_dot_maps_to_its_unicode_bit(row, col, char):
    bitmap = np.zeros((4, 2), dtype=bool)
    bitmap[row, col] = True
    assert braille.bitmap_to_grid(bitmap) == [[char]]


def test_empty_and_full_cells():
    assert braille.bitmap_to_grid(np.zeros((4, 2))) == [[BLANK]]
    assert braille.bitmap_to_grid(np.ones((4, 2))) == [["⣿"]]


def test_cells_are_read_in_position():
    bitmap = np.zeros((8, 4), dtype=bool)
    bitmap[0, 0] = True  # top left cell
    bitmap[7, 3] = True  # bottom right cell
    assert braille.bitmap_to_grid(bitmap) == [["⠁", BLANK], [BLANK, "⢀"]]


def test_partial_cells_are_padded():
    assert braille.bitmap_to_grid(np.ones((5, 3))) == [["⣿", "⡇"], ["⠉", "⠁"]]


# rendering


@pytest.mark.parametrize("smiles", ["CC", "C=C", "C#N", "c1ccccc1O", "CC(=O)Oc1ccccc1C(=O)O", "C[C@H](N)C(=O)O",
                                    "[Na+].CC(=O)[O-]", "[Na+].[Cl-]", "C", "O", "[NH4+]", "[13CH4]", "C" * 500])
@pytest.mark.parametrize("cols, rows", [(10, 5), (80, 30), (240, 120)])
def test_output_is_rectangular_and_fits(smiles, cols, rows):
    result = draw(smiles, cols, rows)
    assert 1 <= len(result.grid) <= rows
    assert {len(row) for row in result.grid} == {len(result.grid[0])}
    assert len(result.grid[0]) <= cols


@pytest.mark.parametrize("smiles", ["CC(=O)Oc1ccccc1C(=O)O", "C[N+](=O)[O-]", "[Na+].[Cl-]"])
def test_labels_point_at_their_text(smiles):
    result = draw(smiles)
    assert result.labels
    for label in result.labels:
        row = "".join(result.grid[label["row"]])
        assert row[label["col"]:label["col"] + len(label["text"])] == label["text"]


def test_bonds_are_braille_and_only_labels_are_not():
    result = draw("c1ccccc1O")
    other = [c for row in result.grid for c in row if not 0x2800 <= ord(c) <= 0x28FF]
    assert sorted(other) == sorted("HO")
    assert any(c != BLANK and 0x2800 <= ord(c) <= 0x28FF for row in result.grid for c in row)


def test_blank_margins_are_trimmed():
    grid = draw("c1ccccc1").grid
    assert any(c != BLANK for c in grid[0]) and any(c != BLANK for c in grid[-1])
    assert any(row[0] != BLANK for row in grid) and any(row[-1] != BLANK for row in grid)


@pytest.mark.parametrize("smiles, expected", [
    ("c1ccccc1", []),                         # skeletal carbons stay unlabelled
    ("c1ccccc1Cl", ["Cl"]),
    ("C[N+](=O)[O-]", ["N⁺", "O", "O⁻"]),
    ("C[C@H](N)C(=O)O", ["HO", "NH₂", "O"]),  # no extra hydrogen stub at the stereocentre
])
def test_labels(smiles, expected):
    assert texts(draw(smiles)) == expected


def test_stereocentre_keeps_the_atom_count_of_the_molecule():
    assert len(draw("C[C@H](N)C(=O)O").labels) == 3


def methanol(oxygen_x, carbon_x):
    return molecule.parse(block([("O", oxygen_x, 0.0), ("C", carbon_x, 0.0)]))


def test_hydrogens_sit_away_from_the_bond():
    assert texts(render.render(methanol(oxygen_x=0.0, carbon_x=1.5).mol, 40, 10)) == ["HO"]
    assert texts(render.render(methanol(oxygen_x=1.5, carbon_x=0.0).mol, 40, 10)) == ["OH"]


@pytest.mark.parametrize("smiles, text", [
    ("C", "CH₄"), ("O", "H₂O"), ("[SH2]", "H₂S"), ("Cl", "HCl"), ("N", "NH₃"), ("[He]", "He"), ("[NH4+]", "NH₄⁺"),
    ("[O-2]", "O²⁻"), ("[13CH4]", "¹³CH₄"),
])
def test_single_atoms_render_as_text(smiles, text):
    result = draw(smiles)
    assert result.text == text
    assert result.bond_dots is None
    assert result.to_dict()["cramped"] is False


def test_ions_are_set_side_by_side():
    assert draw("[Na+].[Cl-]").text == f"Na⁺{BLANK}Cl⁻"


def test_many_ions_wrap_and_stop_at_the_canvas():
    result = draw(".".join(["[Na+]"] * 100), 10, 5)
    assert len(result.grid) == 5
    assert {len(row) for row in result.grid} == {len(result.grid[0])}


def test_ansi_only_adds_colour():
    result = draw("CC(=O)O")
    assert "\x1b[31m" in result.ansi
    assert ANSI.sub("", result.ansi) == result.text


def test_ansi_colours_a_whole_label():
    assert draw("[Cl-]").ansi == "\x1b[32mCl⁻\x1b[0m"


def test_small_molecules_keep_their_scale_on_a_big_canvas():
    assert draw("c1ccccc1", 240, 120).bond_dots == pytest.approx(render.MAX_BOND_DOTS, abs=2)


def test_crowded_canvas_is_flagged():
    assert draw("CC(=O)Oc1ccccc1C(=O)O").to_dict()["cramped"] is False
    assert draw("C" * 200, 40, 12).to_dict()["cramped"] is True


def test_rendering_is_repeatable():
    assert draw("Cn1cnc2c1c(=O)n(C)c(=O)n2C").text == draw("Cn1cnc2c1c(=O)n(C)c(=O)n2C").text


def test_molblock_layout_is_used():
    across = render.render(molecule.parse(block([("C", 0, 0), ("C", 1.5, 0), ("C", 3, 0)])).mol, 60, 30)
    down = render.render(molecule.parse(block([("C", 0, 0), ("C", 0, 1.5), ("C", 0, 3)])).mol, 60, 30)
    assert len(across.grid[0]) > 2 * len(across.grid)
    assert len(down.grid) > len(down.grid[0])


@pytest.mark.parametrize("scale", [0.5, 1.5, 10])
def test_bond_size_does_not_depend_on_the_molblocks_units(scale):
    atoms = [("C", 0, 0), ("C", scale, 0), ("C", 2 * scale, 0)]
    assert render.render(molecule.parse(block(atoms)).mol, 240, 120).bond_dots == pytest.approx(render.MAX_BOND_DOTS, abs=1)


# API


def post(**body):
    return app.test_client().post("/api/v1/render", json=body)


def test_api_returns_molecule_and_rendering():
    data = post(input="c1ccccc1O", cols=40, rows=14).get_json()
    assert data["formula"] == "C6H6O"
    assert data["render"]["cols"] <= 40 and data["render"]["rows"] <= 14
    assert data["render"]["text"].count("\n") == data["render"]["rows"] - 1


def test_api_accepts_sizes_as_numeric_strings():
    assert post(input="CCO", cols="40", rows="14").status_code == 200


@pytest.mark.parametrize("size", [{"cols": 9}, {"cols": 241}, {"rows": 4}, {"rows": 121}, {"cols": "wide"},
                                  {"cols": None}, {"cols": True}, {"rows": 20.5}, {"rows": [20]}])
def test_api_rejects_bad_sizes(size):
    r = post(input="CCO", **size)
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "bad_request"


def test_api_reports_parse_errors():
    assert post(input="C(C").get_json()["error"]["code"] == "syntax"


def test_api_rejects_non_objects():
    assert app.test_client().post("/api/v1/render", json=["CCO"]).status_code == 400


def text_get(**query):
    return app.test_client().get("/api/v1/render.txt", query_string=query)


def test_text_endpoint():
    r = text_get(input="[Cl-]")
    assert r.status_code == 200
    assert r.content_type.startswith("text/plain")
    assert r.get_data(as_text=True) == "Cl⁻\n"


def test_text_endpoint_colour_is_opt_in():
    assert "\x1b[" in text_get(input="[Cl-]", color=1).get_data(as_text=True)


@pytest.mark.parametrize("query", [{"input": "C(C"}, {}, {"input": "CCO", "cols": "wide"}])
def test_text_endpoint_errors_are_plain(query):
    r = text_get(**query)
    assert r.status_code == 400
    assert r.content_type.startswith("text/plain")
    assert not r.get_data(as_text=True).startswith("{")


def test_page_offers_the_renderer():
    html = app.test_client().get("/").get_data(as_text=True)
    assert 'id="term"' in html and 'id="theme"' in html
