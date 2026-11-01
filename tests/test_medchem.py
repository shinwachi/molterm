import json
import os
import re
import sys

import pytest
from rdkit import Chem

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import medchem  # noqa: E402
import render  # noqa: E402
import story  # noqa: E402
from app import app  # noqa: E402

ANSI = re.compile(r"\x1b\[\d+m")
FULL = list(medchem.ALL_NAMES)


def plain(result):
    return ANSI.sub("", result["output"])


def run(line, selection=None, cols=100):
    return medchem.run(line, selection, cols)


def after_filter():
    return run("filter pains=0")["selection"]


# the data


@pytest.mark.parametrize("name", FULL)
def test_compounds_parse_and_draw(name):
    assert medchem.mol(name).GetNumAtoms() > 5
    assert medchem.drawing(name)


def test_compounds_are_distinct():
    keys = {Chem.MolToInchiKey(medchem.mol(name)) for name in FULL}
    assert len(keys) == len(FULL)


def test_known_properties():
    gefitinib = medchem.properties("gefitinib")
    assert gefitinib["mw"] == pytest.approx(446.9, abs=0.1)
    assert gefitinib["hbd"] == 1
    assert medchem.properties("lapatinib")["ro5"] == 2


# each finding in the story, against the real output


def steps_up_to(index):
    """Run the story from the start; return the result of step `index`."""
    selection = None
    for step in story.STEPS[:index + 1]:
        result = run(step["command"], selection)
        selection = result["selection"]
    return result


def step_named(title):
    return next(i for i, step in enumerate(story.STEPS) if step["title"] == title)


def test_every_step_runs_without_an_error():
    selection = None
    for step in story.STEPS:
        result = run(step["command"], selection)
        selection = result["selection"]
        assert result["output"]
        assert "\x1b[31mUnknown" not in result["output"] and "See: " not in plain(result)


def test_every_step_fits_a_hundred_columns():
    selection = None
    for step in story.STEPS:
        result = run(step["command"], selection, cols=100)
        selection = result["selection"]
        assert max(len(line) for line in plain(result).split("\n")) <= 100, step["command"]


def test_story_opens_with_fifteen_compounds():
    assert len(FULL) == 15
    assert "15" in story.STEPS[0]["before"] and "15" in story.STEPS[0]["after"]
    output = plain(steps_up_to(0))
    assert all(name in output for name in FULL)


def test_story_lapatinib_and_neratinib_rank_last_with_two_violations():
    assert sorted(FULL, key=lambda n: medchem.properties(n)["qed"])[:2] == ["lapatinib", "neratinib"]
    assert [n for n in FULL if medchem.properties(n)["ro5"] >= 2] == ["lapatinib", "neratinib"]


def test_story_pains_hits():
    flagged = [n for n in FULL if medchem.alerts(n, "pains")]
    assert flagged == ["quercetin", "epalrestat"]
    assert "catechol" in medchem.alerts("quercetin", "pains")[0][0]
    assert "rhod" in medchem.alerts("epalrestat", "pains")[0][0]
    assert all(medchem.alerts(n, "pains")[0][1] for n in flagged)


def test_story_filter_leaves_thirteen():
    selection = steps_up_to(step_named("Narrow the working set"))["selection"]
    assert len(selection) == 13 and "quercetin" not in selection and "epalrestat" not in selection


def test_story_four_past_the_plot_limits():
    past = [n for n in after_filter() if medchem.properties(n)["mw"] > 500 or medchem.properties(n)["logp"] > 5]
    assert sorted(past) == ["dacomitinib", "lapatinib", "neratinib", "vandetanib"]


def test_story_seven_quinazolines():
    output = plain(run(f"match {story.QUINAZOLINE}", after_filter()))
    assert "7 of 13 match" in output
    misses = output.split("No match:")[1]
    assert "neratinib" in misses and "osimertinib" in misses


def test_story_four_acrylamides_are_the_covalent_ones():
    query = Chem.MolFromSmarts(story.ACRYLAMIDE)
    hits = [n for n in after_filter() if medchem.mol(n).HasSubstructMatch(query)]  # by this step, epalrestat has gone
    assert hits == ["afatinib", "dacomitinib", "neratinib", "osimertinib"]
    assert hits == [n for n in FULL if "covalent" in medchem.COMPOUNDS[n][1]]
    quinazoline = Chem.MolFromSmarts(story.QUINAZOLINE)
    assert [n for n in hits if not medchem.mol(n).HasSubstructMatch(quinazoline)] == ["neratinib", "osimertinib"]


