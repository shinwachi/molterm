// The web terminal: shows what the server's commands print, and runs the guided story two ways.
const screen_ = $("#screen"), historyEl = $("#history"), command = $("#command");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let selection = null, cell = 8, commandLog = [], logIndex = 0;

// Braille can come from a fallback font of another width, so every kind of character is spaced to one measured cell.
function measure() {
    const widthOf = text => {
        const probe = document.createElement("span");
        probe.textContent = text.repeat(40);
        probe.style.cssText = "position:absolute;visibility:hidden;letter-spacing:0";
        screen_.append(probe);
        const width = probe.getBoundingClientRect().width / 40;
        probe.remove();
        return width;
    };
    const ascii = widthOf("M"), dots = widthOf("⣿");
    cell = Math.max(ascii, dots);
    const style = screen_.style;
    style.setProperty("--cell", cell + "px");
    style.setProperty("--row", cell * 2 + "px");  // dots are evenly spaced when a row is twice a cell's width
    style.setProperty("--gap-ascii", cell - ascii + "px");
    style.setProperty("--gap-dots", cell - dots + "px");
}

const kindOf = char => /[⠀-⣿]/.test(char) ? "dots" : char >= " " && char <= "~" ? "ascii" : "wide";

function appendRuns(line, string, code) {
    let run = "", kind = null;
    const flush = () => {
        if (!run) return;
        const span = document.createElement("span");
        span.className = kind + (code ? " c" + code : "");
        span.textContent = run;
        line.append(span);
        run = "";
    };
    for (const char of string) {
        const next = kindOf(char);
        if (next !== kind || next === "wide") { flush(); kind = next; }
        run += char;
    }
    flush();
}

function appendLine(ansi) {
    const line = document.createElement("div");
    line.className = "line";
    let code = null;
    ansi.split(/\x1b\[(\d+)m/).forEach((part, index) => {
        if (index % 2) code = part === "0" ? null : part;
        else appendRuns(line, part, code);
    });
    historyEl.append(line);
    return line;
}

function echo(text) {
    const line = document.createElement("div");
    line.className = "line";
    const prompt = document.createElement("span");
    prompt.className = "prompt ascii";
    prompt.textContent = "medchem$ ";
    line.append(prompt);
    appendRuns(line, text, null);
    historyEl.append(line);
    return line;
}

// Keep the start of a tall output in view, rather than jumping to its end.
function reveal(line) {
    screen_.scrollTop = screen_.scrollHeight;
    if (line && line.isConnected) screen_.scrollTop = Math.min(screen_.scrollTop, line.offsetTop - historyEl.offsetTop);
}

async function execute(text) {
    const typed = echo(text);
    if (text.trim()) { commandLog.push(text); logIndex = commandLog.length; }
    const cols = Math.floor((screen_.clientWidth - 24) / cell);
    let result;
    try {
        const response = await fetch("/api/v1/term", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ line: text, selection, cols }) });
        result = await response.json();
        if (!response.ok) throw new Error(result.error.message);
    } catch (e) {
        appendLine(`\x1b[31mThe server did not answer: ${e.message}\x1b[0m`);
        reveal(typed);
        return false;
    }
    selection = result.selection;
    if (result.clear) historyEl.replaceChildren();
    else if (result.output) { result.output.split("\n").forEach(appendLine); appendLine(""); }
    else appendLine("");
    reveal(typed);
    return true;
}

function restart() {
    selection = null;
    historyEl.replaceChildren();
    command.value = "";
}

// Tab completes the last word, as far as all its candidates agree.
function complete() {
    const words = command.value.split(" ");
    const last = words[words.length - 1].toLowerCase();
    if (!last) return;
    const found = COMPLETIONS.filter(c => c.startsWith(last));
    if (!found.length) return;
    let shared = found[0];
    for (const c of found) while (!c.startsWith(shared)) shared = shared.slice(0, -1);
    words[words.length - 1] = shared + (found.length === 1 ? " " : "");
    command.value = words.join(" ");
}

// The guide: one panel and one set of buttons, for free use and for both ways through the story.

let mode = "free", stepIndex = 0, stage = "before", paused = false, speed = 1, runToken = 0, snapshots = [];

function button(label, onclick, kind = "") {
    const b = document.createElement("button");
    b.className = "btn " + kind;
    b.type = "button";
    b.textContent = label;
    b.onclick = onclick;
    return b;
}

function showGuide({ progress = "", title = "", before = "", after = "" }) {
    $("#progress").textContent = progress;
    $("#title").textContent = title;
    $("#before").textContent = before;
    $("#after").textContent = after;
}

function showStep(index, finished) {
    const step = STORY[index];
    showGuide({ progress: `Step ${index + 1} of ${STORY.length}`, title: step.title, before: step.before, after: finished ? step.after : "" });
}

