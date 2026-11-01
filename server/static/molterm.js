// Shared by the pages: draw a rendering as text, ask the server for one, and run the top bar.
const $ = s => document.querySelector(s);

// Braille can come from a fallback font wider than the monospace letters, so every cell is sized from a measured glyph.
function measureCell() {
    const probe = document.createElement("span");
    probe.textContent = "⣿".repeat(40);
    probe.style.visibility = "hidden";
    $("#out").append(probe);
    const width = probe.getBoundingClientRect().width / 40;
    probe.remove();
    return width;
}

function draw(render) {
    const out = $("#out");
    out.replaceChildren();
    const cell = measureCell();
    out.style.lineHeight = cell * 2 + "px";   // dots are evenly spaced when a row is twice a cell's width
    const lines = render.text.split("\n").map(line => Array.from(line));
    render.labels.forEach(label => Array.from(label.text).forEach((_, i) => {
        const b = document.createElement("b");
        b.textContent = lines[label.row][label.col + i];
        b.style.width = cell + "px";
        b.style.height = cell * 2 + "px";
        lines[label.row][label.col + i] = b;
    }));
    lines.forEach((line, r) => {
        let run = "", code = null;
        const flush = () => {
            if (run && code) { const s = document.createElement("span"); s.className = "c" + code; s.textContent = run; out.append(s); }
            else if (run) out.append(run);
            run = "";
        };
        line.forEach((c, i) => {
            const k = render.colors[r][i];
            if (typeof c !== "string") { flush(); c.className = "c" + k; out.append(c); return; }
            if (k !== code) { flush(); code = k; }
            run += c;
        });
        flush();
        if (r < lines.length - 1) out.append("\n");
    });
}

function status(kind, message) {
    const el = $("#status");
    el.replaceChildren();
    if (kind) { const d = document.createElement("span"); d.className = "dot " + kind; el.append(d, " "); }
    el.append(message);
}

function facts(d) {
    const rows = [["Name", d.name], ["Formula", d.formula], ["SMILES", d.smiles], ["Atoms", d.atoms.length], ["Bonds", d.bonds.length],
                  ["Layout", d.coords === "input" ? "from the molblock" : "computed"], ["Size", d.render.cols + " × " + d.render.rows + " characters"]];
    $("#facts tbody").replaceChildren(...rows.filter(r => r[1] !== "").map(([k, v]) => {
        const tr = document.createElement("tr"), a = tr.insertCell(), b = tr.insertCell();
        a.textContent = k; b.textContent = v;
        if (k === "SMILES") b.className = "mono";
        return tr;
    }));
    $("#facts").hidden = false;
}

// Return a function that renders readRequest()'s values. Only the latest call may touch the page.
function renderer(readRequest, { onDrawn = () => {}, onFailed = () => {} } = {}) {
    let latest = 0;
    return async function go() {
        const mine = ++latest;
        let response, d;
        try {
            response = await fetch("/api/v1/render", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(readRequest()) });
            d = await response.json();
        } catch (e) {
            if (mine === latest) { status("err", "The server did not answer: " + e.message); onFailed(); }
            return;
        }
        if (mine !== latest) return;
        if (!response.ok) { status("err", d.error.message); onFailed(); return; }
        draw(d.render);
        facts(d);
        status(d.render.cramped ? "warn" : "", d.render.cramped ? "Too small to read reliably: add columns or rows." : "");
        onDrawn(d);
    };
}

fetch("/health").then(r => r.ok ? r.json() : Promise.reject())
    .then(d => $("#env").innerHTML = "<i></i>" + (d.env || 'prod') + ' · v' + d.version)
    .catch(() => $("#env").innerHTML = "<i></i>dev");
const modes = ["auto", "light", "dark"];
const btn = $("#theme");
btn.textContent = document.documentElement.dataset.theme || "auto";
btn.onclick = () => {
    const next = modes[(modes.indexOf(btn.textContent) + 1) % 3];
    btn.textContent = next;
    if (next === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = next;
    try { next === "auto" ? localStorage.removeItem("theme") : localStorage.setItem("theme", next); } catch (e) {}
};
