// Plays the demo from /api/v1/showcase in a web terminal with Kitty graphics, as molterm's /showcase does. All the frames are real output
// from the server: this only prints them in turn and moves a pointer to show where the input went. Nothing here is typed by the viewer.
// The demo comes in parts: the first plays while the server draws the rest, which join the end as they arrive.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";

const WANTED_COLS = 120, MAX_ROWS = 38, ADVANCE = 0.602;  // DejaVu Sans Mono's width of a cell, in ems
// restty keeps about 10 MB of decoded images and drops the oldest beyond that, so the server draws for cells of this width, and the terminal scales up.
const IMAGE_CELL = 7;
const MOVE = 650, PRESS = 180, KEY_SHOWN = 900, RESTART_AFTER = 6000, SCROLL_STEP = 60;
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
        // the grid is the one measured below, which the server draws for: restty would otherwise fit one of its own to the box,
        // which differs at fractional pixel ratios, leaving the frames in a corner of a larger grid
        fontSize: FONT_SIZE, ligatures: false, showResizeOverlay: false, autoResize: false,
        theme: parseGhosttyTheme("background = #ffffff\nforeground = #000000\ncursor-color = #000000\n"),
        fonts: [{ url: "/vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                { url: "/vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
    },
    services: () => ({ beforeInput: ({ text, source }) => (source === "pty" ? text : null) }),
});

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const write = data => terminal.sendInput(data, "pty");
const pane = terminal.getActivePane();let grid = null, generation = 0, playing = false, position = 0;
const frames = [];
let parts = 1, loaded = 0, failed = null;

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

async function move(target, quick) {
    const shown = pointer.style.display === "block";
    pointer.classList.toggle("quick", quick);
    pointer.style.display = "block";
    pointer.style.transform = point(...target);
    await sleep(quick || !shown ? 110 : MOVE);
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

async function show(frame, previous, mine) {
    caption.textContent = frame.caption;
    progress.textContent = `${position + 1} of ${frames.length}${loaded < parts ? "+" : ""}`;
    // a drag's steps follow one another closely, as a hand would move
    if (frame.pointer) await move(frame.pointer, !!previous?.pointer && previous.caption === frame.caption);
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
        keycap.style.transform = (frame.pointer ? point(...frame.pointer) : point(grid.cols - 12, 1)) + " translate(22px, 18px)";
        setTimeout(() => { keycap.style.display = "none"; }, KEY_SHOWN);
    }
    if (mine !== generation) return;
    if (frame.output) write(frame.output);
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
        if (position === 0) write("\x1b_Ga=d,q=2\x1b\\\x1bc");
        if (position === frames.length) {
            if (loaded < parts && !failed) {  // the next part is still being drawn
                caption.textContent = "The server is drawing the next part…";
                pointer.style.display = "none";
                await sleep(300);
                continue;
            }
            // nothing carries over, so that a replay starts clean
            caption.textContent = failed ? `The rest of the demo could not be drawn: ${failed}` : "That's the demo. It starts again shortly.";
            await sleep(RESTART_AFTER);
            if (mine !== generation) return;
            position = 0;
            continue;
        }
        await show(frames[position], frames[position - 1], mine);
        if (mine !== generation) return;
        position++;
    }
}

function stop() {
    generation++;
    setPlaying(false);
}

async function load(part) {
    const query = new URLSearchParams({ part, cols: grid.cols, rows: grid.rows, cell_width: IMAGE_CELL,
        cell_height: Math.round(IMAGE_CELL * grid.cellHeight / grid.cellWidth) });
    const response = await fetch(`/api/v1/showcase?${query}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error.message);
    parts = result.parts;
    frames.push(...result.frames);
    loaded = part + 1;
}

playButton.onclick = () => {
    if (playing) { stop(); return; }
    if (frames.length) play();
};
document.getElementById("restart").onclick = () => {
    if (!frames.length) return;
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
        caption.textContent = "Mol* is drawing the first part of the demo on the server…";
        await load(0);
        play();
        for (let part = 1; part < parts; part++) {
            // a later part is tried again a few times, as when the server restarts while drawing it
            for (let tries = 1; ; tries++) {
                try {
                    await load(part);
                    break;
                } catch (error) {
                    if (tries === 3) throw error;
                    await sleep(4000);
                }
            }
        }
    } catch (error) {
        if (frames.length) {
            failed = error.message;
        } else {
            setPlaying(false);
            caption.textContent = `The demo could not start: ${error.message}`;
        }
    }
} else {
    setPlaying(false);
    caption.textContent = "This browser could not start the terminal, which needs WebGL2 or WebGPU.";
}
