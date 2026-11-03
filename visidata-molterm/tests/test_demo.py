"""VisiData itself, run in a pseudo-terminal with the plugin against the molterm server, and the demo's page.

The session tests need the server at $MOLTERM_URL, and are skipped when it can't be reached.
"""
import json
import os
import re
import sys
import urllib.request

import pyte
import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "demo"))

import record  # noqa: E402

URL = os.environ.get("MOLTERM_URL", "http://molterm.localhost")


def reachable():
    """Whether the server answers, with the endpoints the plugin needs."""
    try:
        with urllib.request.urlopen(URL + "/api/v1/render.png?input=CCO", timeout=5):
            return True
    except OSError:
        return False


needs_server = pytest.mark.skipif(not reachable(), reason=f"no molterm server with /api/v1/render.png at {URL}")
KITTY = re.compile(r"\x1b_G(.*?)\x1b\\", re.S)


def screens(frames, cols=record.COLS, rows=record.ROWS):
    """The text on the screen after each frame, as a terminal shows it, pictures aside."""
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    out = []
    for frame in frames:
        stream.feed(KITTY.sub("", frame["output"]))
        out.append("\n".join(screen.display))
    return out


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    steps = [(None, None, "start", 0), ("l", "l", "smiles", 0), ("\x1bm", "Alt+m", "structures", 0),
             ("\x1bt", "Alt+t", "braille", 0), ("\x1bo", "Alt+o", "molecule", 0), ("q", "q", "back", 0)]
    return record.record(steps, env={"MOLTERM_URL": URL})


@needs_server
def test_visidata_starts_with_the_plugin(session):
    first = screens(session)[0]
    assert "aspirin" in first and "CC(=O)Oc1ccccc1C(=" in first


@needs_server
def test_structures_are_kitty_pictures_placed_in_their_cells(session):
    output = "".join(frame["output"] for frame in session[:3])
    commands = [dict(part.split("=") for part in packet.split(";")[0].split(",")) for packet in KITTY.findall(output)]
    placed = [c for c in commands if c.get("a") == "p"]
    sent = {c["i"] for c in commands if c.get("a") == "t"}
    assert placed and {c["i"] for c in placed} <= sent
    assert all(c["C"] == "1" and c["z"] == "-1" and int(c["c"]) <= 24 for c in placed)
    assert "smiles_structure" in screens(session)[2]


@needs_server
def test_braille_draws_the_molecules_in_text(session):
    shown = screens(session)[3]
    assert sum(0x2801 <= ord(c) <= 0x28FF for c in shown) > 100
    assert "OH" in shown  # aspirin's acid, labelled in the drawing


@needs_server
def test_a_molecule_opens_on_its_own_sheet_and_q_returns(session):
    molecule, back = screens(session)[4:6]
    assert "SMILES   CC(=O)Oc1ccccc1C(=O)O" in molecule and "formula  C9H8O4" in molecule and "MW 180.2" in molecule
    assert "1›drugs | 2›aspirin" in molecule
    assert "smiles_structure" in back


# the page


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "OUT", str(tmp_path / "recording.json"))
    monkeypatch.setenv("MOLTERM_VD_NO_RECORDING", "1")
    import app as demo
    monkeypatch.setattr(demo, "ensure_recording", lambda job=None: None)
    return demo, demo.app.test_client()


def test_the_page_waits_while_recording(client):
    demo, http = client
    demo._state["recording"] = ("demo", 116, 34)
    try:
        response = http.get("/api/v1/recording")
        assert response.status_code == 503 and response.get_json()["error"]["code"] == "recording"
    finally:
        demo._state["recording"] = None


def test_the_page_serves_the_recording(client):
    demo, http = client
    with open(record.OUT, "w") as f:
        json.dump({"cols": 116, "rows": 34, "recorded": "now", "frames": [{"caption": "c", "key": None, "output": "x", "pause": 1}]}, f)
    assert http.get("/api/v1/recording").get_json()["frames"][0]["output"] == "x"
    assert http.get("/health").get_json() == {"status": "ok", "recording": False, "ready": True}
    page = http.get("/").get_data(as_text=True)
    assert "/static/vd.js" in page and "/vendor/house.css" in page


def test_a_recording_older_than_the_plugin_is_made_again(client, tmp_path):
    demo, _ = client
    with open(record.OUT, "w") as f:
        f.write("{}")
    os.utime(record.OUT, (0, 0))
    assert demo.stale()
    os.utime(record.OUT, None)
    assert not demo.stale()


def test_the_showcase_scene_is_recorded_per_size_within_limits(client):
    demo, http = client
    assert demo._job({"script": "showcase", "cols": "100", "rows": "38"}) == ("showcase", 100, 38)
    for bad in ({"script": "showcase", "cols": "20", "rows": "38"}, {"script": "showcase", "cols": "x", "rows": "38"},
                {"script": "other"}):
        assert demo._job(bad) is None
    assert http.get("/api/v1/recording?script=showcase&cols=5000&rows=38").status_code == 400
    assert record.output_path("showcase", 100, 38).endswith("showcase-100x38.json")
    assert record.SCRIPTS["showcase"][1] == "showcase.visidatarc" and record.SHOWCASE_STEPS[-1][0] == "gq"  # VisiData quits at the end