def test_story_brenk_flags_the_warheads_as_michael_acceptors():
    query = Chem.MolFromSmarts(story.ACRYLAMIDE)
    for name in ("afatinib", "dacomitinib", "neratinib", "osimertinib"):
        found = dict(medchem.alerts(name, "brenk"))
        assert "Michael_acceptor_1" in found
        warhead = {a for match in medchem.mol(name).GetSubstructMatches(query) for a in match}
        assert found["Michael_acceptor_1"] & warhead


def test_story_rgroups_positions():
    output = plain(run(f"rgroups {story.QUINAZOLINE}", after_filter()))
    rows = {line.split()[0]: line.split() for line in output.split("\n") if line.strip()}
    assert "7 compounds on this core" in output
    head = rows["compound"]
    assert head[1:4] == ["R1", "R2", "R3"]
    for name in ("gefitinib", "erlotinib", "icotinib", "lapatinib", "vandetanib", "afatinib", "dacomitinib"):
        assert "N*" in rows[name][1]  # the aniline
    for name in ("afatinib", "dacomitinib"):
        assert "C(=O)N*" in rows[name][3] or "O=C(" in rows[name][3]  # the acrylamide


def test_story_closest_to_gefitinib():
    output = plain(run("sim gefitinib", after_filter()))
    rows = [line.split() for line in output.split("\n")[1:4]]
    assert [row[0] for row in rows] == ["dacomitinib", "vandetanib", "afatinib"]
    assert [row[1] for row in rows] == ["0.53", "0.44", "0.44"]
    shared = Chem.MolFromSmarts("COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1")
    assert medchem.mol("gefitinib").HasSubstructMatch(shared) and medchem.mol("dacomitinib").HasSubstructMatch(shared)
    assert not medchem.mol("vandetanib").HasSubstructMatch(Chem.MolFromSmarts("Nc1ccc(F)c(Cl)c1"))


def test_story_compare_changes():
    a, b = medchem.properties("gefitinib"), medchem.properties("dacomitinib")
    assert round(b["mw"] - a["mw"]) == 23
    assert a["logp"] < 5 < b["logp"] and f"{b['logp']:.1f}" == "5.2"
    assert "+23" in plain(run("compare gefitinib dacomitinib"))


def test_story_compare_shares_the_core_and_swaps_the_tail():
    common = medchem.rdFMCS.FindMCS([medchem.mol("gefitinib"), medchem.mol("dacomitinib")], timeout=2,
                                    ringMatchesRingOnly=True, completeRingsOnly=True)
    query = Chem.MolFromSmarts(common.smartsString)
    assert query.HasSubstructMatch(Chem.MolFromSmiles("Nc1ccc(F)c(Cl)c1"))
    assert query.HasSubstructMatch(Chem.MolFromSmiles("c1ccc2ncncc2c1"))
    morpholine = Chem.MolFromSmarts("N1CCOCC1")
    assert medchem.mol("gefitinib").HasSubstructMatch(morpholine)
    assert not medchem.mol("dacomitinib").HasSubstructMatch(morpholine)


def test_compare_changes_add_up_as_shown():
    output = plain(run("compare gefitinib dacomitinib"))
    tpsa = next(line.split() for line in output.split("\n") if line.startswith("TPSA"))
    assert int(tpsa[2]) - int(tpsa[1]) == int(tpsa[3])


def test_story_says_nothing_about_potency_without_saying_so():
    assert "potency" in story.STEPS[-1]["after"]


# highlighting


def test_highlight_marks_only_cells_with_ink():
    mol = medchem.mol("afatinib")
    atoms = frozenset(a for m in mol.GetSubstructMatches(Chem.MolFromSmarts(story.ACRYLAMIDE)) for a in m)
    result = render.render(mol, 60, 24, highlight=atoms)
    marked = [(r, c) for r, row in enumerate(result.colors) for c, code in enumerate(row) if code == render.ANSI_HIGHLIGHT]
    assert marked
    assert all(result.grid[r][c] != render.braille.BLANK_CHAR for r, c in marked)


def test_highlight_does_not_change_the_characters():
    mol = medchem.mol("afatinib")
    assert render.render(mol, 60, 24, highlight=frozenset(range(5))).grid == render.render(mol, 60, 24).grid


