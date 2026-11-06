// The agent page. restty is the terminal; the TUI drawn in it is Python (agent/tui/molterm_tui), run in the browser by Pyodide. This
// script only connects the pieces: the terminal's keys go to the TUI, the TUI's output goes to the terminal, and the session's
// events, streamed from the server, go to the TUI. Everything the user sees inside the terminal is decided in Python.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";
import { loadPyodide } from "/vendor/pyodide/pyodide.mjs";

const box = document.getElementById("terminal"), state = document.getElementById("state");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let terminal = null, pane = null, app = null, session = null, cell = null;

function show(text, tone = "") {
    state.lastChild.textContent = text;
    state.className = `chip ${tone}`;
}

async function api(path, data) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: data === undefined ? undefined : JSON.stringify(data) });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error?.message || `${path} answered ${response.status}`);
    return result;
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

function gridFor() {
    const ratio = window.devicePixelRatio || 1;
    return { cols: Math.max(40, Math.floor(box.clientWidth * ratio / cell.width)),
             rows: Math.max(12, Math.floor(box.clientHeight * ratio / cell.height)) };
}

async function startTerminal() {
    terminal = new Restty({
        root: box,
        surface: { shortcuts: false, searchUi: false, defaultContextMenu: false },
        terminal: {
            fontSize: 14, ligatures: false, showResizeOverlay: false,
            theme: parseGhosttyTheme("background = #161615\nforeground = #f0efec\ncursor-color = #f0efec\n"),
            fonts: [{ url: "/vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                    { url: "/vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
        },
        // keys and pastes go to the TUI rather than straight to the screen; what the TUI writes ("pty") is shown
        services: () => ({
            beforeInput: ({ text, source }) => {
                if (source === "pty") return text;
                if (app && (source === "key" || source === "paste")) {
                    app.key(source === "paste" ? `\x1b[200~${text}\x1b[201~` : text);
                }
                return null;
            },
        }),
    });
    pane = terminal.getActivePane();
    const { runtime } = pane;
    while (!["ready", "failed"].includes(runtime.lifecycle.state())) await sleep(30);
    if (runtime.lifecycle.state() !== "ready") throw new Error("this browser could not start the terminal (it needs WebGL2 or WebGPU)");
    await document.fonts?.ready;
    pane.resize(100, 40);
    await settled(100, 40);
    cell = { width: pane.canvas.width / 100, height: pane.canvas.height / 40 };
    const { cols, rows } = gridFor();
    pane.resize(cols, rows);
    await settled(cols, rows);
    return { cols, rows };
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

// what the Python TUI calls to reach the terminal and the server
const bridge = {
    write: text => terminal.sendInput(text, "pty"),
    post_message: (text, cols) => api(`/api/v1/sessions/${session}/messages`, { text, cols })
        .catch(error => app?.event(JSON.stringify({ type: "error", message: error.message }))),
    post_tool_result: (id, json) => api(`/api/v1/sessions/${session}/tool_results`, { id, result: JSON.parse(json) })
        .catch(error => app?.event(JSON.stringify({ type: "error", message: error.message }))),
    interrupt: () => api(`/api/v1/sessions/${session}/interrupt`).catch(() => {}),
};

function listen() {
    const source = new EventSource(`/api/v1/sessions/${session}/events`);
    source.onmessage = message => app.event(message.data);
    source.onopen = () => show(`session ${session.slice(0, 8)}`, "ok");
    source.onerror = () => show("reconnecting…", "err");  // EventSource tries again by itself, from the last event it had
}

try {
    show("loading Python and the terminal…");
    const [size, pyodide] = await Promise.all([startTerminal(), startPython()]);
    show("starting a session…");
    session = (await api("/api/v1/sessions")).id;
    const { App } = pyodide.pyimport("molterm_tui");
    app = App(bridge, size.cols, size.rows);
    app.start();
    listen();
    setInterval(() => app.tick(), 120);
    new ResizeObserver(async () => {
        const { cols, rows } = gridFor();
        pane.resize(cols, rows);
        app.resize(cols, rows);
    }).observe(box);
    terminal.focus?.();
    box.addEventListener("click", () => terminal.focus?.());
} catch (error) {
    show("not running", "err");
    document.getElementById("note").textContent = `The page could not start: ${error.message}`;
    console.error(error);
}
