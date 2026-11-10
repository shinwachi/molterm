// The agent page. restty is the terminal; the TUI drawn in it is Python (agent/tui/molterm_tui), run in the browser by Pyodide. This
// script only connects the pieces: the terminal's keys go to the TUI, the TUI's output goes to the terminal, and the session's
// events, streamed from the server, go to the TUI. Everything the user sees inside the terminal is decided in Python.
// What any host of the TUI needs (the bridge, Shift+Enter) and the choices that travel (copying, focus) are in bridge.js; what's
// here is this page's own.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";
import { loadPyodide } from "/vendor/pyodide/pyodide.mjs";
import { api, copyText, createBridge, focusPolicy, sendShiftEnter } from "/static/bridge.js";

const box = document.getElementById("terminal"), state = document.getElementById("state");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let terminal = null, pane = null, app = null, session = null;
let cell = null, grid = null;  // a cell's size in device pixels, at cell.ratio, and the grid the terminal has

function show(text, tone = "") {
    state.lastChild.textContent = text;
    state.className = `chip ${tone}`;
}

// restty sizes its canvas in whole cells, some time after it's asked to
async function settled(cols, rows) {
    let last = "";
    for (let tries = 0; tries < 100; tries++) {
        await sleep(40);
        const { width, height } = pane.canvas;
        const now = `${width}x${height}`;
        if (width && width % cols === 0 && height % rows === 0 && now === last) return;
        last = now;
    }
}

async function resize(cols, rows) {
    pane.resize(cols, rows);
    await settled(cols, rows);
    grid = { cols, rows };
    cell = { width: pane.canvas.width / cols, height: pane.canvas.height / rows, ratio: window.devicePixelRatio || 1 };
}

// The grid that fills the box. A cell's size in device pixels follows the zoom (devicePixelRatio), so when that has changed since the
// cell was measured, the current grid is drawn again at the new ratio first, to measure it afresh.
async function fit() {
    const ratio = window.devicePixelRatio || 1;
    if (ratio !== cell.ratio) await resize(grid.cols, grid.rows);
    const cols = Math.max(40, Math.floor(box.clientWidth * ratio / cell.width));
    const rows = Math.max(12, Math.floor(box.clientHeight * ratio / cell.height));
    if (cols !== grid.cols || rows !== grid.rows) await resize(cols, rows);
    return grid;
}

// one fit at a time; a change during a fit gets one more after it
let fitting = null, again = false;
function refit() {
    if (fitting) { again = true; return; }
    fitting = (async () => {
        do {
            again = false;
            const { cols, rows } = await fit();
            app?.resize(cols, rows);
        } while (again);
    })().catch(error => console.error(error)).finally(() => { fitting = null; });
}

// The TUI is the terminal's program, reached through a PTY that is this page: what the TUI writes comes out of it, so the terminal
// reads its modes (mouse reporting, bracketed paste) as from any program, and what the terminal sends (keys, pastes, mouse reports,
// replies) goes into the TUI.
const pty = {
    callbacks: null,
    connect({ callbacks }) { this.callbacks = callbacks; callbacks.onConnect?.(); },
    disconnect() { const callbacks = this.callbacks; this.callbacks = null; callbacks?.onDisconnect?.(); },
    isConnected() { return this.callbacks !== null; },
    sendInput(data) { app?.key(data); return true; },
    resize() {},
    write(text) { this.callbacks?.onData(text); },
};

async function startTerminal() {
    terminal = new Restty({
        root: box,
        surface: { shortcuts: false, searchUi: false, defaultContextMenu: false },
        terminal: {
            // the page sizes the terminal (fit), so that the TUI and the terminal always agree on the grid
            fontSize: 14, ligatures: false, showResizeOverlay: false, autoResize: false,
            theme: parseGhosttyTheme("background = #161615\nforeground = #f0efec\ncursor-color = #f0efec\n"),
            fonts: [{ url: "/vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                    { url: "/vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
        },
        services: () => ({ ptyTransport: pty }),
    });
    pane = terminal.getActivePane();
    const { runtime } = pane;
    while (!["ready", "failed"].includes(runtime.lifecycle.state())) await sleep(30);
    if (runtime.lifecycle.state() !== "ready") throw new Error("this browser could not start the terminal (it needs WebGL2 or WebGPU)");
    await document.fonts?.ready;
    await resize(100, 40);
    terminal.connectPty("");
    return fit();
}

async function startPython() {
    const pyodide = await loadPyodide({ indexURL: "/vendor/pyodide/" });
    const { files } = await (await fetch("/tui/manifest.json")).json();
    for (const name of files) {
        const path = `/home/pyodide/${name}`;
        pyodide.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
        pyodide.FS.writeFile(path, await (await fetch(`/tui/${name}`, { cache: "no-store" })).text());
    }
    pyodide.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
    return pyodide;
}

function listen() {
    const source = new EventSource(`/api/v1/sessions/${session}/events`);
    source.onmessage = message => app.event(message.data);
    source.onopen = () => show(`session ${session.slice(0, 8)}`, "ok");
    source.onerror = () => show("reconnecting…", "err");  // EventSource tries again by itself, from the last event it had
}

try {
    show("loading Python and the terminal…");
    const [size, pyodide] = await Promise.all([startTerminal(), startPython()]);
    // ?session=<id> joins a session an agent of its own made (see TUTORIAL.md); otherwise the server starts one with its agent,
    // and with ?tutorial, a tutor that watches it and chimes in
    const params = new URLSearchParams(location.search);
    const joining = params.get("session"), tutorial = params.has("tutorial");
    const mode = document.getElementById("mode");
    show(joining ? "joining the session…" : "starting a session…");
    if (joining) {
        const response = await fetch(`/api/v1/sessions/${encodeURIComponent(joining)}`);
        const info = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(info.error?.message || "no such session");
        session = joining;
        // a session another program made, its agent its own: &tutorial gives it the server's tutor, which the agent needn't know of
        if (tutorial && !info.tutor) await api(`/api/v1/sessions/${encodeURIComponent(session)}/tutor`);
        if (mode && (tutorial || info.tutor)) mode.hidden = true;  // there's no leaving it; /tutor hides it
        else if (mode) mode.href = `/?session=${encodeURIComponent(session)}&tutorial`;
    } else {
        session = (await api("/api/v1/sessions", tutorial ? { tutor: true } : undefined)).id;
        if (mode && tutorial) { mode.textContent = "Leave the tutorial"; mode.href = "/"; }
    }
    const { App } = pyodide.pyimport("molterm_tui");
    const bridge = createBridge({
        session, app: () => app, cell: () => cell, write: text => pty.write(text),
        copy: text => copyText(text, { refocus: () => terminal.focus?.() }),
    });
    app = App(bridge, size.cols, size.rows);
    app.start();
    listen();
    setInterval(() => app.tick(), 120);
    new ResizeObserver(refit).observe(box);
    window.addEventListener("resize", refit);  // a zoom changes the ratio, and not always the box
    terminal.focus?.();
    sendShiftEnter({ box, key: data => app.key(data) });
    // This page is nothing but the terminal, and nothing else on it wants the keyboard, so every click is a click on it: one on the
    // top bar, on the note under it, or in the frame's margin would otherwise leave a page that has quietly stopped listening.
    focusPolicy("page", { box, focus: () => terminal.focus?.() });
} catch (error) {
    show("not running", "err");
    document.getElementById("note").textContent = `The page could not start: ${error.message}`;
    console.error(error);
}
