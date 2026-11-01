import base64
import os
import re
import sys
import time
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import graphics  # noqa: E402
import molecule  # noqa: E402
import render  # noqa: E402
from app import app  # noqa: E402

SEQUENCE = re.compile(r"\x1b_G([^;\x1b]*);([^\x1b]*)\x1b\\")


def mol(smiles):
    return molecule.parse(smiles).mol


def image(smiles, cols=80, rows=30, theme="dark"):
    return graphics.draw_image(mol(smiles), cols, rows, theme)


def keys(sequence):
    return dict(pair.split("=") for pair in sequence.split(","))


def rgba(picture):
    return np.asarray(picture.convert("RGBA"))


# the escape sequences


@pytest.mark.parametrize("size", [1, 3000, 3073, 20000])
def test_chunks_follow_the_protocol(size):
    png = bytes(range(256)) * (size // 256 + 1)
    png = png[:size]
    text = graphics.kitty_sequence(png, 12)
    found = SEQUENCE.findall(text)
    assert SEQUENCE.sub("", text) == "\n"

    first = keys(found[0][0])
    assert (first["a"], first["f"], first["c"], first["q"]) == ("T", "100", "12", "2")
    assert "r" not in first  # the terminal keeps the shape only when given a single dimension
    for sequence, _ in found[1:]:
        assert set(keys(sequence)) == {"m", "q"}
    assert [keys(s)["m"] for s, _ in found] == ["1"] * (len(found) - 1) + ["0"]
    for _, payload in found[:-1]:
        assert len(payload) == graphics.CHUNK_SIZE
    assert 0 < len(found[-1][1]) <= graphics.CHUNK_SIZE
    assert base64.b64decode("".join(p for _, p in found)) == png


def test_render_decodes_back_to_the_drawing():
    found = SEQUENCE.findall(graphics.render(mol("CC(=O)Oc1ccccc1C(=O)O")))
    picture = Image.open(BytesIO(base64.b64decode("".join(p for _, p in found))))
    assert picture.format == "PNG"
    assert picture.width == int(keys(found[0][0])["c"]) * graphics.CELL_PIXELS


# the drawing


def test_background_is_transparent_and_ink_is_not():
    alpha = rgba(image("c1ccccc1O")[0])[:, :, 3]
    assert alpha[0, 0] == 0
    assert alpha.max() == 255


def test_width_is_whole_columns():
    for smiles in ("CC", "c1ccccc1O", "CC(=O)Oc1ccccc1C(=O)O"):
        picture, used = image(smiles)
        assert picture.width == used * graphics.CELL_PIXELS


def test_as_compact_as_the_text_modes():
    smiles = "CC(=O)Oc1ccccc1C(=O)O"
    assert abs(image(smiles)[1] - render.render(mol(smiles), mode="octant").to_dict()["cols"]) <= 1


@pytest.mark.parametrize("cols, rows", [(10, 5), (12, 30), (240, 5)])
def test_stays_inside_the_canvas(cols, rows):
    picture, used = image("Cn1cnc2c1c(=O)n(C)c(=O)n2C", cols, rows)
    assert used <= cols
    assert picture.height <= rows * graphics.CELL_ASPECT * graphics.CELL_PIXELS


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
    prepare = graphics.rdMolDraw2D.PrepareMolForDrawing
    monkeypatch.setattr(graphics.rdMolDraw2D, "PrepareMolForDrawing", lambda m, **k: prepare(m, **{**k, "addChiralHs": True}))
    stubbed = rgba(image(cholesterol)[0])
    assert ours.shape != stubbed.shape or (ours != stubbed).any()


def opaque_pixels(picture):
    pixels = rgba(picture)
    return pixels[pixels[:, :, 3] == 255][:, :3]


@pytest.mark.parametrize("theme, ink", [("dark", (0xee, 0xee, 0xee)), ("light", (0x1a, 0x1a, 0x1a))])
def test_bonds_use_the_theme_ink(theme, ink):
    # within 1, because storing the image with alpha can round a channel down
    assert (abs(opaque_pixels(image("CCCC", theme=theme)[0]).astype(int) - ink) <= 1).all()


@pytest.mark.parametrize("theme", graphics.THEMES)
def test_elements_keep_their_colours(theme):
    expected = tuple(round(c * 255) for c in graphics._rgb(graphics._ELEMENTS[theme][31]))
    assert (opaque_pixels(image("CCO", theme=theme)[0]) == expected).all(axis=1).any()


def test_every_coloured_element_has_a_colour_in_each_theme():
    for theme in graphics.THEMES:
        assert set(render.ANSI_COLORS.values()) <= set(graphics._ELEMENTS[theme])


@pytest.mark.parametrize("smiles", ["[Na+].[Cl-]", "O", "[He]"])
def test_atoms_without_bonds_stay_as_text(smiles):
    output = graphics.render(mol(smiles))
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
                   for t in graphics.THEMES)
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


# Sixel and iTerm2


