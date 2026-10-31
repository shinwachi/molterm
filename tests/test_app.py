import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from app import app  # noqa: E402


def test_health():
    r = app.test_client().get("/health")
    assert r.status_code == 200
    assert r.get_json()["service"] == "molterm"


def test_index():
    assert app.test_client().get("/").status_code == 200
