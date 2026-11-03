import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import braille  # noqa: E402
import molecule  # noqa: E402
import octant  # noqa: E402
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


DRUGS = ["c1ccccc1O", "CC(=O)Oc1ccccc1C(=O)O", "Cn1cnc2c1c(=O)n(C)c(=O)n2C", "C[C@H](N)C(=O)O", "CCCCCC",
         "CC(C)Cc1ccc(cc1)C(C)C(=O)O", "CC(=O)Nc1ccc(O)cc1", "CC1(C)S[C@@H]2[C@H](NC(=O)Cc3ccccc3)C(=O)N2[C@H]1C(=O)O",
         "C[N+](=O)[O-]", "O=C(O)c1ccccc1Cl", "CN1CCC[C@H]1c1cccnc1", "C/C=C/C", "N#Cc1ccccc1"]


@pytest.mark.parametrize("smiles", DRUGS)
def test_every_bond_leaves_ink_at_the_smallest_size(smiles):
    mol = molecule.parse(smiles).mol
    bitmap, _, positions = render._rasterize(mol, 80, 30)
    for bond in mol.GetBonds():
        (x1, y1), (x2, y2) = positions[bond.GetBeginAtomIdx()], positions[bond.GetEndAtomIdx()]
        col, row = (x1 + x2) / 2 * braille.CELL_WIDTH, (y1 + y2) / 2 * braille.CELL_HEIGHT
        near = bitmap[max(int(row) - 1, 0):int(row) + 2, max(int(col) - 1, 0):int(col) + 2]
        assert near.any(), f"bond {bond.GetIdx()} of {smiles} left no dots"


@pytest.mark.parametrize("smiles", DRUGS)
def test_no_label_is_overwritten_at_the_smallest_size(smiles):
    result = draw(smiles)
    assert len(result.labels) == sum(render._label_text(a) is not None for a in molecule.parse(smiles).mol.GetAtoms())
    for label in result.labels:
        row = "".join(result.grid[label["row"]])
        assert row[label["col"]:label["col"] + len(label["text"])] == label["text"]


def test_size_is_compact_and_independent_of_the_canvas():
    small, large = draw("c1ccccc1O", 80, 30), draw("c1ccccc1O", 240, 120)
    assert (len(small.grid[0]), len(small.grid)) == (len(large.grid[0]), len(large.grid))
    assert len(small.grid[0]) <= 12 and len(small.grid) <= 4


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


# octants


def octant_mask(char):
    return octant.CHARS.index(char)


def test_octant_table_covers_every_pattern_once():
    assert len(octant.CHARS) == 256
    assert len(set(octant.CHARS)) == 256


@pytest.mark.parametrize("mask, code", [
    (0x04, 0x1CD00),  # BLOCK OCTANT-3, the first in the block
    (0x06, 0x1CD01),  # BLOCK OCTANT-23
    (0x07, 0x1CD02),  # BLOCK OCTANT-123
    (0x08, 0x1CD03),  # BLOCK OCTANT-4
    (0xFE, 0x1CDE5),  # BLOCK OCTANT-2345678, the last in the block
])
def test_octant_table_anchors(mask, code):
    assert octant.CHARS[mask] == chr(code)


@pytest.mark.parametrize("rows, cols, char", [
    ((0, 1), (0, 1), "▀"), ((2, 3), (0, 1), "▄"), ((0, 1, 2, 3), (0,), "▌"), ((0, 1, 2, 3), (1,), "▐"),
    ((0, 1), (0,), "▘"), ((0, 1), (1,), "▝"), ((2, 3), (0,), "▖"), ((2, 3), (1,), "▗"),
    ((3,), (0, 1), "▂"), ((0, 1, 2, 3), (0, 1), "█"),
])
def test_octant_shapes_use_the_existing_block_characters(rows, cols, char):
    bitmap = np.zeros((4, 2), dtype=bool)
    bitmap[np.ix_(rows, cols)] = True
    assert octant.bitmap_to_grid(bitmap) == [[char]]


def test_octant_empty_cell_is_a_space():
    assert octant.bitmap_to_grid(np.zeros((4, 2))) == [[" "]]


@pytest.mark.parametrize("smiles", DRUGS)
def test_octant_draws_the_same_dots_as_braille(smiles):
    mol = molecule.parse(smiles).mol
    bitmap, _, _ = render._rasterize(mol, 80, 30)
    for row, chars in zip(range(0, 120, 4), octant.bitmap_to_grid(bitmap)):
        for col, char in zip(range(0, 160, 2), chars):
            mask = octant_mask(char)
            for bit, (y, x) in enumerate((y, x) for y in range(4) for x in range(2)):
                assert bool(mask >> bit & 1) == bitmap[row + y, col + x]


