"""The automated black-on-white demo, its ASCII mode and its theme."""
import base64
import os
import re
import sys
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import annotate  # noqa: E402
import asciiart  # noqa: E402
import board  # noqa: E402
import editor  # noqa: E402
import editor_icons  # noqa: E402
import graphics  # noqa: E402
import molecule  # noqa: E402
import render  # noqa: E402
import showcase  # noqa: E402
from app import app  # noqa: E402

SIZE = dict(cols=120, rows=38, cell_width=7, cell_height=13)
KITTY = re.compile("\x1b_G([^;\x1b]*);([^\x1b]*)\x1b\\\\")


@pytest.fixture(scope="module")
def frames():
    return showcase.frames(**SIZE)


@pytest.fixture
def client():
    return app.test_client()


def mol(smiles):
    return molecule.parse(smiles, "smiles").mol


def test_the_demo_shows_an_image_beside_ascii(frames):
    first = frames[0]["output"]
    assert "a=T,f=100" in first and "ASCII" in first and "Kitty graphics" in first and "Caffeine" in first


def test_the_edit_gives_what_the_demo_says_and_is_handed_back(frames):
    assert molecule.parse(showcase.EDITED, "smiles").to_dict()["formula"] == "C10H12FNO2"
    _, output = showcase._edit(**SIZE)
    line = output.split("\n")[1]
    written, name = board._split_line(line)
    smiles, notes = annotate.split(written)
    assert smiles == showcase.EDITED and name == "Paracetamol"
    assert [entry[1:] for entry in notes["atoms"]] == [["", "ethyl ether"], ["yellow", ""]]
    assert frames[-1]["output"].index("\x1b[2J") < frames[-1]["output"].index("as printed on quitting")


def test_the_edit_opens_a_window_and_closes_it(frames):
    assert any("Edit Paracetamol" in frame["output"] for frame in frames)
    assert any("edited" in frame["output"] for frame in frames)


def test_the_window_is_resized_moved_minimized_and_brought_back():
    run = showcase._Session(SIZE["cols"], SIZE["rows"], SIZE["cell_width"], SIZE["cell_height"])
    run.click(run.tile(1), "")
    top, left, height, width = run.board()["editing"]["box"]
    corner = run.grip()
    run.drag(corner, (corner[0] - 26, corner[1] - 7), "")
    assert run.board()["editing"]["box"] == [top, left, height - 7, width - 26]
    bar = run.title_bar()
    run.drag(bar, (bar[0] + 24, bar[1] + 6), "")
    assert run.board()["editing"]["box"] == [top + 6, left + 24, height - 7, width - 26]
    run.click(run.control("minimize"), "")
    assert run.board()["editing"]["minimized"] and "□ Edit Paracetamol" in run.frames[-1]["output"]
    run.click(run.chip(), "")
    assert not run.board()["editing"]["minimized"]
    assert run.board()["editing"]["box"] == [top + 6, left + 24, height - 7, width - 26]


def test_the_demo_shows_the_window_being_handled(frames):
    captions = " ".join(frame["caption"] for frame in frames)
    assert "resizes it" in captions and "title bar moves it" in captions and "Minimized" in captions


def test_the_status_line_follows_each_step_of_the_edit(frames):
    outputs = "".join(frame["output"] for frame in frames)
    assert "CCOc1ccc(NC(C)=O)cc1" in outputs and showcase.EDITED in outputs


def test_pointers_stay_in_the_window_and_every_frame_has_a_caption(frames):
    for frame in frames:
        assert frame["caption"] and frame["pause"] > 0
        if frame["pointer"]:
            assert 0 <= frame["pointer"][0] < SIZE["cols"] and 0 <= frame["pointer"][1] < SIZE["rows"]


@pytest.mark.parametrize("size", [dict(cols=90, rows=30, cell_width=7, cell_height=13), dict(cols=160, rows=38, cell_width=7, cell_height=15)])
def test_the_edit_works_in_other_windows(size):
    _, output = showcase._edit(**size)
    assert annotate.split(board._split_line(output.split("\n")[1])[0])[0] == showcase.EDITED


