// The web terminal: shows what the server's commands print, as text in the page.
const screen_ = $("#screen"), historyEl = $("#history");
let selection = null, cell = 8;

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
    remember(text);
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

function scrollToPrompt() {
    screen_.scrollTop = screen_.scrollHeight;
}

function restart() {
    selection = null;
    historyEl.replaceChildren();
    command.value = "";
}

screen_.addEventListener("click", () => {
    if (mode !== "play" && !String(getSelection())) command.focus({ preventScroll: true });
});
addEventListener("resize", measure);

measure();
stop();
appendLine("\x1b[90mType help to see the commands, or choose Play demo or Walk me through it.\x1b[0m");
appendLine("");
command.blur();
