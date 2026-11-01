// The guided session in a web terminal that shows Kitty graphics. It reads and prints like `molterm demo` in a real terminal.
import { Restty, parseGhosttyTheme } from "/vendor/restty.esm.js";

const FONT_SIZE = 13, ADVANCE = 0.602;  // DejaVu Sans Mono's width of a cell, in ems
const PLAY_PAUSE = 4000, TYPE_PAUSE = 35, BEFORE_RUN = 700;
// restty keeps about 10 MB of decoded images. A larger image never shows, and each new one pushes out the oldest.
const IMAGE_CELL = 10;

const theme = parseGhosttyTheme(`
background = #111113
foreground = #e9e8e4
cursor-color = #e9e8e4
palette = 1=#ff8a80
palette = 2=#7ddc7d
palette = 3=#f0d060
palette = 4=#86b6ef
palette = 5=#e0a0f0
palette = 6=#5fe3e3
palette = 8=#9a9990
`);

const element = document.getElementById("terminal");
const terminal = new Restty({
    root: element,
    surface: { shortcuts: false, searchUi: false, defaultContextMenu: false },
    terminal: {
        fontSize: FONT_SIZE, ligatures: false, showResizeOverlay: false, theme,
        fonts: [{ url: "/vendor/DejaVuSansMono.ttf", name: "DejaVu Sans Mono" },
                { url: "/vendor/DejaVuSansMono-Bold.ttf", name: "DejaVu Sans Mono Bold", weight: 700 }],
    },
    services: () => ({
        beforeInput({ text, source }) {
            if (source === "pty") return text;
            key(text);
            return null;  // nothing is echoed unless the line editor prints it
        },
    }),
});

const write = data => terminal.sendInput(data.replace(/\r?\n/g, "\r\n"), "pty");
const columns = () => Math.max(40, Math.floor(element.clientWidth / (FONT_SIZE * ADVANCE)));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function say(text) {
    const width = columns() - 1, rows = [];
    let row = "";
    for (const word of text.split(" ")) {
        if (row && row.length + 1 + word.length > width) { rows.push(row); row = word; }
        else row = row ? `${row} ${word}` : word;
    }
    write(rows.concat(row).join("\n") + "\n");
}

let selection = null, step = 0, walking = true, playing = false, busy = true, generation = 0;
let buffer = "", history = [], back = 0;

const prompt = () => write(`\x1b[32mmedchem$\x1b[0m ${buffer}`);

function heading(index) {
    write(`\n\x1b[1mStep ${index + 1} of ${STORY.length}: ${STORY[index].title}\x1b[0m\n`);
    say(STORY[index].before);
    write("\n");
}

function offer() {
    if (walking) write(`\x1b[90mPress Enter to run: ${STORY[step].command} (or type another command)\x1b[0m\n`);
    prompt();
}

async function run(line) {
    const mine = generation;
    const response = await fetch("/api/v1/term", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line, selection, cols: columns(), display: "kitty", cell_width: IMAGE_CELL }),
    });
    const result = await response.json();
    if (mine !== generation) return false;
    if (!response.ok) { write(`\x1b[31m${result.error.message}\x1b[0m\n`); return true; }
    selection = result.selection;
    if (result.clear) write("\x1b_Ga=d,q=2\x1b\\\x1b[2J\x1b[H");
    if (result.output) write(result.output + "\n\n");
    return true;
}

async function runStep() {
    if (!await run(STORY[step].command)) return;
    say(STORY[step].after);
    step++;
    if (step === STORY.length) {
        walking = false;
        write("\n");
        say("Your turn: help lists the commands.");
    } else {
        heading(step);
    }
}

