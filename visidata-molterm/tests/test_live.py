"""The live demo: real VisiData sessions over a WebSocket, as the page opens them, and the lockdown that keeps them to the sheet.

Each test runs the server in this process and a VisiData per session, reading the screen back with a terminal emulator. Drawings need
the molterm server at $MOLTERM_URL; the tests of structures skip without it.
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time

import pyte
import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = os.path.dirname(__file__)
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "demo")]

import library  # noqa: E402
import live  # noqa: E402
from test_demo import needs_server  # noqa: E402

KITTY = re.compile(r"\x1b_G(.*?)\x1b\\", re.S)
COLS, ROWS = 120, 34


@pytest.fixture(autouse=True)
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "WORK", str(tmp_path))
    monkeypatch.setattr(live, "LIBRARY", str(tmp_path / "compounds.csv"))
    live.sessions.clear()
    return tmp_path


class Visitor:
    """A browser's side of a session: a WebSocket, and the screen its output draws."""

    def __init__(self, client, origin=True):
        self.client, self.origin = client, origin
        self.screen = pyte.Screen(COLS, ROWS)
        self.raw = b""

    async def __aenter__(self):
        headers = {"Origin": f"http://{self.client.host}:{self.client.port}"} if self.origin else {"Origin": "http://elsewhere.example"}
        self.ws = await self.client.ws_connect("/live/ws", headers=headers)
        await self.ws.send_str(json.dumps({"type": "resize", "cols": COLS, "rows": ROWS}))
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def read(self, quiet=1.0, longest=15):
        """Take output until it pauses."""
        started = time.monotonic()
        while time.monotonic() - started < longest:
            try:
                message = await self.ws.receive(timeout=quiet)
            except asyncio.TimeoutError:
                break
            if message.type != 2:  # binary
                break
            self.raw += message.data
        # from all the output, since a picture's sequence can be split between messages
        self.screen = pyte.Screen(COLS, ROWS)
        pyte.Stream(self.screen).feed(KITTY.sub("", self.raw.decode("utf-8", "replace")))
        return self.text()

    async def press(self, keys, quiet=1.0):
        await self.ws.send_str(json.dumps({"type": "input", "data": keys}))
        return await self.read(quiet)

    def text(self):
        return "\n".join(self.screen.display)


def run(test):
    async def go():
        client = TestClient(TestServer(live.make_app()))
        await client.start_server()
        try:
            return await test(client)
        finally:
            await client.close()
    return asyncio.run(go())


def test_the_library_is_a_thousand_distinct_amides():
    rows = library.compounds()
    assert len(rows) == 1000 and len({r[4] for r in rows}) == 1000 and len({r[0] for r in rows}) == 1000
    assert rows[0] == ["MT-0001", "benzoic", "benzylamine", "aryl", "O=C(c1ccccc1)NCc1ccccc1"]
    assert {r[3] for r in rows} == {"aryl", "heteroaryl", "alkyl"}


def test_a_visitor_gets_visidata_on_the_library():
    async def test(client):
        async with Visitor(client) as visitor:
            screen = await visitor.read(quiet=2)
            assert "1000 rows" in screen and "MT-0001" in screen and "smiles" in screen
            assert len(live.sessions) == 1
            home = next(iter(live.sessions)).home
        await asyncio.sleep(0.5)
        assert not live.sessions and not os.path.exists(home)  # gone with the socket, home and all
    run(test)


@pytest.mark.parametrize("keys", ["=", "g=", "\x18", "g\x18", "\x13", "o", "!", "z|", "\x1a", "\x0f"])
def test_python_shell_files_and_suspending_are_refused(keys):
    # expression column, set by expression, eval, exec, save, open, shell, select by expression, suspend, editor
    async def test(client):
        async with Visitor(client) as visitor:
            await visitor.read(quiet=2)
            screen = await visitor.press(keys)
            if "Enter" in screen or ": " in screen.splitlines()[-1]:  # anything that asked for input gets something to run
                screen = await visitor.press("__import__('os').system('touch /tmp/owned')\r")
            assert "1000 rows" in screen  # still on the sheet, not suspended or quit
            assert not os.path.exists("/tmp/owned")
            session = next(iter(live.sessions))
            assert session.process.poll() is None
    run(test)


