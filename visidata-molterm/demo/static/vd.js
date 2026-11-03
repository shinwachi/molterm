// Plays the recorded VisiData session in a web terminal with Kitty graphics. The recording is the terminal's real output, cut at each key
// pressed, so this only prints it back at the size it was recorded, and shows the key and a caption.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";

const ADVANCE = 0.602;  // DejaVu Sans Mono's width of a cell, in ems
const KEY_SHOWN = 1100, RESTART_AFTER = 6000;
const element = document.getElementById("terminal"), frameBox = document.getElementById("frame");
const caption = document.getElementById("caption"), progress = document.getElementById("progress");
const keycap = document.getElementById("keycap"), playButton = document.getElementById("play");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

let terminal = null, pane = null, session = null, generation = 0, playing = false, position = 0;

async function load() {
    for (;;) {
        const response = await fetch("/api/v1/recording");
        const result = await response.json();
        if (response.ok) return result;
        caption.textContent = result.error.message;
        if (result.error.code !== "recording") throw new Error(result.error.message);
        await sleep(3000);
    }
}

// restty sizes its canvas in whole cells some time after a resize, so a grid has taken hold when the canvas divides evenly into it
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

// The canvas stretches to its parent, so the parent must be the canvas's own size, or text and pictures come out distorted.
function fitParent() {
    const ratio = window.devicePixelRatio || 1;
    const width = `${pane.canvas.width / ratio}px`, height = `${pane.canvas.height / ratio}px`;
    if (element.style.width !== width || element.style.height !== height) {
        element.style.width = frameBox.style.width = width;
        element.style.height = frameBox.style.height = height;
    }
}

async function start(cols, rows) {
    // the largest font at which the recorded width fits the page
    const fontSize = Math.min(Math.max(Math.floor(document.getElementById("screen").clientWidth / (cols * ADVANCE)) - 0.5, 7), 18);
    // somewhere to start; restty fits its grid to this box, so it's set to the grid's exact size once a cell is measured
    element.style.width = frameBox.style.width = `${Math.ceil(cols * fontSize * ADVANCE)}px`;
    element.style.height = frameBox.style.height = `${Math.ceil(rows * fontSize * 1.4)}px`;
    terminal = new Restty({
        root: element,
        surface: { shortcuts: false, searchUi: false, defaultContextMenu: false },
        terminal: {
            fontSize, ligatures: false, showResizeOverlay: false,
            theme: parseGhosttyTheme("background = #161615\nforeground = #f0efec\ncursor-color = #161615\n"),
            fonts: [{ url: "/vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                    { url: "/vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
        },
        // the session is a recording: nothing typed here is sent anywhere, and the terminal's own answers are dropped
        services: () => ({ beforeInput: ({ text, source }) => (source === "pty" ? text : null) }),
    });
    pane = terminal.getActivePane();
    const { runtime } = pane;
    while (!["ready", "failed"].includes(runtime.lifecycle.state())) await sleep(30);
    if (runtime.lifecycle.state() !== "ready") throw new Error("This browser could not start the terminal, which needs WebGL2 or WebGPU.");
    // restty sizes its canvas in whole cells of the device's pixels, so a known grid gives the cell's size
    pane.resize(100, 40);
    await settled(100, 40);
    const ratio = window.devicePixelRatio || 1;
    const cellWidth = pane.canvas.width / 100 / ratio, cellHeight = pane.canvas.height / 40 / ratio;
    element.style.width = frameBox.style.width = `${cols * cellWidth}px`;
    element.style.height = frameBox.style.height = `${rows * cellHeight}px`;
    await sleep(120);
    pane.resize(cols, rows);
    await settled(cols, rows);
    fitParent();
    setInterval(fitParent, 500);  // in case restty sizes its canvas again, as when its font arrives late
}

const write = data => terminal.sendInput(data, "pty");

async function show(frame, mine) {
    caption.textContent = frame.caption;
    progress.textContent = `${position + 1} of ${session.frames.length}`;
    if (frame.key) {
        keycap.textContent = frame.key;
        keycap.style.display = "block";
        setTimeout(() => { keycap.style.display = "none"; }, KEY_SHOWN);
    }
    if (mine !== generation) return;
    write(frame.output);
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
        if (position === 0) write("\x1b_Ga=d,d=A,q=2\x1b\\\x1bc");  // no pictures, and a fresh terminal, as VisiData had
        await show(session.frames[position], mine);
        if (mine !== generation) return;
        position++;
        if (position === session.frames.length) {
            caption.textContent = "That's the demo. It starts again shortly.";
            await sleep(RESTART_AFTER);
            if (mine !== generation) return;
            position = 0;
        }
    }
}

playButton.onclick = () => {
    if (playing) { generation++; setPlaying(false); return; }
    if (session) play();
};
document.getElementById("restart").onclick = () => {
    if (!session) return;
    position = 0;
    play();
};

try {
    session = await load();
    document.getElementById("recorded").textContent =
        `Recorded ${session.recorded} in a terminal of ${session.cols} by ${session.rows} cells.`;
    await start(session.cols, session.rows);
    play();
} catch (error) {
    setPlaying(false);
    caption.textContent = `The demo could not start: ${error.message}`;
}
