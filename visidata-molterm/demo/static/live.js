// The live demo: restty, the web terminal, connected over a WebSocket to a VisiData of the visitor's own on the server. restty sends the
// keys and the grid's size, and prints what comes back, Kitty graphics included; this page only starts it and shows whether it's connected.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";

const element = document.getElementById("terminal");
const state = document.getElementById("state"), connectButton = document.getElementById("connect");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const socketUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/live/ws`;

function show(text, ok) {
    state.textContent = text;
    state.className = ok ? "ok" : "off";
}

async function capacity() {
    try {
        const result = await (await fetch("/live/status")).json();
        document.getElementById("idle").textContent = result.idle_minutes;
        document.getElementById("longest").textContent = result.longest_minutes;
        document.getElementById("capacity").textContent = `${result.sessions} of ${result.max} sessions are in use.`;
    } catch (error) { /* the page works without it */ }
}

const terminal = new Restty({
    root: element,
    surface: { shortcuts: false, searchUi: false, defaultContextMenu: false },
    terminal: {
        fontSize: 14, ligatures: false, showResizeOverlay: false,
        theme: parseGhosttyTheme("background = #161615\nforeground = #f0efec\ncursor-color = #f0efec\n"),
        fonts: [{ url: "/vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                { url: "/vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
    },
});
const pane = terminal.getActivePane();

async function ready() {
    const { runtime } = pane;
    while (!["ready", "failed"].includes(runtime.lifecycle.state())) await sleep(30);
    return runtime.lifecycle.state() === "ready";
}

let wasConnected = false;

// restty fits its grid to the box by itself, once its font has come; it sends that grid to VisiData when it connects, and again
// whenever the box changes. So connect once its canvas has stopped changing size.
async function settled() {
    await document.fonts?.ready;
    let last = "", steady = 0;
    for (let tries = 0; tries < 60 && steady < 3; tries++) {
        await sleep(100);
        const now = `${pane.canvas.width}x${pane.canvas.height}`;
        steady = now === last && pane.canvas.width > 0 ? steady + 1 : 0;
        last = now;
    }
}

async function connect() {
    connectButton.disabled = true;
    wasConnected = false;  // so that the new connection is reported, however quickly the old one went
    terminal.disconnectPty?.();
    // restty refuses a new connection while the last one is still closing
    for (let tries = 0; tries < 40 && pane.isPtyConnected?.(); tries++) await sleep(50);
    await sleep(300);
    pane.sendInput?.("\x1b_Ga=d,d=A,q=2\x1b\\\x1bc", "pty");  // a clean screen, without the last session's pictures
    try {
        terminal.connectPty(socketUrl);
    } catch (error) {
        show(`Could not connect: ${error.message}`, false);
    }
    connectButton.disabled = false;
    show("Connecting…", false);
    terminal.focus?.();
    capacity();
}

// restty reports its connection only as an event inside it, so the page asks it now and then
setInterval(() => {
    const connected = terminal.isPtyConnected?.() ?? pane.isPtyConnected?.();
    if (connected && !wasConnected) show("Connected. Click the screen to type.", true);
    if (!connected && wasConnected) { show("The session has ended.", false); capacity(); }
    wasConnected = Boolean(connected);
}, 700);

connectButton.onclick = connect;
for (const button of document.querySelectorAll("button[data-alt]")) {
    button.onclick = () => {
        pane.sendKeyInput("\x1b" + button.dataset.alt);  // Alt and the letter, as a terminal sends it
        terminal.focus?.();
    };
}
// restty takes keys through an input of its own, which a click on the canvas focuses; this covers the frame around it
document.getElementById("screen").addEventListener("click", () => terminal.focus?.());
// Alt and a letter, as terminals send it: Escape, then the letter. VisiData's molterm keys are Alt+m, Alt+y, Alt+o and Alt+t, and a
// browser can otherwise hand restty the letter alone, or a character the Alt key composes.
document.addEventListener("keydown", event => {
    if (!event.altKey || event.ctrlKey || event.metaKey || !element.contains(document.activeElement) || !/^Key[A-Z]$/.test(event.code)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const letter = event.code.slice(3).toLowerCase();
    pane.sendKeyInput("\x1b" + (event.shiftKey ? letter.toUpperCase() : letter));
}, true);

if (await ready()) {
    await settled();
    connect();
} else {
    show("This browser could not start the terminal, which needs WebGL2 or WebGPU.", false);
    connectButton.disabled = true;
}