def test_every_image_fits_the_web_terminals_store(frames):
    # measured in the browser for /kitty: restty showed 1600 x 1500, and never showed 1700 x 1500
    for frame in frames[:4]:
        payload = "".join(m[2] for m in KITTY.finditer(frame["output"]))
        image = Image.open(BytesIO(base64.b64decode(payload)))
        assert image.width * image.height <= 1600 * 1500


def test_a_small_window_is_refused():
    with pytest.raises(showcase.ShowcaseError):
        showcase.frames(89, 38, 7, 13)


def test_the_endpoint_gives_frames(client):
    response = client.get("/api/v1/showcase", query_string=SIZE)
    assert response.status_code == 200 and len(response.get_json()["frames"]) > 10


@pytest.mark.parametrize("query", [{}, {"cols": 120, "rows": 38}, {**SIZE, "cols": 50}, {**SIZE, "cols": "wide"}])
def test_the_endpoint_refuses_bad_sizes(client, query):
    assert client.get("/api/v1/showcase", query_string=query).status_code == 400


def test_the_page_is_linked_and_uses_local_libraries(client):
    html = client.get("/showcase").get_data(as_text=True)
    assert 'href="/showcase"' in html and 'id="terminal"' in html and "/static/showcase.js" in html
    script = client.get("/static/showcase.js").get_data(as_text=True)
    assert "/vendor/restty.esm.js" in script and "http" not in script and "#ffffff" in script and "#000000" in script


@pytest.mark.parametrize("smiles", ["CC(=O)Nc1ccc(O)cc1", "Cn1cnc2c1c(=O)n(C)c(=O)n2C", "NCCc1c[nH]c2ccc(O)cc12", "[NH3+]CC([O-])=O", "C#N", "[Na+].[Cl-]"])
def test_ascii_is_only_ascii(smiles):
    assert render.render(mol(smiles), 60, 30, "ascii").text.isascii()


def test_ascii_keeps_the_labels_and_draws_the_bonds():
    text = render.render(mol("NCCc1ccc(O)cc1"), 60, 30, "ascii").text
    assert "H2N" in text and "OH" in text and "/" in text and "-" in text


def test_ascii_shows_a_double_bond_as_two_lines():
    single, double = (render.render(mol(s), 40, 20, "ascii").text for s in ("CC", "C=C"))
    assert sum(c != " " and c != "\n" for c in double) > sum(c != " " and c != "\n" for c in single) * 1.5


def test_ascii_stays_inside_its_canvas():
    result = render.render(mol("Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1"), 58, 34, "ascii")
    assert max(len(line) for line in result.text.split("\n")) <= 58 and len(result.text.split("\n")) <= 34


def test_ascii_uses_larger_bonds_when_there_is_room():
    assert render._ascii_bond_dots(mol("CCO"), 60, 30) == asciiart.BOND_DOTS[0]
    assert render._ascii_bond_dots(mol("CCCCCCCCCCCCCCCCCCCC"), 40, 20) == asciiart.BOND_DOTS[-1]


def test_ascii_is_available_through_the_text_endpoint(client):
    response = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "ascii"})
    assert response.status_code == 200 and "OH" in response.get_data(as_text=True) and response.get_data(as_text=True).isascii()


def test_mono_draws_every_element_in_black():
    image, _ = graphics.draw_image(mol("CC(=O)Nc1ccc(O)cc1Cl"), 80, 30, "mono")
    pixels = np.asarray(image)
    solid = pixels[pixels[:, :, 3] == 255]
    assert len(solid) and solid[:, :3].max() == 0


def test_mono_is_accepted_where_themes_are(client):
    response = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "theme": "mono"})
    assert response.status_code == 200
    assert client.get("/api/v1/term.txt", query_string={"line": "grid gefitinib", "display": "kitty", "theme": "mono"}).status_code == 200


