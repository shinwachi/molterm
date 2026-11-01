// Two real terminal emulators given the same commands: one receives text, the other images in a graphics protocol.
let selection = null, generation = 0;

function makePane(id, withImages) {
    const term = new Terminal({
        fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono"),
        fontSize: 13, convertEol: true, disableStdin: true, scrollback: 5000,
        // the element colours of the other pages, since xterm's default red and blue are hard to read on black
        theme: { background: "#111113", foreground: "#e9e8e4", cursor: "#111113", red: "#ff8a80", green: "#7ddc7d", yellow: "#f0d060",
                 blue: "#86b6ef", magenta: "#e0a0f0", cyan: "#5fe3e3", brightBlack: "#9a9990" },
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(document.getElementById(id));
    if (withImages) term.loadAddon(new ImageAddon.ImageAddon());
    fit.fit();
    term.write("\x1b[?25l");  // the command line lives below the panes
    return { term, fit };
}

const textPane = makePane("pane-text", false);
const imagePane = makePane("pane-image", true);
const panes = [textPane, imagePane];

function cellWidth({ term }) {
    return term.element.querySelector(".xterm-screen").getBoundingClientRect().width / term.cols;
}

function writePane({ term }, data) {
    return new Promise(resolve => term.write(data, resolve));
}

const askServer = (text, cols, display, cell_width, sent) => fetch("/api/v1/term", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ line: text, selection: sent, cols, display, cell_width }),
}).then(async response => {
    const result = await response.json();
    if (!response.ok) throw new Error(result.error.message);
    return result;
});

async function show(pane, text, result) {
    if (result.clear) { pane.term.clear(); return; }
    const buffer = pane.term.buffer.active;
    const start = buffer.baseY + buffer.cursorY;
    await writePane(pane, `\x1b[32mmedchem$\x1b[0m ${text}\n${result.output ? result.output + "\n" : ""}\n`);
    pane.term.scrollToLine(start);  // the start of a tall output, rather than its end
}

async function execute(text) {
    remember(text);
    const mine = generation, sent = selection, display = $("#format").value;
    // Each pane shows its answer as soon as it arrives, since images take longer to draw than text.
    const inPane = async (pane, ask) => {
        try {
            const result = await ask();
            if (mine === generation) await show(pane, text, result);
            return result;
        } catch (e) {
            if (mine === generation) await writePane(pane, `\x1b[31mThe server did not answer: ${e.message}\x1b[0m\n`);
            return null;
        }
    };
    const [textResult, imageResult] = await Promise.all([
        inPane(textPane, () => askServer(text, textPane.term.cols, "text", undefined, sent)),
        inPane(imagePane, () => askServer(text, imagePane.term.cols, display, Math.round(cellWidth(imagePane)), sent)),
    ]);
    if (mine !== generation || !textResult || !imageResult) return false;
    selection = textResult.selection;
    return true;
}

function scrollToPrompt() {
    panes.forEach(({ term }) => term.scrollToBottom());
}

function restart() {
    generation++;  // an answer still on its way belongs to the session before this one
    selection = null;
    panes.forEach(({ term }) => { term.reset(); term.write("\x1b[?25l"); });
    command.value = "";
}

function showFormat() {
    const name = $("#format").selectedOptions[0].textContent;
    $("#image-heading").textContent = `Graphics terminal: RDKit's image, as ${name} escape sequences`;
}

$("#format").onchange = showFormat;
$("#form").onsubmit = e => {
    e.preventDefault();
    command.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
};
addEventListener("resize", () => panes.forEach(({ fit }) => fit.fit()));

showFormat();
stop();
command.blur();
