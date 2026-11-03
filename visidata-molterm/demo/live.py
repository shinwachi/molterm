"""The live demo: VisiData with the plugin, on a library of 1000 compounds, for anyone with a browser.

Each visitor's WebSocket gets its own VisiData in a pseudo-terminal, started by demo/sandbox.py with resource limits and
demo/live.visidatarc, whose lockdown leaves only commands that work on the sheet in memory. The page's web terminal, restty, speaks
the protocol here: JSON messages {"type": "input", "data"} and {"type": "resize", "cols", "rows"} in, the terminal's bytes out.

Sessions are few and short: at most LIVE_MAX_SESSIONS at once and LIVE_PER_ADDRESS from one address, ended after LIVE_IDLE seconds
without input or LIVE_LONGEST in all. Each one's home is a fresh folder under /tmp, removed when it ends. The container is locked
down too (see docker-compose.yml).

    python demo/live.py            # serves /live on port 8940
"""
import asyncio
import fcntl
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

from aiohttp import WSMsgType, web

import library

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("LIVE_PORT", "8940"))
MAX_SESSIONS = int(os.environ.get("LIVE_MAX_SESSIONS", "4"))
PER_ADDRESS = int(os.environ.get("LIVE_PER_ADDRESS", "2"))
IDLE = int(os.environ.get("LIVE_IDLE", str(10 * 60)))
LONGEST = int(os.environ.get("LIVE_LONGEST", str(30 * 60)))
WORK = os.environ.get("LIVE_WORK", os.path.join(tempfile.gettempdir(), "molterm-live"))
LIBRARY = os.path.join(WORK, "compounds.csv")
COLS_RANGE, ROWS_RANGE = (40, 400), (12, 160)  # a large screen at a small font is about 300 by 100
MAX_MESSAGE = 64 * 1024  # a paste can be long, but not this long
MAX_QUEUED = 256  # chunks of output waiting for a slow browser before the session is dropped


def _clamp(value, limits):
    try:
        return min(max(int(value), limits[0]), limits[1])
    except (TypeError, ValueError):
        return None


def _notice(text):
    """A line printed into the visitor's terminal, below whatever VisiData left there."""
    return f"\x1b[0m\x1b[?1049l\r\n\x1b[1m{text}\x1b[0m\r\n".encode()


class Session:
    def __init__(self, ws, address):
        self.ws, self.address = ws, address
        self.process = self.master = self.home = None
        self.started = self.last_input = time.monotonic()
        self.output = asyncio.Queue()
        self.ended = asyncio.Event()
        self.reason = ""

    def start(self, cols, rows):
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.home = tempfile.mkdtemp(prefix="session-", dir=WORK)
        for folder in (".local/share/visidata", ".config/visidata", ".cache/visidata"):  # two of VisiData's threads race to make them
            os.makedirs(os.path.join(self.home, folder), exist_ok=True)
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home, "TERM": "molterm-kitty", "LANG": "C.UTF-8",
                       "MOLTERM_URL": os.environ.get("MOLTERM_URL", "http://molterm.localhost"), "EDITOR": "false",
                       "VISUAL": "false", "PAGER": "false", "ESCDELAY": "25", "PYTHONDONTWRITEBYTECODE": "1"}
        self.process = subprocess.Popen([sys.executable, os.path.join(HERE, "sandbox.py"), LIBRARY], stdin=slave, stdout=slave,
                                        stderr=slave, cwd=self.home, env=environment, start_new_session=True, close_fds=True)
        os.close(slave)
        self.master = master
        os.set_blocking(master, False)
        asyncio.get_running_loop().add_reader(master, self._readable)

    def _readable(self):
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError:  # the far end closed: VisiData has quit
            data = b""
        if not data:
            self.end("VisiData has quit. Reload the page for a new session.")
            return
        if self.output.qsize() > MAX_QUEUED:
            self.end("The connection was too slow to keep up.")
            return
        self.output.put_nowait(data)

    def type(self, text):
        if self.master is not None:
            self.last_input = time.monotonic()
            os.write(self.master, text.encode("utf-8", "replace"))

    def resize(self, cols, rows):
        if self.master is not None:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def end(self, reason):
        if not self.ended.is_set():
            self.reason = reason
            self.ended.set()

    def close(self):
        """Stop VisiData and everything it started, and remove its home."""
        if self.master is not None:
            asyncio.get_running_loop().remove_reader(self.master)
            os.close(self.master)
            self.master = None
        if self.process and self.process.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.process.pid, sig)
                    self.process.wait(timeout=2)
                    break
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    continue
        if self.home:
            shutil.rmtree(self.home, ignore_errors=True)


sessions = set()


def _address(request):
    # the connection comes from Traefik, which adds the visitor's address last; anything before it the visitor could have written
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[-1].strip() or request.remote or "?"


