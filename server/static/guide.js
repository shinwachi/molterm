// The guide, shared by the terminal pages: free use, the automated demo and the walkthrough, over one command line.
// Each page defines selection, execute(text), restart() and scrollToPrompt(), then calls stop() to begin.
const command = $("#command");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let commandLog = [], logIndex = 0, busy = false;

// Only one command at a time: a second would interleave its output with the first.
async function runCommand(text) {
    busy = true;
    try { return await execute(text); } finally { busy = false; }
}

function remember(text) {
    if (text.trim()) { commandLog.push(text); logIndex = commandLog.length; }
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
    showGuide(INTRO);
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
        const ok = await runCommand(STORY[i].command);
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
    scrollToPrompt();
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
    if (busy) return;
    if (stage === "before") {
        const token = runToken;
        command.value = "";
        const step = stepIndex;
        const ok = await runCommand(STORY[step].command);
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
    if (busy) return;
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
    if (busy) { e.preventDefault(); return; }
    e.preventDefault();
    const text = command.value;
    if (mode === "walk") {
        // the suggested command, or nothing once it has run, moves the story on; anything else is the reader exploring
        const suggested = stage === "before" && text.trim() === STORY[stepIndex].command;
        if (suggested || (stage === "after" && !text.trim())) { advance(); return; }
    }
    command.value = "";
    await runCommand(text);
    if (mode === "walk" && stage === "before") { command.value = STORY[stepIndex].command; command.select(); }
};

