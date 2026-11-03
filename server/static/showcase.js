// Plays the demo from /api/v1/showcase in a web terminal with Kitty graphics. All the frames are real output from the server: this only prints
// them in turn and moves a pointer to show where the input went. Nothing here is typed by the viewer.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";

const WANTED_COLS = 120, MAX_ROWS = 38, ADVANCE = 0.602;  // DejaVu Sans Mono's width of a cell, in ems
// restty keeps about 10 MB of decoded images and drops the oldest beyond that, so the server draws for cells of this width, and the terminal scales up.
const IMAGE_CELL = 7;
const MOVE = 650, PRESS = 180, KEY_SHOWN = 900, RESTART_AFTER = 6000, SCROLL_STEP = 60;
const element = document.getElementById("terminal");
const caption = document.getElementById("caption"), progress = document.getElementById("progress");
const pointer = document.getElementById("pointer"), keycap = document.getElementById("keycap");
const playButton = document.getElementById("play");
const clipboard = document.getElementById("clipboard"), clipboardText = document.getElementById("clipboard-text");
const clipboardImage = document.getElementById("clipboard-image");
const savePng = document.getElementById("clipboard-png"), saveSvg = document.getElementById("clipboard-svg");
const COPIED = /\x1b\]52;c;([A-Za-z0-9+/=]*)\x1b\\/g;
// Kitty's clipboard protocol, which carries the pictures: a write, then pieces of each type, then an empty piece to end it
const KITTY_COPY = /\x1b\]5522;([^\x1b]*)\x1b\\/g;

// a font that gives about the same grid on any screen, since the demo is laid out for it
const FONT_SIZE = Math.min(Math.max(Math.floor(document.getElementById("frame").clientWidth / (WANTED_COLS * ADVANCE)), 10), 24);