def test_the_lockdown_leaves_no_way_to_run_code():
    """Inside a VisiData configured as the live demo is, with the lockdown loaded: what is left, and what fails."""
    check = """
import sys, os, subprocess
sys.path[:0] = ['/app', '/app/demo']
import visidata
from visidata import vd, Sheet
import molterm_visidata
import lockdown
names = set(vd.commands)
for name in ('exec-python', 'exec-shell', 'addcol-expr', 'save-sheet', 'open-file', 'options-global', 'sheets-all', 'suspend',
             'pyobj-row', 'syscopy-cell', 'import-python', 'setcol-expr', 'select-expr', 'search-expr', 'macro-record'):
    assert name not in names, name
for name in ('addcol-molecule', 'addcol-molprops', 'open-molecule', 'sort-asc', 'freq-col', 'search-col', 'go-down', 'quit-sheet'):
    assert name in names, name
for call in (lambda: os.system('true'), lambda: subprocess.run(['true']), lambda: os.popen('true'), lambda: os.execvp('true', ['true'])):
    try:
        call()
        raise SystemExit('ran a process')
    except PermissionError:
        pass
try:
    Sheet('s').evalExpr('1+1')
    raise SystemExit('evaluated')
except Exception as error:
    assert 'Not in this online demo' in str(error), error
os.environ['MOLTERM_URL'] = 'x'
vd.options.molterm_url = 'http://169.254.169.254'
assert molterm_visidata.server_url() != 'http://169.254.169.254'
print('ok', len(names))
"""
    done = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.startswith("ok"), done.stderr[-2000:]


def test_sessions_are_limited_in_number_and_by_address(monkeypatch):
    monkeypatch.setattr(live, "MAX_SESSIONS", 1)

    async def test(client):
        async with Visitor(client) as first:
            await first.read(quiet=2)
            async with Visitor(client) as second:
                screen = await second.read(quiet=1)
                assert "All 1 sessions are in use" in screen
            assert len(live.sessions) == 1
    run(test)


def test_a_session_opened_from_another_site_is_refused():
    async def test(client):
        async with Visitor(client, origin=False) as visitor:
            assert "only be opened from its own page" in await visitor.read(quiet=1)
            assert not live.sessions
    run(test)


def test_an_idle_session_ends_and_says_so(monkeypatch):
    monkeypatch.setattr(live, "IDLE", 1)

    async def test(client):
        async with Visitor(client) as visitor:
            await visitor.read(quiet=2)
            screen = await visitor.read(quiet=8, longest=12)
            assert "without a key" in screen
            process = next(iter(live.sessions), None)
        await asyncio.sleep(0.5)
        assert not live.sessions and (process is None or process.process.poll() is not None)
    run(test)


@needs_server
def test_structures_and_properties_for_the_thousand():
    async def test(client):
        async with Visitor(client) as visitor:
            screen = await visitor.read(quiet=3)
            assert "smiles_structure" in screen  # there from the start
            assert visitor.raw.count(b"a=p,") >= 3  # pictures placed in their cells
            started = time.monotonic()
            screen = await visitor.press("\x1by", quiet=3)
            assert "formula" in screen and "MW" in screen
            assert time.monotonic() - started < 30
    run(test)


def test_the_page_and_script_are_revalidated():
    async def test(client):
        for path in ("/live", "/live/static/live.js"):
            response = await client.get(path)
            assert response.status == 200 and response.headers["Cache-Control"] == "no-cache"
    run(test)


def test_the_visitors_address_is_the_one_traefik_added():
    class Request:
        remote = "172.18.0.2"
        def __init__(self, forwarded):
            self.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    assert live._address(Request("1.2.3.4")) == "1.2.3.4"
    assert live._address(Request("6.6.6.6, 1.2.3.4")) == "1.2.3.4"  # a visitor's own header can't pick another address
    assert live._address(Request("")) == "172.18.0.2"