function controls() {
    const box = $("#controls");
    if (mode === "play") {
        box.replaceChildren(
            button(paused ? "Resume" : "Pause", () => { paused = !paused; controls(); }, "primary"),
            button(`Speed ${speed}×`, () => { speed = speed === 1 ? 2 : 1; controls(); }),
            button("Stop", stop, "quiet"));
    } else if (mode === "walk") {
        const last = stepIndex === STORY.length - 1;
        box.replaceChildren(
            button(stage === "before" ? "Run command" : last ? "Finish" : "Next step", advance, "primary"),
            button("Back", back),
            button("Stop", stop, "quiet"));
    } else {
        box.replaceChildren(button("Play demo", play, "primary"), button("Walk me through it", walk));
    }
}

function stop() {
    runToken++;
    mode = "free";
    paused = false;
    command.readOnly = false;
    command.value = "";
    showGuide({ title: "A medicinal chemistry session", before: "See how Molterm's text drawings work in real analysis, on 15 known " +
        "compounds. Watch the demo, walk through it at your own pace, or type help and explore. Every result is computed live " +
        "with RDKit. The terminal is simulated and only runs the commands listed in help." });
    controls();
    command.focus({ preventScroll: true });
}

// Resolves after ms of unpaused time, or with false as soon as this run was stopped.
async function wait(ms, token) {
    let left = ms;
    while (left > 0) {
        if (token !== runToken) return false;
        await new Promise(r => setTimeout(r, 40));
        if (!paused) left -= 40 * speed;
    }
    return token === runToken;
}

const readingTime = text => Math.max(2500, text.split(" ").length * 300);

async function typeOut(text, token) {
    if (reducedMotion) { command.value = text; return wait(300, token); }
    for (let i = 1; i <= text.length; i++) {
        command.value = text.slice(0, i);
        if (!await wait(45, token)) return false;
    }
    return wait(350, token);
}

async function play() {
    const token = ++runToken;
    mode = "play";
    paused = false;
    command.readOnly = true;
    restart();
    controls();
    for (let i = 0; i < STORY.length; i++) {
        stepIndex = i;
        showStep(i, false);
        if (!await wait(readingTime(STORY[i].before), token)) return;
        if (!await typeOut(STORY[i].command, token)) return;
        command.value = "";
        const ok = await execute(STORY[i].command);
        if (token !== runToken) return;
        if (!ok) { stop(); return; }
        showStep(i, true);
        if (!await wait(readingTime(STORY[i].after) + 1500, token)) return;
    }
    mode = "free";
    command.readOnly = false;
    controls();
    command.focus({ preventScroll: true });
}

function prepareWalkStep(index) {
    stepIndex = index;
    stage = "before";
    snapshots[index] = selection;
    command.value = STORY[index].command;
    showStep(index, false);
    controls();
    screen_.scrollTop = screen_.scrollHeight;
    command.focus({ preventScroll: true });
    command.select();
}

function walk() {
    runToken++;
    mode = "walk";
    paused = false;
    command.readOnly = false;
    snapshots = [];
    restart();
    prepareWalkStep(0);
}

async function advance() {
    if (stage === "before") {
        const token = runToken;
        command.value = "";
        const step = stepIndex;
        const ok = await execute(STORY[step].command);
        if (token !== runToken || !ok) return;
        stage = "after";
        showStep(step, true);
        controls();
        command.focus({ preventScroll: true });
    } else if (stepIndex < STORY.length - 1) {
        prepareWalkStep(stepIndex + 1);
    } else {
        mode = "free";
        controls();
        command.focus({ preventScroll: true });
    }
}

function back() {
    if (stage === "before" && stepIndex === 0) return;
    const target = stage === "after" ? stepIndex : stepIndex - 1;
    selection = snapshots[target];
    prepareWalkStep(target);
}

command.onkeydown = async e => {
    if (e.key === "Tab") { e.preventDefault(); complete(); return; }
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        logIndex = Math.min(Math.max(logIndex + (e.key === "ArrowUp" ? -1 : 1), 0), commandLog.length);
        command.value = commandLog[logIndex] || "";
        return;
    }
    if (e.key !== "Enter" || mode === "play") return;
    e.preventDefault();
    const text = command.value;
    if (mode === "walk") {
        // the suggested command, or nothing once it has run, moves the story on; anything else is the reader exploring
        const suggested = stage === "before" && text.trim() === STORY[stepIndex].command;
        if (suggested || (stage === "after" && !text.trim())) { advance(); return; }
    }
    command.value = "";
    await execute(text);
    if (mode === "walk" && stage === "before") { command.value = STORY[stepIndex].command; command.select(); }
};

screen_.addEventListener("click", () => {
    if (mode !== "play" && !String(getSelection())) command.focus({ preventScroll: true });
});
addEventListener("resize", measure);

measure();
stop();
appendLine("\x1b[90mType help to see the commands, or choose Play demo or Walk me through it.\x1b[0m");
appendLine("");
command.blur();
