"""The two-pane page: the same commands as text and as images. Each finding in its story is checked here."""
import base64
import json
import os
import re
import sys
from io import BytesIO

import numpy as np
import pytest
from PIL import Image
from rdkit import Chem

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import graphics  # noqa: E402
import medchem  # noqa: E402
import render  # noqa: E402
import story  # noqa: E402
from app import app  # noqa: E402

ANSI = re.compile(r"\x1b\[\d+m")
ITERM = re.compile("\x1b\\]1337;File=([^:]*):([^\x07]*)\x07")
DRAWING_COMMANDS = ["grid", "show afatinib", f"match {story.ACRYLAMIDE}", "alerts pains", "compare gefitinib dacomitinib",
                    "sim gefitinib"]
TEXT_COMMANDS = ["props qed", "plot mw logp", "ls", "help"]


def run(line, display="text", selection=None, cols=70, cell_pixels=None):
    return medchem.run(line, selection, cols, display, cell_pixels)["output"]


def images(output):
    return [Image.open(BytesIO(base64.b64decode(m[2]))) for m in ITERM.finditer(output)]


def has_image(output):
    return "\x1b]1337;" in output or "\x1bP" in output


@pytest.mark.parametrize("line", DRAWING_COMMANDS)
@pytest.mark.parametrize("display", ["iterm", "sixel"])
def test_drawings_become_one_image(line, display):
    output = run(line, display)
    assert len(re.findall("\x1b\\]1337;|\x1bP", output)) == 1
    without_image = re.sub("\x1b\\]1337;[^\x07]*\x07|\x1bP.*?\x1b\\\\", "", output, flags=re.S)
    assert not re.search("[⠁-⣾]", without_image)  # no Braille drawing beside it; a full ⣿ is sim's score bar


@pytest.mark.parametrize("line", DRAWING_COMMANDS)
def test_text_display_has_no_images(line):
    assert not has_image(run(line))


@pytest.mark.parametrize("line", TEXT_COMMANDS)
@pytest.mark.parametrize("display", ["iterm", "sixel"])
def test_tables_and_plots_are_the_same_in_both(line, display):
    assert run(line, display) == run(line)


def test_selection_is_the_same_in_both():
    assert medchem.run("filter pains=0", None, 70, "iterm")["selection"] == medchem.run("filter pains=0")["selection"]


def test_image_width_fits_the_columns():
    for line in DRAWING_COMMANDS:
        for picture in images(run(line, "iterm", cols=70)):
            assert picture.width <= 70 * graphics.CELL_PIXELS


def test_gallery_wraps_to_the_columns():
    narrow, wide = images(run("grid", "iterm", cols=50))[0], images(run("grid", "iterm", cols=200))[0]
    assert narrow.height > wide.height and narrow.width < wide.width


def test_image_keeps_the_scale_of_the_text():
    # about the same number of columns per drawing, however many are shown
    width = int(re.search(r"width=(\d+)", run("show gefitinib", "iterm"))[1])
    text_rows = ANSI.sub("", run("grid gefitinib")).split("\n")
    assert abs(width - max(len(row) for row in text_rows)) <= 4


def test_highlight_shows_in_the_image_and_changes_nothing_else():
    mol = medchem.mol("afatinib")
    atoms = frozenset(a for m in mol.GetSubstructMatches(Chem.MolFromSmarts(story.ACRYLAMIDE)) for a in m)
    marked, plain = graphics.draw_image(mol, 80, 30, highlight=atoms)[0], graphics.draw_image(mol, 80, 30)[0]
    assert marked.size == plain.size
    assert (np.asarray(marked) != np.asarray(plain)).any()
    r, g, b = (np.asarray(marked).astype(int)[:, :, i] for i in range(3))
    alpha = np.asarray(marked)[:, :, 3]
    assert ((g > r + 40) & (b > r + 40) & (alpha > 0)).any()  # some of it is cyan


def test_captions_are_in_the_image():
    with_names = images(run("grid gefitinib", "iterm"))[0]
    plain = images(run("show gefitinib", "iterm"))[0]
    assert with_names.height > plain.height


def test_sixel_uses_the_cell_width_it_is_given():
    small = run("show gefitinib", "sixel", cell_pixels=8)
    large = run("show gefitinib", "sixel", cell_pixels=16)
    size = lambda text: int(re.search(r'"1;1;(\d+);', text)[1])  # noqa: E731
    assert size(large) > size(small) * 1.8