def test_no_highlight_has_no_highlight_colour():
    assert all(render.ANSI_HIGHLIGHT not in row for row in render.render(medchem.mol("afatinib"), 60, 24).colors)


# the commands


def test_selection_order_and_unknown_names():
    assert run("ls", ["imatinib", "nobody", "gefitinib"])["selection"] == ["gefitinib", "imatinib"]


def test_filter_conditions_combine():
    result = run("filter pains=0 mw<=500 logp<5")
    assert all(medchem.properties(n)["mw"] <= 500 and medchem.properties(n)["logp"] < 5 for n in result["selection"])
    assert "gefitinib" in result["selection"] and "lapatinib" not in result["selection"]


def test_reset_restores_everything():
    assert run("reset", ["gefitinib"])["selection"] == FULL


def test_clear_is_reported():
    assert run("clear")["clear"] is True and run("ls")["clear"] is False


def test_grid_wraps_to_the_width():
    for cols in (40, 100, 200):
        assert max(len(line) for line in plain(run("grid", cols=cols)).split("\n")) <= max(cols, 60)
    assert len(plain(run("grid", cols=40)).split("\n")) > len(plain(run("grid", cols=200)).split("\n"))


def test_plot_labels_every_compound_or_says_it_could_not():
    output = plain(run("plot mw logp"))
    for name in FULL:
        assert name in output


def test_plot_rows_share_one_width():
    rows = plain(run("plot mw logp")).split("\n")[1:19]
    assert len({len(row) for row in rows}) == 1


def test_show_accepts_a_smiles():
    assert "C2H6O" in plain(run("show CCO"))


@pytest.mark.parametrize("line, message", [
    ("blah", "Unknown command"), ("filter nope<1", "not a condition"), ("filter", "Give conditions"),
    ("match ((", "not valid SMARTS"), ("rgroups ((", "not valid SMARTS"), ("sim nobody", "No compound"),
    ("sim", "Name 1"), ("compare gefitinib", "Name 2"), ("show 'open", "Could not read"), ("props nope", "Sort by"),
    ("alerts nope", "filter set"), ("plot mw", "two properties"), ("show not(a(smiles", "not a compound"),
    ("x" * 501, "too long"),
])
def test_mistakes_get_a_message(line, message):
    result = run(line)
    assert message in plain(result)
    assert "\x1b[31m" in result["output"]


def test_a_mistake_keeps_the_selection():
    assert run("blah", ["gefitinib", "imatinib"])["selection"] == ["gefitinib", "imatinib"]


def test_an_empty_set_is_handled():
    empty = run("filter mw<1")["selection"]
    assert empty == []
    for line in ("ls", "grid", "props", "alerts pains", "plot mw logp", "match C", "rgroups c1ccccc1", "sim gefitinib"):
        assert isinstance(run(line, empty)["output"], str)


def test_nothing_is_executed():
    # a line is only ever looked up: shell syntax is an unknown command or a plain argument
    assert "Unknown command" in plain(run("rm -rf /")) and "Unknown command" in plain(run("$(reboot)"))


# the page and its endpoint


@pytest.fixture
def client():
    return app.test_client()


def test_endpoint_runs_a_command(client):
    body = client.post("/api/v1/term", json={"line": "filter pains=0", "selection": None, "cols": 80}).get_json()
    assert len(body["selection"]) == 13 and body["clear"] is False


@pytest.mark.parametrize("payload", [{}, {"line": 3}, {"line": "ls", "selection": "gefitinib"}, {"line": "ls", "selection": [1]},
                                     {"line": "ls", "cols": "wide"}, {"line": "ls", "cols": True}])
def test_endpoint_rejects_bad_requests(client, payload):
    response = client.post("/api/v1/term", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "bad_request"


def test_page_carries_the_story(client):
    html = client.get("/terminal").get_data(as_text=True)
    assert 'id="screen"' in html and 'id="theme"' in html and "/static/terminal.js" in html
    embedded = re.search(r"const STORY = (\[.*?\]);\n", html, re.S).group(1)
    assert json.loads(embedded) == story.STEPS


def test_page_is_linked_from_the_others(client):
    for path in ("/", "/demo", "/terminal"):
        assert 'href="/terminal"' in client.get(path).get_data(as_text=True)


def test_page_uses_local_assets_only(client):
    html = client.get("/terminal").get_data(as_text=True)
    assert "http://" not in html and "https://" not in html