async function submit() {
    const line = buffer.trim();
    buffer = ""; back = 0;
    write((walking && !line ? STORY[step].command : "") + "\n");  // so that the transcript shows what ran
    busy = true;
    const mine = generation;
    try {
        if (line) history.push(line);
        if (walking && (!line || line === STORY[step].command)) await runStep();
        else if (line) await run(line);
    } catch (error) {
        write(`\x1b[31mThe server did not answer: ${error.message}\x1b[0m\n`);
    }
    if (mine !== generation) return;
    busy = false;
    offer();
}

function replaceLine(text) {
    write("\b \b".repeat(buffer.length) + text);
    buffer = text;
}

function complete() {
    const start = buffer.lastIndexOf(" ") + 1, word = buffer.slice(start);
    if (!word) return;
    const found = COMPLETIONS.filter(name => name.startsWith(word.toLowerCase()));
    if (found.length === 1) replaceLine(buffer.slice(0, start) + found[0] + " ");
    else if (found.length > 1) { write(`\n\x1b[90m${found.join("  ")}\x1b[0m\n`); prompt(); }
}

function key(text) {
    if (playing && text === "\x03") { stop(); return; }
    if (busy || playing) return;
    if (text === "\r" || text === "\n") submit();
    else if (text === "\x7f" || text === "\b") { if (buffer) replaceLine(buffer.slice(0, -1)); }
    else if (text === "\x03") { buffer = ""; write("^C\n"); prompt(); }
    else if (text === "\x15") replaceLine("");
    else if (text === "\t") complete();
    else if (text === "\x1b[A" || text === "\x1bOA") { if (back < history.length) replaceLine(history[history.length - ++back]); }
    else if (text === "\x1b[B" || text === "\x1bOB") { if (back > 0) replaceLine(--back ? history[history.length - back] : ""); }
    else if (!text.startsWith("\x1b")) {
        const clean = text.replace(/[\x00-\x1f\x7f]/g, " ");  // a paste can hold anything
        write(clean);
        buffer += clean;
    }
}

function begin() {
    generation++;
    selection = null; step = 0; walking = true; buffer = ""; busy = false;
    write("\x1b_Ga=d,q=2\x1b\\\x1bc");
    say("Fifteen known compounds, mostly kinase inhibitors. Results are computed live with RDKit from structure alone.");
    heading(0);
}

const playButton = document.getElementById("play");

function stop() {
    if (!playing) return;
    playing = false;
    generation++;
    playButton.textContent = "Play demo";
    busy = false;
    write("\n");
    offer();
}

async function play() {
    begin();
    playing = true;
    busy = true;
    playButton.textContent = "Stop";
    const mine = generation;
    while (walking) {
        const command = STORY[step].command;
        write("\x1b[32mmedchem$\x1b[0m ");
        for (const char of command) {
            write(char);
            await sleep(TYPE_PAUSE);
            if (mine !== generation) return;
        }
        write("\n");
        await sleep(BEFORE_RUN);
        if (mine !== generation) return;
        try {
            await runStep();
        } catch (error) {
            write(`\x1b[31mThe server did not answer: ${error.message}\x1b[0m\n`);
            stop();
            return;
        }
        if (mine !== generation) return;
        await sleep(PLAY_PAUSE);
        if (mine !== generation) return;
    }
    playing = false;
    busy = false;
    playButton.textContent = "Play demo";
    prompt();
}

playButton.onclick = () => (playing ? stop() : play());
document.getElementById("restart").onclick = () => {
    playing = false;
    playButton.textContent = "Play demo";
    begin();
    offer();
    terminal.focus();
};

// Text written before the terminal has its final size gets reflowed, which leaves the cursor a line below the prompt.
async function ready() {
    const { runtime } = terminal.getActivePane();
    while (!["ready", "failed"].includes(runtime.lifecycle.state())) await sleep(30);
    return runtime.lifecycle.state() === "ready";
}

if (await ready()) {
    begin();
    offer();
    terminal.focus();
} else {
    document.getElementById("intro").textContent = "This browser could not start the terminal, which needs WebGL2 or WebGPU. " +
        "The Compare page shows images in another terminal emulator.";
}