def test_a_molecule_without_bonds_stays_text():
    for display in ("iterm", "sixel"):
        assert not has_image(run("show [Na+].[Cl-]", display))


def test_a_mistake_is_text_in_both():
    assert run("nope", "iterm") == run("nope")


# each finding in the story


def test_every_step_runs_cleanly_in_all_displays():
    for step in story.COMPARE_STEPS:
        for display in medchem.DISPLAYS:
            output = run(step["command"], display)
            assert output and "See: " not in ANSI.sub("", output)


def test_story_opens_with_fifteen():
    assert len(medchem.ALL_NAMES) == 15 and "15" in story.COMPARE_STEPS[0]["after"]


def test_story_afatinib_has_a_stereocentre_and_double_bonds():
    mol = medchem.mol("afatinib")
    assert Chem.FindMolChiralCenters(mol, includeUnassigned=True)
    assert mol.HasSubstructMatch(Chem.MolFromSmarts("C=C")) and mol.HasSubstructMatch(Chem.MolFromSmarts("C=O"))


def test_story_the_wedge_appears_only_in_the_image():
    # same atoms in the same places, with and without stereo: only a wedge could tell them apart
    stereo = medchem.mol("afatinib")
    flat = Chem.Mol(stereo)
    Chem.RemoveStereochemistry(flat)
    assert render.render(stereo, 60, 24).text == render.render(flat, 60, 24).text
    assert (np.asarray(graphics.draw_image(stereo, 80, 30)[0]) != np.asarray(graphics.draw_image(flat, 80, 30)[0])).any()


def test_story_five_of_fifteen_acrylamides():
    assert "5 of 15 match" in ANSI.sub("", run(f"match {story.ACRYLAMIDE}"))


def test_story_pains_hits():
    output = ANSI.sub("", run("alerts pains"))
    assert "quercetin" in output and "epalrestat" in output and "2 flagged" in output
    assert "catechol" in output and "rhod" in output


def test_story_compare_tables_are_identical():
    def table(output):
        return ANSI.sub("", output)[ANSI.sub("", output).index("property"):]
    assert table(run("compare gefitinib dacomitinib")) == table(run("compare gefitinib dacomitinib", "iterm"))


def test_story_plot_and_props_have_no_image():
    for line in ("plot mw logp", "props qed"):
        assert not has_image(run(line, "iterm"))
    assert re.search("[⠁-⣿]", run("plot mw logp", "iterm"))


# the page and its endpoint


@pytest.fixture
def client():
    return app.test_client()


def test_endpoint_returns_images(client):
    body = client.post("/api/v1/term", json={"line": "grid gefitinib", "display": "iterm"}).get_json()
    assert "\x1b]1337;" in body["output"]
    body = client.post("/api/v1/term", json={"line": "grid gefitinib", "display": "sixel", "cell_width": 9}).get_json()
    assert "\x1bP" in body["output"]


@pytest.mark.parametrize("payload", [{"display": "kitty"}, {"display": "braille"}, {"display": 3}, {"cell_width": "9"}, {"cell_width": 3},
                                     {"cell_width": 41}, {"cell_width": True}])
def test_endpoint_rejects_bad_options(client, payload):
    response = client.post("/api/v1/term", json={"line": "ls", **payload})
    assert response.status_code == 400 and response.get_json()["error"]["code"] == "bad_request"


def test_page_carries_its_story_and_local_libraries(client):
    html = client.get("/compare").get_data(as_text=True)
    assert json.loads(re.search(r"const STORY = (\[.*?\]);\n", html, re.S)[1]) == story.COMPARE_STEPS
    assert "http://" not in html and "https://" not in html
    assert 'id="pane-text"' in html and 'id="pane-image"' in html and 'id="theme"' in html


@pytest.mark.parametrize("name", ["xterm.js", "xterm.css", "addon-fit.js", "addon-image.js"])
def test_libraries_are_served_from_the_image(client, name):
    assert client.get(f"/vendor/{name}").status_code == 200


def test_vendor_route_stays_inside_its_folder(client):
    assert client.get("/vendor/../server/app.py").status_code == 404
    assert client.get("/vendor/%2e%2e/VERSION").status_code == 404


def test_every_page_links_here(client):
    for path in ("/", "/demo", "/terminal", "/compare"):
        assert 'href="/compare"' in client.get(path).get_data(as_text=True)
