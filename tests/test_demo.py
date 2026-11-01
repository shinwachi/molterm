import html
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import molecule  # noqa: E402
import render  # noqa: E402
import samples  # noqa: E402
from app import app  # noqa: E402

ALL = [item for _, items in samples.GROUPS for item in items]
IDS = [name for name, _, _ in ALL]


@pytest.fixture
def page():
    return app.test_client().get("/demo").get_data(as_text=True)


def test_names_and_structures_are_unique():
    assert len(set(IDS)) == len(IDS)
    canonical = [molecule.parse(smiles).to_dict()["smiles"] for _, smiles, _ in ALL]
    assert len(set(canonical)) == len(canonical)


@pytest.mark.parametrize("name, smiles, formula", ALL, ids=IDS)
def test_sample_has_the_stated_formula(name, smiles, formula):
    assert molecule.parse(smiles).to_dict()["formula"] == formula


@pytest.mark.parametrize("name, smiles, formula", ALL, ids=IDS)
def test_sample_draws_legibly_at_the_page_size(name, smiles, formula):
    result = render.render(molecule.parse(smiles).mol).to_dict()
    assert not result["cramped"]
    assert result["cols"] <= render.DEFAULT_COLS and result["rows"] <= render.DEFAULT_ROWS
    lines = result["text"].split("\n")
    for label in result["labels"]:
        assert lines[label["row"]][label["col"]:label["col"] + len(label["text"])] == label["text"]
    # labels that touch read as one group: ATP's phosphates gave "OHO"
    spans = sorted((label["row"], label["col"], label["col"] + len(label["text"])) for label in result["labels"])
    for (row, _, end), (next_row, start, _) in zip(spans, spans[1:]):
        assert row != next_row or start > end


def test_page_lists_every_sample(page):
    unescaped = html.unescape(page)
    for name, smiles, formula in ALL:
        assert name in unescaped
        assert f'data-smiles="{smiles}"' in unescaped


def test_page_escapes_samples_into_attributes(page):
    # '+' and '=' must survive, or the buttons would send a different molecule
    assert 'data-smiles="[Na+].[Cl-]"' in page


def test_page_starts_on_the_default_sample(page):
    default = next(smiles for name, smiles, _ in ALL if name == samples.DEFAULT)
    assert f'input.value = "{default}"' in page


def test_page_has_the_house_top_bar(page):
    assert 'class="topbar"' in page and 'id="theme"' in page
    assert '/demo" aria-current="page"' in page


def test_pages_use_local_assets_only(page):
    assert "http://" not in page and "https://" not in page
    assert "/static/molterm.js" in page


def test_static_assets_are_served():
    client = app.test_client()
    for path in ("/static/molterm.js", "/static/molterm.css", "/static/house.css"):
        assert client.get(path).status_code == 200


def test_both_pages_link_to_each_other():
    client = app.test_client()
    assert 'href="/demo"' in client.get("/").get_data(as_text=True)
    assert 'href="/"' in client.get("/demo").get_data(as_text=True)