def decode_sixel(sequence):
    """A small independent decoder: return an array of (r, g, b, painted) with percentages for colours."""
    assert sequence.startswith("\x1bP0;1;0q") and sequence.endswith("\x1b\\\n")
    body = sequence[len("\x1bP0;1;0q"):-3]
    width, height = (int(n) for n in re.match(r'"1;1;(\d+);(\d+)', body).groups())
    body = re.sub(r'^"1;1;\d+;\d+', "", body)
    canvas = np.zeros((height, width, 4), dtype=int)
    colors, current, x, top = {}, None, 0, 0
    for token in re.finditer(r"#(\d+);2;(\d+);(\d+);(\d+)|#(\d+)|!(\d+)(.)|([?-~])|([$-])", body):
        if token[1]:
            colors[int(token[1])] = (int(token[2]), int(token[3]), int(token[4]))
        elif token[5]:
            current = colors[int(token[5])]
        elif token[9] == "$":
            x = 0
        elif token[9] == "-":
            x, top = 0, top + 6
        else:
            count, char = (int(token[6]), token[7]) if token[6] else (1, token[8])
            bits = ord(char) - 63
            for i in range(6):
                if bits & (1 << i) and top + i < height:
                    canvas[top + i, x:x + count] = (*current, 1)
            x += count
    return canvas


def sixel_of(smiles, theme="dark", cell_pixels=10, background=(0, 0, 0)):
    picture, _ = graphics.draw_image(mol(smiles), 80, 30, theme, cell_pixels)
    return picture, decode_sixel(graphics.sixel_sequence(picture, background))


def test_sixel_keeps_the_size_and_leaves_the_background_unpainted():
    picture, decoded = sixel_of("CC(=O)Oc1ccccc1C(=O)O")
    assert decoded.shape[:2] == (picture.height, picture.width)
    alpha = rgba(picture)[:, :, 3]
    assert (decoded[:, :, 3] == (alpha >= graphics.SIXEL_MIN_ALPHA)).all()
    assert alpha[0, 0] == 0 and decoded[0, 0, 3] == 0


def test_sixel_colours_match_the_drawing():
    picture, decoded = sixel_of("CCO")
    pixels = rgba(picture)
    solid = pixels[:, :, 3] == 255
    expected = pixels[solid][:, :3].astype(float) * 100 / 255
    assert abs(decoded[solid][:, :3] - expected).max() <= 10


def test_sixel_register_zero_holds_the_background_and_no_ink():
    picture, _ = graphics.draw_image(mol("CCO"), 80, 30, "dark", 10)
    text = graphics.sixel_sequence(picture, (255, 255, 255))
    assert "#0;2;100;100;100" in text
    assert not re.search(r"#0(?!;)", text)  # tmux paints empty pixels with it, so ink must not share it


def test_sixel_edges_blend_into_the_given_background():
    picture, on_black = sixel_of("c1ccccc1O", background=(0, 0, 0))
    _, on_white = sixel_of("c1ccccc1O", background=(255, 255, 255))
    alpha = rgba(picture)[:, :, 3]
    edge = (alpha > graphics.SIXEL_MIN_ALPHA) & (alpha < 200)
    assert edge.any()
    assert on_white[edge][:, :3].mean() > on_black[edge][:, :3].mean() + 10


def test_sixel_uses_the_real_cell_size():
    small, _ = sixel_of("c1ccccc1O", cell_pixels=8)
    large, _ = sixel_of("c1ccccc1O", cell_pixels=16)
    assert large.width > small.width * 1.8


def test_iterm_sequence():
    text = graphics.iterm_sequence(b"\x89PNGdata", 12)
    match = re.fullmatch("\x1b\\]1337;File=([^:]*):([^\x07]*)\x07\n", text)
    settings = dict(pair.split("=") for pair in match[1].split(";"))
    assert settings == {"inline": "1", "size": "8", "width": "12", "preserveAspectRatio": "1"}
    assert base64.b64decode(match[2]) == b"\x89PNGdata"


@pytest.mark.parametrize("mode, start", [("sixel", "\x1bP"), ("iterm", "\x1b]1337;"), ("kitty", "\x1b_G")])
def test_endpoint_offers_each_format(client, mode, start):
    response = client.get("/api/v1/render.txt", query_string={"input": "c1ccccc1O", "mode": mode})
    assert response.status_code == 200 and response.get_data(as_text=True).startswith(start)


@pytest.mark.parametrize("mode", graphics.MODES)
def test_every_format_keeps_unbonded_molecules_as_text(client, mode):
    text = client.get("/api/v1/render.txt", query_string={"input": "[Na+].[Cl-]", "mode": mode}).get_data(as_text=True)
    assert "Na" in text and "\x1bP" not in text and "\x1b_G" not in text and "1337" not in text


def test_endpoint_sixel_options_change_the_output(client):
    args = {"input": "CCO", "mode": "sixel"}
    plain = client.get("/api/v1/render.txt", query_string=args).data
    assert client.get("/api/v1/render.txt", query_string={**args, "cell_width": "16"}).data != plain
    assert client.get("/api/v1/render.txt", query_string={**args, "background": "ffffff"}).data != plain


@pytest.mark.parametrize("args", [{"cell_width": "3"}, {"cell_width": "41"}, {"cell_width": "wide"}, {"cell_width": "٣٠"},
                                  {"background": "white"}, {"background": "12345"}, {"background": "12345g"}])
def test_endpoint_rejects_bad_sixel_options(client, args):
    response = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "sixel", **args})
    assert response.status_code == 400 and b"\x1b" not in response.data


def test_large_molecule_stays_practical_as_sixel():
    big = molecule.parse(".".join(["c1ccccc1"] * 80)).mol
    started = time.monotonic()
    text = graphics.render(big, "sixel", 240, 120)
    assert time.monotonic() - started < 10
    assert len(text) < 5_000_000
