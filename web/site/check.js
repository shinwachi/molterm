// Draw every sample here and compare it with the molterm server's own rendering of the same molblock (check.json, written by tests/fixtures.mjs).
// The result is also left on window.checkResult.
import loadRDKit from "./rdkit.js";
import { parse, render } from "./molterm.js";

const RDKit = await loadRDKit();
const fixtures = await (await fetch("check.json")).json();
const canvas = document.createElement("canvas");
const body = document.querySelector("#results tbody");
const results = [];

function share(a, b) {
    const left = a.split("\n").map(l => [...l]), right = b.split("\n").map(l => [...l]);
    let same = 0, total = 0;
    for (let r = 0; r < Math.max(left.length, right.length); r++) {
        for (let c = 0; c < Math.max(left[r]?.length ?? 0, right[r]?.length ?? 0); c++) {
            total++;
            if (left[r]?.[c] === right[r]?.[c]) same++;
        }
    }
    return total ? same / total : 1;
}

for (const fixture of fixtures.renderings) {
    const parsed = parse(RDKit, fixture.molblock);
    const result = render(parsed, { cols: fixture.cols, rows: fixture.rows, mode: fixture.mode, canvas });
    parsed.mol.delete();
    const matching = share(result.text, fixture.text);
    results.push({ name: fixture.name, mode: fixture.mode, cols: fixture.cols, rows: fixture.rows, matching, exact: result.text === fixture.text });
    const row = document.createElement("tr");
    for (const text of [fixture.name, fixture.mode, `${fixture.cols}×${fixture.rows}`, `${(matching * 100).toFixed(1)}%`]) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
    }
    row.lastChild.className = "num" + (result.text === fixture.text ? "" : " mismatch");
    body.append(row);
    if (result.text !== fixture.text) {
        const block = document.createElement("div");
        block.innerHTML = `<h3></h3><div class="check-row"><div><p class="faint">server</p><pre></pre></div><div><p class="faint">here</p><pre></pre></div></div>`;
        block.querySelector("h3").textContent = `${fixture.name}, ${fixture.mode}, ${fixture.cols}×${fixture.rows}`;
        const [server, here] = block.querySelectorAll("pre");
        server.textContent = fixture.text;
        here.textContent = result.text;
        document.getElementById("mismatches").append(block);
    }
}

const exact = results.filter(r => r.exact).length;
const worst = Math.min(...results.map(r => r.matching));
document.querySelector("#summary").lastChild.textContent = `${exact} of ${results.length} exact · worst ${(worst * 100).toFixed(1)}%`;
document.querySelector("#summary i").style.background = worst > 0.97 ? "var(--ok)" : "var(--warn)";
window.checkResult = { results, exact, worst };
