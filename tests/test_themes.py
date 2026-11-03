"""Colour themes: named ones, ones fitted to the terminal's colours, and their use by the pictures, the editor and the list."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

import board  # noqa: E402
import editor  # noqa: E402
import graphics  # noqa: E402
import medchem  # noqa: E402
import molecule  # noqa: E402
import themes  # noqa: E402
from app import app  # noqa: E402


@pytest.fixture
def client():
    return app.test_client()


def luminance(colour):
    return (299 * colour[0] + 587 * colour[1] + 114 * colour[2]) / 1000


@pytest.mark.parametrize("name", themes.NAMES)
def test_every_theme_is_complete_and_legible(name):
    theme = themes.get(name)
    assert len(theme.elements) == 5
    # the ink stands out from the background, whichever is lighter
    assert abs(luminance(theme.ink) - luminance(theme.background)) > 100
    assert theme.dark == (luminance(theme.background) < 128)
    assert set(themes.chrome(name)) == {"bar", "ink", "dim", "pick", "on_pick", "err", "warn", "pick_ink"}
    assert set(themes.icons(name)) == {"ink", "hover", "active", "on_active"}


def test_the_original_themes_keep_their_colours():
    assert themes.chrome("dark")["bar"] == "48;2;42;42;40" and themes.chrome("light")["pick"] == "48;2;42;120;214"
    assert themes.panel("dark") == (32, 32, 30, 255) and themes.panel("mono") == (255, 255, 255, 255)
    assert themes.chrome("mono")["on_pick"] == "38;2;0;0;0;4"
    assert themes.icons("mono")["active"] == (0, 0, 0, 255) and themes.icons("mono")["on_active"] == (255, 255, 255, 255)


def test_a_theme_fitted_to_the_terminal_takes_its_colours():
    theme = themes.get(themes.spec("dark", "1E1E2E", "cdd6f4"))
    assert theme.background == (0x1e, 0x1e, 0x2e) and theme.ink == (0xcd, 0xd6, 0xf4)
    assert themes.panel(theme.name) == (0x1e, 0x1e, 0x2e, 255)
    # the bars sit between the two, near the background
    assert all(min(a, b) <= c <= max(a, b) for a, b, c in zip(theme.background, theme.ink, theme.bar))
    assert theme.elements == themes.get("dark").elements
    assert themes.get("light::102030").background == themes.get("light").background


@pytest.mark.parametrize("text", ["", "neon", "dark:12345", "dark:zzzzzz:", "dark:000000:000000:000000", "Dark"])
def test_unknown_themes_are_refused(text):
    with pytest.raises(themes.ThemeError):
        themes.get(text)


def test_endpoints_take_named_and_fitted_themes(client):
    for theme in themes.NAMES:
        assert client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "theme": theme}).status_code == 200
    fitted = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "theme": "dark", "background": "1e1e2e", "foreground": "cdd6f4"})
    plain = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "theme": "dark"})
    assert fitted.status_code == 200 and fitted.data != plain.data
    bad = client.get("/api/v1/render.txt", query_string={"input": "CCO", "mode": "kitty", "theme": "neon"})
    assert bad.status_code == 400 and b"solarized-dark" in bad.data
    assert client.get("/api/v1/render.txt", query_string={"input": "CCO", "foreground": "white"}).status_code == 400
    assert client.post("/api/v1/edit", data={"cols": 100, "rows": 30, "theme": "gruvbox-light"}).status_code == 200
    assert client.post("/api/v1/tui", data={"cols": 100, "rows": 30, "theme": "dracula", "background": "282a36"}).status_code == 200
    assert client.post("/api/v1/edit", data={"cols": 100, "rows": 30, "theme": "neon"}).status_code == 400
    assert client.get("/api/v1/term.txt", query_string={"line": "grid gefitinib", "display": "kitty", "theme": "nord"}).status_code == 200


@pytest.mark.parametrize("name", ["nord", "solarized-light", "dark:101010:e0e0e0"])
def test_the_editor_and_the_list_draw_in_the_theme(name):
    colours = themes.chrome(name)
    _, _, output = editor.step("", "CCO", "", 100, 30, 10, 20, True, name)
    assert colours["bar"] in output and colours["ink"] in output
    _, _, output = board.step("", "", "", 100, 30, 10, 20, True, name)
    assert colours["ink"] in output or colours["dim"] in output


def test_session_captions_take_the_theme():
    ink = medchem._image_ink("dracula")
    assert ink[medchem.BOLD] == "#f8f8f2" and ink[medchem.CYAN] == "#8be9fd"


def test_the_drawing_uses_the_theme_elements():
    image, _ = graphics.draw_image(molecule.parse("CCO", "smiles").mol, 20, 10, "gruvbox-dark")
    pixels = np.asarray(image)
    opaque = {tuple(int(c) for c in p) for p in pixels[pixels[:, :, 3] == 255][:, :3]}
    assert themes.get("gruvbox-dark").elements[0] in opaque  # oxygen, red
