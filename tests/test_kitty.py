import base64
import os
import re
import sys
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import kitty  # noqa: E402
import molecule  # noqa: E402
import render  # noqa: E402
from app import app  # noqa: E402

SEQUENCE = re.compile(r"\x1b_G([^;\x1b]*);([^\x1b]*)\x1b\\")


def mol(smiles):
    return molecule.parse(smiles).mol


def image(smiles, cols=80, rows=30, theme="dark"):
    png, used = kitty.draw_png(mol(smiles), cols, rows, theme)
    return Image.open(BytesIO(png)), used


def keys(sequence):
    return dict(pair.split("=") for pair in sequence.split(","))


def rgba(picture):
    return np.asarray(picture.convert("RGBA"))


# the escape sequences


@pytest.mark.parametrize("size", [1, 3000, 3073, 20000])
def test_chunks_follow_the_protocol(size):
    png = bytes(range(256)) * (size // 256 + 1)
    png = png[:size]
    text = kitty.encode(png, 12)
    found = SEQUENCE.findall(text)
    assert SEQUENCE.sub("", text) == "\n"

    first = keys(found[0][0])
    assert (first["a"], first["f"], first["c"], first["q"]) == ("T", "100", "12", "2")
    assert "r" not in first  # the terminal keeps the shape only when given a single dimension
    for sequence, _ in found[1:]:
        assert set(keys(sequence)) == {"m", "q"}
    assert [keys(s)["m"] for s, _ in found] == ["1"] * (len(found) - 1) + ["0"]
    for _, payload in found[:-1]:
        assert len(payload) == kitty.CHUNK_SIZE
    assert 0 < len(found[-1][1]) <= kitty.CHUNK_SIZE
    assert base64.b64decode("".join(p for _, p in found)) == png


def test_render_decodes_back_to_the_drawing():
    found = SEQUENCE.findall(kitty.render(mol("CC(=O)Oc1ccccc1C(=O)O")))
    picture = Image.open(BytesIO(base64.b64decode("".join(p for _, p in found))))
    assert picture.format == "PNG"
    assert picture.width == int(keys(found[0][0])["c"]) * kitty.CELL_PIXELS


# the drawing


def test_background_is_transparent_and_ink_is_not():
    alpha = rgba(image("c1ccccc1O")[0])[:, :, 3]
    assert alpha[0, 0] == 0
    assert alpha.max() == 255


def test_width_is_whole_columns():
    for smiles in ("CC", "c1ccccc1O", "CC(=O)Oc1ccccc1C(=O)O"):
        picture, used = image(smiles)
        assert picture.width == used * kitty.CELL_PIXELS


def test_as_compact_as_the_text_modes():
    smiles = "CC(=O)Oc1ccccc1C(=O)O"
    assert abs(image(smiles)[1] - render.render(mol(smiles), mode="octant").to_dict()["cols"]) <= 1


@pytest.mark.parametrize("cols, rows", [(10, 5), (12, 30), (240, 5)])
def test_stays_inside_the_canvas(cols, rows):
    picture, used = image("Cn1cnc2c1c(=O)n(C)c(=O)n2C", cols, rows)
    assert used <= cols
    assert picture.height <= rows * kitty.CELL_ASPECT * kitty.CELL_PIXELS


def test_a_larger_canvas_does_not_enlarge_the_drawing():
    assert image("c1ccccc1O", 40, 20)[0].size == image("c1ccccc1O", 200, 100)[0].size


def test_double_bonds_show_as_two_lines():
    # what the text modes cannot do at this size
    def strokes(smiles):
        picture = image(smiles)[0]
        alpha = rgba(picture)[:, :, 3] > 128
        ink_columns = np.nonzero(alpha.any(axis=0))[0]
        middle = alpha[:, (ink_columns[0] + ink_columns[-1]) // 2]
        return int(middle[0]) + int(np.count_nonzero(middle[1:] & ~middle[:-1]))

    assert strokes("CC") == 1
    assert strokes("C=C") == 2


def test_stereo_wedges_are_drawn():
    plain, wedged = rgba(image("CC(N)C(=O)O")[0]), rgba(image("C[C@H](N)C(=O)O")[0])
    assert plain.shape == wedged.shape  # stereo does not move the atoms, so only the wedge can differ
    assert (plain != wedged).any()


def test_no_hydrogen_stub_at_ring_fusions(monkeypatch):
    # RDKit's default preparation adds these hydrogens, unlabelled, so the drawing must differ from what it would give
    cholesterol = "C[C@H](CCCC(C)C)[C@H]1CC[C@@H]2[C@@]1(CC[C@H]3[C@H]2CC=C4[C@@]3(CC[C@@H](C4)O)C)C"
    ours = rgba(image(cholesterol)[0])
    prepare = kitty.rdMolDraw2D.PrepareMolForDrawing
    monkeypatch.setattr(kitty.rdMolDraw2D, "PrepareMolForDrawing", lambda m, **k: prepare(m, **{**k, "addChiralHs": True}))
    stubbed = rgba(image(cholesterol)[0])
    assert ours.shape != stubbed.shape or (ours != stubbed).any()


def opaque_pixels(picture):
    pixels = rgba(picture)
    return pixels[pixels[:, :, 3] == 255][:, :3]


@pytest.mark.parametrize("theme, ink", [("dark", (0xee, 0xee, 0xee)), ("light", (0x1a, 0x1a, 0x1a))])
def test_bonds_use_the_theme_ink(theme, ink):
    # within 1, because storing the image with alpha can round a channel down
    assert (abs(opaque_pixels(image("CCCC", theme=theme)[0]).astype(int) - ink) <= 1).all()


@pytest.mark.parametrize("theme", kitty.THEMES)
def test_elements_keep_their_colours(theme):
    expected = tuple(round(c * 255) for c in kitty._rgb(kitty._ELEMENTS[theme][31]))
    assert (opaque_pixels(image("CCO", theme=theme)[0]) == expected).all(axis=1).any()


def test_every_coloured_element_has_a_colour_in_each_theme():
    for theme in kitty.THEMES:
        assert set(render.ANSI_COLORS.values()) <= set(kitty._ELEMENTS[theme])


@pytest.mark.parametrize("smiles", ["[Na+].[Cl-]", "O", "[He]"])
def test_atoms_without_bonds_stay_as_text(smiles):
    output = kitty.render(mol(smiles))
    assert "\x1b_G" not in output
    assert output == render.render(mol(smiles)).ansi + "\n"


# the endpoint


@pytest.fixture
def client():
    return app.test_client()


def test_endpoint_returns_an_image(client):
    response = client.get("/api/v1/render.txt", query_string={"input": "c1ccccc1O", "mode": "kitty"})
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert text.startswith("\x1b_G") and text.endswith("\x1b\\\n")


def test_endpoint_themes_differ(client):
    dark, light = (client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "theme": t}).data
                   for t in kitty.THEMES)
    assert dark != light


def test_endpoint_rejects_an_unknown_theme(client):
    response = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "theme": "blue"})
    assert response.status_code == 400
    assert b"theme must be" in response.data


def test_endpoint_reports_bad_input_as_text(client):
    response = client.get("/api/v1/render.txt", query_string={"input": "not a molecule(", "mode": "kitty"})
    assert response.status_code == 400
    assert b"\x1b_G" not in response.data


def test_json_endpoint_does_not_offer_kitty(client):
    response = client.post("/api/v1/render", json={"input": "CCO", "mode": "kitty"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "bad_request"


def test_text_modes_are_unchanged_by_a_theme(client):
    args = {"input": "CCO", "mode": "octant"}
    assert client.get("/api/v1/render.txt", query_string=args).data == \
        client.get("/api/v1/render.txt", query_string={**args, "theme": "light"}).data