def _same_origin(request):
    origin = request.headers.get("Origin", "")
    host = request.headers.get("X-Forwarded-Host") or request.host
    return origin.split("://", 1)[-1] == host


async def websocket(request):
    ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE, heartbeat=30)
    await ws.prepare(request)
    address = _address(request)
    refusal = None
    if not _same_origin(request):
        refusal = "This session can only be opened from its own page."
    elif len(sessions) >= MAX_SESSIONS:
        refusal = f"All {MAX_SESSIONS} sessions are in use. Please try again in a few minutes."
    elif sum(s.address == address for s in sessions) >= PER_ADDRESS:
        refusal = f"There are already {PER_ADDRESS} sessions from your address. Close one to open another."
    if refusal:
        await ws.send_bytes(_notice(refusal))
        await ws.close(code=1013)
        return ws
    session = Session(ws, address)
    sessions.add(session)
    sender = asyncio.create_task(_send(session))
    watchdog = asyncio.create_task(_watch(session))
    receiver = asyncio.create_task(_receive(session))
    try:
        await session.ended.wait()
        while not session.output.empty() and not ws.closed:
            await ws.send_bytes(session.output.get_nowait())
        if session.reason and not ws.closed:
            await ws.send_bytes(_notice(session.reason))
    finally:
        for task in (sender, watchdog, receiver):
            task.cancel()
        sessions.discard(session)
        session.close()
        if not ws.closed:
            await ws.close()
    return ws


async def _receive(session):
    """The visitor's keys and the terminal's size. VisiData starts once the size is known, or after a moment at a usual one."""
    ws = session.ws
    try:
        first = await asyncio.wait_for(ws.receive(), timeout=3)
    except asyncio.TimeoutError:
        first = None
    messages = [first] if first is not None else []
    size = (120, 34)
    if first is not None and first.type == WSMsgType.TEXT:
        try:
            message = json.loads(first.data)
            if message.get("type") == "resize":
                size = (_clamp(message.get("cols"), COLS_RANGE) or 120, _clamp(message.get("rows"), ROWS_RANGE) or 34)
                messages = []
        except (ValueError, AttributeError):
            pass
    session.start(*size)
    try:
        for message in messages:
            _handle(session, message)
        async for message in ws:
            if not _handle(session, message):
                break
    finally:
        session.end("")


def _handle(session, message):
    """Act on one message; False when the socket has closed."""
    if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
        return False
    if message.type != WSMsgType.TEXT:
        return True
    try:
        data = json.loads(message.data)
    except ValueError:
        return True
    if not isinstance(data, dict):
        return True
    if data.get("type") == "input" and isinstance(data.get("data"), str):
        session.type(data["data"])
    elif data.get("type") == "resize":
        cols, rows = _clamp(data.get("cols"), COLS_RANGE), _clamp(data.get("rows"), ROWS_RANGE)
        if cols and rows:
            session.resize(cols, rows)
    return True


async def _send(session):
    while True:
        data = await session.output.get()
        await session.ws.send_bytes(data)


async def _watch(session):
    while not session.ended.is_set():
        await asyncio.sleep(5)
        now = time.monotonic()
        if now - session.last_input > IDLE:
            session.end(f"The session ended after {IDLE // 60} minutes without a key. Reload the page for a new one.")
        elif now - session.started > LONGEST:
            session.end(f"Sessions last {LONGEST // 60} minutes. Reload the page for a new one.")


async def page(request):
    return web.FileResponse(os.path.join(HERE, "templates", "live.html"))


async def status(request):
    return web.json_response({"sessions": len(sessions), "max": MAX_SESSIONS, "idle_minutes": IDLE // 60, "longest_minutes": LONGEST // 60,
                              "compounds": len(library.compounds())})


async def health(request):
    return web.json_response({"status": "ok" if os.path.exists(LIBRARY) else "no library", "sessions": len(sessions)},
                             status=200 if os.path.exists(LIBRARY) else 503)


@web.middleware
async def _revalidate(request, handler):
    """The page and its script are checked with the server each time, so that a browser never runs an old script on a new page."""
    response = await handler(request)
    if not isinstance(response, web.WebSocketResponse):
        response.headers["Cache-Control"] = "no-cache"
    return response


async def _shutdown(app):
    for session in list(sessions):
        session.close()


def make_app():
    os.makedirs(WORK, exist_ok=True)
    library.write(LIBRARY)
    app = web.Application(middlewares=[_revalidate])
    app.add_routes([web.get("/live", page), web.get("/live/", page), web.get("/live/ws", websocket), web.get("/live/status", status),
                    web.get("/live/health", health), web.static("/live/static", os.path.join(HERE, "static"))])
    app.on_shutdown.append(_shutdown)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), port=PORT, access_log=None)
