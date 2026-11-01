"""The session for terminals that show Kitty graphics: its endpoints for the client, and its page in a web terminal."""
import base64
import json
import os
import re
import sys
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import graphics  # noqa: E402
import medchem  # noqa: E402
import story  # noqa: E402
from app import app  # noqa: E402

KITTY = re.compile("\x1b_G([^;\x1b]*);([^\x1b]*)\x1b\\\\")


def image_of(output):
    return Image.open(BytesIO(base64.b64decode("".join(m[2] for m in KITTY.finditer(output)))))


@pytest.fixture
def client():
    return app.test_client()


def term(client, **query):
    return client.get("/api/v1/term.txt", query_string=query)


def test_kitty_sends_one_chunked_image_in_the_session():
    output = medchem.run("grid gefitinib erlotinib", display="kitty")["output"]
    keys = [m[1] for m in KITTY.finditer(output)]
    assert keys[0].startswith("a=T,f=100,c=") and "m=1" in keys[0] and keys[-1].endswith("m=0") and len(keys) > 1
    assert sum("a=T" in k for k in keys) == 1
    assert all(len(m[2]) <= graphics.CHUNK_SIZE for m in KITTY.finditer(output))


def test_kitty_honours_a_smaller_cell_and_iterm_ignores_it():
    full = image_of(medchem.run("grid gefitinib", display="kitty")["output"])
    small = image_of(medchem.run("grid gefitinib", display="kitty", cell_pixels=10)["output"])
    assert small.width < full.width * 0.7
    iterm = medchem.run("grid gefitinib", display="iterm", cell_pixels=8)["output"]
    assert iterm == medchem.run("grid gefitinib", display="iterm")["output"]


@pytest.mark.parametrize("cols", [40, 60, 120, 240])
def test_the_whole_set_fits_the_web_terminals_image_store(cols):
    # measured in the browser: restty showed 1600 x 1500, and never showed 1700 x 1500
    sheet = image_of(medchem.run("grid", cols=cols, display="kitty", cell_pixels=10)["output"])
    assert sheet.width * sheet.height <= 1600 * 1500


def test_theme_reaches_drawings_and_captions():
    def inks(theme):
        sheet = image_of(medchem.run("grid gefitinib", display="kitty", theme=theme)["output"]).convert("RGBA")
        pixels = np.asarray(sheet).astype(int)
        return pixels[pixels[:, :, 3] == 255][:, :3].sum(axis=1).mean()
    assert inks("dark") > 400 > inks("light")


def test_help_only_promises_line_editing_where_there_is_some():
    assert "Tab" in medchem.run("help")["output"]
    assert "Tab" not in medchem.run("help", line_editing=False)["output"]


def test_term_text_starts_with_the_flag_and_the_working_set(client):
    response = term(client, line="filter pains=0")
    first, rest = response.get_data(as_text=True).split("\n", 1)
    flag, names = first.split(" ")
    assert response.status_code == 200 and flag == "0" and "quercetin" not in names.split(",") and len(names.split(",")) == 13
    assert "Removed 2, kept 13." in rest


def test_term_text_keeps_the_working_set_it_is_given(client):
    assert term(client, line="ls", selection="afatinib,gefitinib").get_data(as_text=True).startswith("0 gefitinib,afatinib\n")


def test_term_text_tells_an_empty_set_from_no_set(client):
    empty = term(client, line="ls", selection="").get_data(as_text=True)
    assert empty.startswith("0 \n") and "0 of 15" in empty
    assert "15 of 15" in term(client, line="ls").get_data(as_text=True)


def test_term_text_reports_clear(client):
    assert term(client, line="clear").get_data(as_text=True).startswith("1 ")


def test_term_text_draws_images(client):
    assert "\x1b_G" in term(client, line="grid gefitinib", display="kitty").get_data(as_text=True)
    assert "\x1bP" in term(client, line="grid gefitinib", display="sixel", cell_width="9", background="202830").get_data(as_text=True)


def test_term_text_shows_a_mistake_in_the_terminal(client):
    response = term(client, line="rm -rf /")
    assert response.status_code == 200 and "Unknown command" in response.get_data(as_text=True)


@pytest.mark.parametrize("query", [{}, {"line": "ls", "display": "braille"}, {"line": "ls", "theme": "blue"}, {"line": "ls", "cols": "wide"},
                                   {"line": "ls", "cols": "-1"}, {"line": "ls", "cell_width": "3"}, {"line": "ls", "background": "red"}])
def test_term_text_refuses_bad_options(client, query):
    assert term(client, **query).status_code == 400


def test_story_text_has_four_fields_for_each_step(client):
    text = client.get("/api/v1/story.txt").get_data(as_text=True)
    rows = [row.split("\t") for row in text.rstrip("\n").split("\n")]
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [(s["title"], s["command"], s["before"], s["after"]) for s in story.STEPS]
    assert all(len(r) == 4 for r in rows)


def test_story_is_ascii_because_the_client_wraps_it_by_bytes():
    for step in story.STEPS:
        assert all(value.isascii() for value in step.values())


def test_story_only_claims_what_is_true_of_every_display():
    for step in story.STEPS:
        output = medchem.run(step["command"], display="kitty")["output"]
        assert output and "Unknown command" not in output
    assert "plain text" not in json.dumps(story.STEPS)


def test_page_carries_its_story_and_local_libraries(client):
    html = client.get("/kitty").get_data(as_text=True)
    assert json.loads(re.search(r"const STORY = (\[.*?\]);\n", html, re.S)[1]) == story.STEPS
    assert 'id="terminal"' in html and 'id="theme"' in html and "/molterm && " not in html and "./molterm demo" in html
    assert "<script" in html and not re.search(r'(src|href)="https?://[^"]*\.(js|css|ttf)', html)


@pytest.mark.parametrize("name", ["restty.esm.js", "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "LICENSE.restty", "LICENSE.dejavu"])
def test_libraries_are_served_from_the_image(client, name):
    response = client.get(f"/vendor/{name}")
    assert response.status_code == 200
    response.close()


def test_page_does_not_use_the_libraries_cdn_fonts(client):
    script = client.get("/static/kitty.js").get_data(as_text=True)
    assert "/vendor/DejaVuSansMono.ttf" in script and "fonts:" in script and "http" not in script