def test_mono_chrome_is_black_on_white():
    assert editor._BACKING["mono"] == (255, 255, 255, 255) and editor._CHROME["mono"]["ink"] == "38;2;0;0;0"
    assert editor_icons._COLOURS["mono"]["ink"] == (0, 0, 0, 255)
    assert set(editor._CHROME["mono"]) == set(editor._CHROME["light"])


def _straight_carbons(s):
    found = []
    for atom in s["atoms"]:
        neighbours = editor._neighbours(s, atom[0])
        if atom[1] == "C" and len(neighbours) == 2:
            a, b = (editor._atom(s, n) for n in neighbours)
            ax, ay, bx, by = a[2] - atom[2], a[3] - atom[3], b[2] - atom[2], b[3] - atom[3]
            if (ax * bx + ay * by) / ((ax ** 2 + ay ** 2) ** 0.5 * (bx ** 2 + by ** 2) ** 0.5) < -0.98:
                found.append(atom[0])
    return found


def _with_ethyl(smiles):
    s = editor.new_state()
    editor.load_smiles(s, smiles)
    end = next(a for a in s["atoms"] if a[1] == "C" and len(editor._neighbours(s, a[0])) == 1)
    editor._attach_group(s, end, editor.GROUPS["et"], end[2], end[3])
    return s


@pytest.mark.parametrize("smiles", ["OC", "CC", "NC", "c1ccccc1C", "CC(=O)Nc1ccc(OC)cc1"])
def test_an_attached_ethyl_group_zigzags(smiles):
    s = _with_ethyl(smiles)
    assert _straight_carbons(s) == []


def test_the_ethyl_group_comes_out_right():
    s = _with_ethyl("OC")
    carbons = [a for a in s["atoms"] if a[1] == "C"]
    middle = next(a for a in carbons if len(editor._neighbours(s, a[0])) == 2)
    a, b = (editor._atom(s, n) for n in editor._neighbours(s, middle[0]))
    ax, ay, bx, by = a[2] - middle[2], a[3] - middle[3], b[2] - middle[2], b[3] - middle[3]
    cosine = (ax * bx + ay * by) / ((ax ** 2 + ay ** 2) ** 0.5 * (bx ** 2 + by ** 2) ** 0.5)
    assert cosine > -0.9
    assert editor.analyse(s)[0] == "CCO"


def _clipboard(output):
    return [base64.b64decode(text).decode() for text in re.findall(r"\x1b\]52;c;([A-Za-z0-9+/=]*)\x1b\\", output)]


def test_showcase_copies_from_the_list_and_the_window():
    frames = showcase.frames(120, 38, 7, 14)
    copied = [text for frame in frames for text in _clipboard(frame["output"])]
    aspirin = "CC(=O)Oc1ccccc1C(=O)O"
    edited = "CCOc1ccc(NC(C)=O)cc1F |atomProp:2.atomNote.ethyl ether:13.molterm_highlight.yellow|"
    assert copied == [aspirin, aspirin, edited, edited]
    pictures = [frame["output"] for frame in frames if "\x1b]5522;type=write" in frame["output"]]
    assert len(pictures) == 1
    mimes = {base64.b64decode(m).decode() for m in re.findall(r"\x1b\]5522;type=wdata:mime=([A-Za-z0-9+/=]+);", pictures[0])}
    assert mimes == {"text/plain", "image/png", "image/svg+xml"}


def test_showcase_pastes_what_it_copied_into_a_foggy_drawing():
    pasted = next(frame["output"] for frame in showcase.frames(120, 38, 7, 14) if "$ molterm" in frame["output"])
    assert f"-f {showcase.PASTED_FOG} '{showcase.EDITED}'" in pasted
    assert "\x1b_G" in pasted


def test_showcase_shows_fog_settings():
    outputs = [frame["output"] for frame in showcase.frames(120, 38, 7, 14)]
    assert any("-t mono -f 100" in output for output in outputs)
