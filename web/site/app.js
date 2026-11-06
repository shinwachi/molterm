// The demo page: RDKit.js and molterm's renderer (molterm.js) draw a molecule, and restty, a web terminal, shows the result as a shell
// would. There is no server behind this: everything comes from the page's own files.
import loadRDKit from "./rdkit.js";
import { Restty, parseGhosttyTheme } from "./vendor/restty.esm.js";
import { parse, render, pictureSvg, kittySequence, ParseError, MAX_ATOMS } from "./molterm.js";
import { GROUPS, DEFAULT } from "./samples.js";

const $ = selector => document.querySelector(selector);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const input = $("#input"), status = $("#status"), engine = $("#engine");
const quoted = text => "'" + text.replaceAll("'", "'\\''") + "'";
const canvas = document.createElement("canvas");  // RDKit draws here, unseen, for the renderer to read

let RDKit = null, terminal = null, pane = null, grid = { cols: 80, rows: 30, cellWidth: 8, cellHeight: 16 };
let mode = "braille", lastText = "";

// theme: auto (the OS's), light or dark, remembered
const themeButton = $("#theme");
const themes = ["auto", "light", "dark"];
const storedTheme = () => { try { return localStorage.getItem("theme") || "auto"; } catch (e) { return "auto"; } };
themeButton.textContent = storedTheme();
themeButton.onclick = () => {
    const next = themes[(themes.indexOf(themeButton.textContent) + 1) % themes.length];
    themeButton.textContent = next;
    if (next === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = next;
    try { if (next === "auto") localStorage.removeItem("theme"); else localStorage.setItem("theme", next); } catch (e) {}
};

// samples, as rows: the whole row is the button, and the selected one takes a faint tint
for (const [group, items] of GROUPS) {
    const body = document.createElement("tbody");
    body.innerHTML = `<tr><th scope="rowgroup"></th></tr>`;
    body.querySelector("th").textContent = group;
    for (const [name, smiles, formula] of items) {
        const row = document.createElement("tr");
        row.innerHTML = `<td><button class="sample" type="button" aria-pressed="false"><span></span><span class="mono"></span></button></td>`;
        const button = row.querySelector("button");
        button.dataset.smiles = smiles;
        button.children[0].textContent = name;
        button.children[1].textContent = formula;
        button.onclick = () => { input.value = smiles; mark(); draw(); };
        body.append(row);
    }
    $("#samples").append(body);
}
const samples = [...document.querySelectorAll(".sample")];
const mark = () => samples.forEach(b => b.setAttribute("aria-pressed", b.dataset.smiles === input.value.trim()));

// restty sizes its canvas in whole cells, some time after it's asked to
async function settled(cols, rows) {
    let last = "";
    for (let tries = 0; tries < 100; tries++) {
        await sleep(40);
        const { width, height } = pane.canvas;
        const now = `${width}x${height}`;
        if (width && (!cols || (width % cols === 0 && height % rows === 0)) && now === last) return;
        last = now;
    }
}

async function startTerminal() {
    const box = $("#terminal");
    terminal = new Restty({
        root: box,
        surface: { shortcuts: false, searchUi: false, defaultContextMenu: false },
        terminal: {
            fontSize: 15, ligatures: false, showResizeOverlay: false,
            theme: parseGhosttyTheme("background = #ffffff\nforeground = #1d1d1b\ncursor-color = #ffffff\n" +
                                     "palette = 1=#c62828\npalette = 2=#2d7a5e\npalette = 3=#b45309\npalette = 4=#2a78d6\npalette = 5=#9c27b0\n" +
                                     "palette = 6=#00838f\npalette = 8=#8a8980\n"),
            fonts: [{ url: "vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                    { url: "vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
        },
        // nothing typed here goes anywhere, and the terminal's own answers are dropped
        services: () => ({ beforeInput: ({ text, source }) => (source === "pty" ? text : null) }),
    });
    pane = terminal.getActivePane();
    const { runtime } = pane;
    while (!["ready", "failed"].includes(runtime.lifecycle.state())) await sleep(30);
    if (runtime.lifecycle.state() !== "ready") throw new Error("this browser could not start the terminal, which needs WebGL2 or WebGPU");
    await document.fonts?.ready;
    // a known grid gives the size of a cell, in the device's pixels; then as many cells as fit the box
    pane.resize(100, 40);
    await settled(100, 40);
    const ratio = window.devicePixelRatio || 1;
    const cellWidth = pane.canvas.width / 100, cellHeight = pane.canvas.height / 40;
    const cols = Math.max(30, Math.floor(box.clientWidth * ratio / cellWidth));
    const rows = Math.max(12, Math.floor(box.clientHeight * ratio / cellHeight));
    pane.resize(cols, rows);
    await settled(cols, rows);
    box.style.width = `${pane.canvas.width / ratio}px`;
    box.style.height = `${pane.canvas.height / ratio}px`;
    grid = { cols, rows, cellWidth, cellHeight };
}

const write = data => terminal?.sendInput(data.replaceAll("\n", "\r\n"), "pty");

function setStatus(text, error = false) {
    status.textContent = text;
    status.style.color = error ? "var(--err)" : "";
}

/** RDKit's picture, cropped to its ink, as PNG base64, and the cells it spans. */
async function picture(parsed, cols, rows) {
    const width = Math.round(cols * grid.cellWidth), height = Math.round(rows * grid.cellHeight);
    const image = new Image();
    const svg = pictureSvg(parsed, width, height, grid.cellWidth * 2.6);
    image.src = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    await image.decode();
    URL.revokeObjectURL(image.src);
    const full = document.createElement("canvas");
    full.width = width; full.height = height;
    const context = full.getContext("2d", { willReadFrequently: true });
    context.drawImage(image, 0, 0);
    const { data } = context.getImageData(0, 0, width, height);
    let top = height, bottom = -1, left = width, right = -1;
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            const i = (y * width + x) * 4;
            if (data[i] < 250 || data[i + 1] < 250 || data[i + 2] < 250) {
                if (y < top) top = y; if (y > bottom) bottom = y; if (x < left) left = x; if (x > right) right = x;
            }
        }
    }
    if (bottom < 0) return null;
    const margin = Math.round(grid.cellWidth / 2);
    left = Math.max(0, left - margin); top = Math.max(0, top - margin);
    right = Math.min(width - 1, right + margin); bottom = Math.min(height - 1, bottom + margin);
    const cropped = document.createElement("canvas");
    cropped.width = right - left + 1; cropped.height = bottom - top + 1;
    cropped.getContext("2d").drawImage(full, left, top, cropped.width, cropped.height, 0, 0, cropped.width, cropped.height);
    const base64 = cropped.toDataURL("image/png").split(",")[1];
    return { base64, cols: Math.ceil(cropped.width / grid.cellWidth), rows: Math.ceil(cropped.height / grid.cellHeight) };
}

async function draw() {
    const text = input.value;
    if (!RDKit || !text.trim()) return;
    const wanted = Number($("#size").value);
    const cols = Math.min(wanted, grid.cols - 1), rows = Math.max(8, Math.round(cols * 3 / 8));
    const started = performance.now();
    let parsed;
    try {
        parsed = parse(RDKit, text);
    } catch (error) {
        if (!(error instanceof ParseError)) throw error;
        write(`\x1b[90m$\x1b[0m molterm ${quoted(text.trim().split("\n")[0])}\n\x1b[31m${error.message}\x1b[0m\n\n`);
        setStatus(error.message, true);
        return;
    }
    try {
        const shown = text.includes("M  END") ? "molecule.mol" : quoted(text.trim());
        const flag = { braille: " -m braille", ascii: " -m ascii", picture: " -m kitty" }[mode];
        write(`\x1b[90m$\x1b[0m molterm${flag} -c ${cols} ${shown}\n`);
        if (mode === "picture") {
            const result = await picture(parsed, cols, rows);
            if (result) write(kittySequence(result.base64, result.cols) + "\n");
            lastText = "";
        } else {
            const result = render(parsed, { cols, rows, mode, canvas });
            write(result.ansi + "\n");
            lastText = result.text;
        }
        const took = Math.round(performance.now() - started);
        write(`\x1b[90m${parsed.formula} · ${parsed.atoms.length} atoms\x1b[0m\n\n`);
        setStatus(`Drawn in ${took} ms, in this browser.`);
        $("#copy").disabled = !lastText;
        history.replaceState(null, "", "#" + new URLSearchParams({ smiles: text.trim(), mode }));
    } finally {
        parsed.mol.delete();
    }
}

$("#form").onsubmit = event => { event.preventDefault(); draw(); };
input.oninput = mark;
$("#size").onchange = draw;
for (const button of document.querySelectorAll("#modes button")) {
    button.onclick = () => {
        mode = button.dataset.mode;
        document.querySelectorAll("#modes button").forEach(b => b.setAttribute("aria-checked", b === button));
        draw();
    };
}
$("#clear").onclick = () => { write("\x1b_Ga=d,d=A,q=2\x1b\\\x1b[2J\x1b[3J\x1b[H"); lastText = ""; $("#copy").disabled = true; };
$("#copy").onclick = async () => {
    try {
        await navigator.clipboard.writeText(lastText);
        setStatus("Copied the drawing as plain text.");
    } catch (error) {
        setStatus("The browser did not allow copying.", true);
    }
};

// a link can name a molecule and a display: #smiles=CCO&mode=ascii
const linked = new URLSearchParams(location.hash.slice(1));
input.value = linked.get("smiles") || GROUPS.flatMap(([, items]) => items).find(([name]) => name === DEFAULT)[1];
if (["braille", "ascii", "picture"].includes(linked.get("mode"))) {
    mode = linked.get("mode");
    document.querySelectorAll("#modes button").forEach(b => b.setAttribute("aria-checked", b.dataset.mode === mode));
}
mark();

try {
    setStatus("Loading RDKit (WebAssembly, 7 MB) and the terminal…");
    const [rdkit] = await Promise.all([loadRDKit(), startTerminal()]);
    RDKit = rdkit;
    engine.lastChild.textContent = `RDKit ${RDKit.version()} · WebAssembly`;
    engine.querySelector("i").style.background = "var(--ok)";
    $("#rdkit-version").textContent = RDKit.version();
    write(`\x1b[90m# molterm in the browser: up to ${MAX_ATOMS} atoms, drawn by RDKit ${RDKit.version()} in WebAssembly\x1b[0m\n\n`);
    await draw();
} catch (error) {
    engine.lastChild.textContent = "not running";
    engine.querySelector("i").style.background = "var(--err)";
    setStatus(`The demo could not start: ${error.message}`, true);
}