def test_octant_rendering_matches_braille_in_size_and_labels():
    a, b = draw("CC(=O)Oc1ccccc1C(=O)O"), render.render(molecule.parse("CC(=O)Oc1ccccc1C(=O)O").mol, 80, 30, "octant")
    assert (len(a.grid), len(a.grid[0])) == (len(b.grid), len(b.grid[0]))
    assert a.labels == b.labels
    assert b.mode == "octant"
    assert not any(0x2801 <= ord(c) <= 0x28FF for row in b.grid for c in row)
    assert any(0x1CD00 <= ord(c) <= 0x1CDE5 for row in b.grid for c in row)


def test_octant_ions_use_plain_spaces():
    assert render.render(molecule.parse("[Na+].[Cl-]").mol, 40, 10, "octant").text == "Na⁺ Cl⁻"


# colour


def test_colors_match_the_grid():
    result = draw("CC(=O)Nc1ccc(O)cc1")
    assert [len(row) for row in result.colors] == [len(row) for row in result.grid]


def test_plain_carbon_has_no_colour():
    assert {code for row in draw("c1ccccc1").colors for code in row} == {None}


@pytest.mark.parametrize("smiles, code", [("CCO", 31), ("CCN", 34), ("CCS", 33), ("CCCl", 32), ("CCP", 35), ("CCF", 32)])
def test_bonds_take_their_elements_colour(smiles, code):
    result = draw(smiles)
    label = {(result.labels[0]["row"], result.labels[0]["col"] + i) for i in range(len(result.labels[0]["text"]))}
    coloured = {(r, c) for r, row in enumerate(result.colors) for c, k in enumerate(row) if k == code}
    assert coloured - label, "no bond cell carries the colour"


def test_blank_cells_have_no_colour():
    result = draw("CCO")
    assert all(k is None for gr, cr in zip(result.grid, result.colors) for g, k in zip(gr, cr) if g == BLANK)


def test_ansi_starts_and_ends_a_colour_around_a_bond():
    assert ANSI.sub("", draw("CCO").ansi) == draw("CCO").text
    assert all(line.endswith("\x1b[0m") or "\x1b[" not in line for line in draw("CCO").ansi.split("\n"))


# mode in the API


@pytest.mark.parametrize("mode", ["fancy", ["octant"], 1, None])
def test_api_rejects_bad_modes(mode):
    r = post(input="CCO", mode=mode)
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "bad_request"


def test_api_octant_mode():
    data = post(input="c1ccccc1O", mode="octant").get_json()["render"]
    assert data["mode"] == "octant"
    assert any(0x1CD00 <= ord(c) <= 0x1CDE5 for c in data["text"])


def test_text_endpoint_octant_mode():
    assert any(0x1CD00 <= ord(c) <= 0x1CDE5 for c in text_get(input="c1ccccc1O", mode="octant").get_data(as_text=True))
    assert text_get(input="CCO", mode="fancy").status_code == 400


# for programs that draw molecules themselves, such as the VisiData plugin


def test_render_json_adds_properties_when_asked():
    plain = app.test_client().post("/api/v1/render", json={"input": "CC(=O)Oc1ccccc1C(=O)O"}).get_json()
    assert "properties" not in plain
    asked = app.test_client().post("/api/v1/render", json={"input": "CC(=O)Oc1ccccc1C(=O)O", "properties": True}).get_json()
    props = asked["properties"]
    assert round(props["mw"], 1) == 180.2 and props["hbd"] == 1 and props["ro5"] == 0
    assert {"logp", "tpsa", "hba", "rotb", "qed", "pains", "brenk"} <= set(props)


def test_render_png_is_a_transparent_picture_within_the_cells():
    from io import BytesIO
    from PIL import Image
    response = app.test_client().get("/api/v1/render.png", query_string={"input": "c1ccccc1O", "cols": 20, "rows": 6, "cell_width": 10})
    assert response.status_code == 200 and response.mimetype == "image/png"
    image = Image.open(BytesIO(response.data))
    used = int(response.headers["X-Molterm-Cols"])
    assert image.mode == "RGBA" and image.width == used * 10 <= 200 and 0 < image.height <= 6 * 2 * 10
    assert image.getpixel((0, 0))[3] == 0
    bad = app.test_client().get("/api/v1/render.png", query_string={"input": "C1CC", "cols": 20, "rows": 6})
    assert bad.status_code == 400
    assert app.test_client().get("/api/v1/render.png", query_string={"input": "CCO", "theme": "neon"}).status_code == 400


def test_properties_for_many_structures_at_once_keep_their_order():
    client = app.test_client()
    response = client.post("/api/v1/properties", json={"inputs": ["CCO", "C1CC", "c1ccccc1"]})
    results = response.get_json()["results"]
    assert response.status_code == 200 and len(results) == 3
    assert results[0]["formula"] == "C2H6O" and results[0]["properties"]["hbd"] == 1
    assert results[1]["error"]["code"] and "properties" not in results[1]
    assert results[2]["formula"] == "C6H6"
    assert client.post("/api/v1/properties", json={"inputs": "CCO"}).status_code == 400
    assert client.post("/api/v1/properties", json={"inputs": ["C"] * 1001}).status_code == 400