const terminal = new Restty({
    root: element,
    surface: { shortcuts: false, searchUi: false, defaultContextMenu: false },
    terminal: {
        fontSize: FONT_SIZE, ligatures: false, showResizeOverlay: false,
        theme: parseGhosttyTheme("background = #ffffff\nforeground = #000000\ncursor-color = #ffffff\n"),
        fonts: [{ url: "/vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                { url: "/vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
    },
    services: () => ({ beforeInput: ({ text, source }) => (source === "pty" ? text : null) }),
});

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const write = data => terminal.sendInput(data, "pty");
const pane = terminal.getActivePane();
let grid = null, frames = null, generation = 0, playing = false, position = 0;

// restty applies a resize some time later, slower on a first visit. Its canvas is sized in whole cells, so a grid has taken hold when the
// canvas divides evenly into it, and stays so.
async function settled(cols, rows) {
    let last = "";
    for (let tries = 0; tries < 100; tries++) {
        await sleep(50);
        const { width, height } = pane.canvas;
        const now = `${width}x${height}`;
        if (width && width % cols === 0 && height % rows === 0 && now === last) return;
        last = now;
    }
}

// The canvas stretches to its parent, so the parent must always be the canvas's own size, or text and images come out distorted.
function fitParent() {
    const ratio = window.devicePixelRatio || 1;
    const width = `${pane.canvas.width / ratio}px`, height = `${pane.canvas.height / ratio}px`;
    if (element.style.width !== width || element.style.height !== height) {
        element.style.width = width;
        element.style.height = height;
        document.getElementById("frame").style.height = height;
    }
}

// restty doesn't report its grid, but holds whatever it is given, and sizes its canvas in whole cells of the device's pixels.
async function measure() {
    pane.resize(100, 40);
    await settled(100, 40);
    const cellWidth = pane.canvas.width / 100, cellHeight = pane.canvas.height / 40, ratio = window.devicePixelRatio || 1;
    const area = document.getElementById("frame");
    const cols = Math.floor(area.clientWidth * ratio / cellWidth), rows = Math.min(Math.floor(area.clientHeight * ratio / cellHeight), MAX_ROWS);
    const cssWidth = cellWidth / ratio, cssHeight = cellHeight / ratio;
    // the canvas stretches to its parent, so the parent must be exactly the grid's size, or the text comes out distorted
    element.style.width = `${cols * cssWidth}px`;
    element.style.height = `${rows * cssHeight}px`;
    area.style.height = element.style.height;
    document.getElementById("screen").style.height = "auto";
    document.getElementById("screen").style.minHeight = "0";
    await sleep(120);
    pane.resize(cols, rows);
    await settled(cols, rows);
    fitParent();
    setInterval(fitParent, 500);  // in case restty sizes its canvas again, as when its font arrives late
    return { cols, rows, cellWidth, cellHeight, cssWidth, cssHeight };
}

function point(col, row) {
    return `translate(${col * grid.cssWidth}px, ${row * grid.cssHeight}px)`;
}

async function move(target) {
    pointer.style.display = "block";
    pointer.style.transform = point(...target);
    await sleep(MOVE);
}

const decodeText = base64 => new TextDecoder().decode(Uint8Array.from(atob(base64), c => c.charCodeAt(0)));

function showText(text) {
    clipboardText.textContent = text.startsWith("<?xml") ? `SVG, ${(text.length / 1024).toFixed(0)} KB of text` : text;
}

// What the terminal was asked to copy, shown below the screen, as a page that plays by itself shouldn't overwrite the viewer's clipboard
function showCopies(output) {
    for (const [, text] of output.matchAll(COPIED)) {
        clipboardText.dataset.text = decodeText(text);
        showText(clipboardText.dataset.text);
        clipboardImage.hidden = savePng.hidden = saveSvg.hidden = true;
        clipboard.hidden = false;
    }
    let types = null;
    for (const [, packet] of output.matchAll(KITTY_COPY)) {
        if (packet === "type=write") {
            types = {};
        } else if (packet === "type=wdata" && types) {
            if (types["text/plain"]) {
                clipboardText.dataset.text = decodeText(types["text/plain"]);
                showText(clipboardText.dataset.text);
            }
            const png = types["image/png"], svg = types["image/svg+xml"];
            clipboardImage.hidden = savePng.hidden = !png;
            saveSvg.hidden = !svg;
            if (png) clipboardImage.src = savePng.href = `data:image/png;base64,${png}`;
            if (svg) saveSvg.href = `data:image/svg+xml;base64,${svg}`;
            types = null;
        } else if (types && packet.startsWith("type=wdata:mime=")) {
            const [meta, data] = packet.split(";");
            const mime = atob(meta.slice("type=wdata:mime=".length));
            types[mime] = (types[mime] || "") + data;
        }
    }
}

// The view moves through the terminal's history as a mouse wheel moves it, a few lines at a time, so that the scrolling is seen.
async function scrollView(lines, mine) {
    const steps = Math.ceil(Math.abs(lines) / 3);
    const { left, top, width, height } = pane.canvas.getBoundingClientRect();
    for (let step = 0; step < steps && mine === generation; step++) {
        const delta = Math.sign(lines) * Math.min(3, Math.abs(lines) - step * 3);
        pane.canvas.dispatchEvent(new WheelEvent("wheel", { deltaY: delta * grid.cssHeight, deltaMode: WheelEvent.DOM_DELTA_PIXEL,
            clientX: left + width / 2, clientY: top + height / 2, bubbles: true, cancelable: true }));
        await sleep(SCROLL_STEP);
    }
}

async function show(frame, mine) {
    caption.textContent = frame.caption;
    progress.textContent = `${position + 1} of ${frames.length}`;
    if (frame.pointer) await move(frame.pointer);
    if (mine !== generation) return;
    if (!frame.pointer && frame.key === null) pointer.style.display = "none";
    if (frame.click) {
        pointer.classList.add("clicking");
        await sleep(PRESS);
        pointer.classList.remove("clicking");
    }
    if (frame.key) {
        keycap.textContent = frame.key;
        keycap.style.display = "block";
        keycap.style.transform = pointer.style.transform + " translate(22px, 18px)";
        setTimeout(() => { keycap.style.display = "none"; }, KEY_SHOWN);
    }
    if (mine !== generation) return;
    if (frame.output) {
        showCopies(frame.output);
        write(frame.output.replace(COPIED, "").replace(KITTY_COPY, ""));
    }
    if (frame.scroll) {
        keycap.textContent = frame.scroll < 0 ? "Wheel ↑" : "Wheel ↓";
        keycap.style.display = "block";
        keycap.style.transform = point(grid.cols - 12, 1);
        await scrollView(frame.scroll, mine);
        keycap.style.display = "none";
    }
    await sleep(frame.pause);
}

function setPlaying(on) {
    playing = on;
    playButton.textContent = on ? "Stop" : "Play";
}

async function play() {
    const mine = ++generation;
    setPlaying(true);
    while (mine === generation) {
        if (position === 0) {
            write("\x1bc");
            clipboard.hidden = true;
        }
        await show(frames[position], mine);
        if (mine !== generation) return;
        position++;
        if (position === frames.length) {  // nothing carries over, so that a replay starts clean
            caption.textContent = "That's the demo. It starts again shortly.";
            await sleep(RESTART_AFTER);
            if (mine !== generation) return;
            position = 0;
        }
    }
}

function stop() {
    generation++;
    setPlaying(false);
}

async function load() {
    const query = new URLSearchParams({ cols: grid.cols, rows: grid.rows, cell_width: IMAGE_CELL, cell_height: Math.round(IMAGE_CELL * grid.cellHeight / grid.cellWidth) });
    const response = await fetch(`/api/v1/showcase?${query}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error.message);
    return result.frames;
}

// The VisiData scene comes from another service, recorded at this terminal's size the first time it's asked for, which takes half a
// minute or so: the demo plays meanwhile, and the scene joins the end once it's ready. Without the service, the demo plays without it.
async function loadScene() {
    const query = new URLSearchParams({ cols: grid.cols, rows: grid.rows });
    for (let tries = 0; tries < 60; tries++) {
        let response, result;
        try {
            response = await fetch(`/api/v1/showcase/visidata?${query}`);
            result = await response.json();
        } catch (error) {
            return;
        }
        if (response.ok) {
            appendScene(result.frames || []);
            return;
        }
        if (result.error?.code !== "recording") return;
        await sleep(5000);
    }
}

function appendScene(scene) {
    if (!scene.length) return;
    // a clean screen and no pictures before VisiData starts, as a new terminal would have
    const clean = "\x1b_Ga=d,d=A,q=2\x1b\\\x1b[0m\x1b[2J\x1b[3J\x1b[H";
    frames.push(...scene.map((frame, index) => ({
        caption: frame.caption, output: (index === 0 ? clean : "") + frame.output, pointer: null, click: false,
        key: frame.key, pause: frame.pause, scroll: 0,
    })));
}

// The site is plain HTTP, where browsers offer no navigator.clipboard, so this is the older way, which a click allows.
document.getElementById("clipboard-copy").onclick = () => {
    // the whole text, which for an SVG is more than is shown
    const shown = clipboardText.textContent;
    clipboardText.textContent = clipboardText.dataset.text || shown;
    const selection = getSelection(), range = document.createRange();
    range.selectNodeContents(clipboardText);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("copy");
    selection.removeAllRanges();
    clipboardText.textContent = shown;
};

playButton.onclick = () => {
    if (playing) { stop(); return; }
    if (frames) play();
};
document.getElementById("restart").onclick = () => {
    if (!frames) return;
    position = 0;
    pointer.style.display = "none";
    play();
};

async function ready() {
    const { runtime } = pane;
    while (!["ready", "failed"].includes(runtime.lifecycle.state())) await sleep(30);
    return runtime.lifecycle.state() === "ready";
}

if (await ready()) {
    try {
        grid = await measure();
        caption.textContent = "Preparing the demo…";
        frames = await load();
        play();
        loadScene();
    } catch (error) {
        setPlaying(false);
        caption.textContent = `The demo could not start: ${error.message}`;
    }
} else {
    setPlaying(false);
    caption.textContent = "This browser could not start the terminal, which needs WebGL2 or WebGPU.";
}
