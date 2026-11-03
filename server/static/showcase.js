// Plays the demo from /api/v1/showcase in a web terminal with Kitty graphics. All the frames are real output from the server: this only prints
// them in turn and moves a pointer to show where the input went. Nothing here is typed by the viewer.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";

const WANTED_COLS = 120, MAX_ROWS = 38, ADVANCE = 0.602;  // DejaVu Sans Mono's width of a cell, in ems
// restty keeps about 10 MB of decoded images and drops the oldest beyond that, so the server draws for cells of this width, and the terminal scales up.
const IMAGE_CELL = 7;
const MOVE = 650, PRESS = 180, KEY_SHOWN = 900, RESTART_AFTER = 6000;
const element = document.getElementById("terminal");
const caption = document.getElementById("caption"), progress = document.getElementById("progress");
const pointer = document.getElementById("pointer"), keycap = document.getElementById("keycap");
const playButton = document.getElementById("play");

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

// restty doesn't report its grid, but holds whatever it is given, and sizes its canvas in whole cells of the device's pixels.
async function measure() {
    pane.resize(100, 40);
    await sleep(120);
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
    await sleep(120);
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
    if (frame.output) write(frame.output);
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
        if (position === 0) write("\x1bc");
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
    } catch (error) {
        setPlaying(false);
        caption.textContent = `The demo could not start: ${error.message}`;
    }
} else {
    setPlaying(false);
    caption.textContent = "This browser could not start the terminal, which needs WebGL2 or WebGPU.";
}
