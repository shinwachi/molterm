// The port against the molterm server's own output (site/check.json). ASCII needs no rasterizing, so it is checked here, character for
// character; Braille needs a browser to draw RDKit's SVG, and is checked by check.html, in a browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import initRDKit from "../vendor/RDKit_minimal.js";
import { GROUPS } from "../site/samples.js";
import { parse, render, formula, labelText, kittySequence, ParseError } from "../site/molterm.js";

const RDKit = await initRDKit({ locateFile: () => new URL("../vendor/RDKit_minimal.wasm", import.meta.url).pathname });
const fixtures = JSON.parse(readFileSync(new URL("../site/check.json", import.meta.url)));
const samples = GROUPS.flatMap(([, items]) => items);

test("RDKit.js is the version the server draws with", () => {
    assert.equal(RDKit.version(), fixtures.rdkit);
});

test("every sample's formula is the server's", () => {
    for (const [name, smiles, expected] of samples) {
        const parsed = parse(RDKit, smiles);
        assert.equal(parsed.formula, expected, name);
        parsed.mol.delete();
    }
});

for (const rendering of fixtures.renderings.filter(r => r.mode === "ascii")) {
    test(`ASCII ${rendering.name} at ${rendering.cols}x${rendering.rows} matches the server`, async () => {
        const parsed = parse(RDKit, rendering.molblock);
        const result = render(parsed, { cols: rendering.cols, rows: rendering.rows, mode: "ascii" });
        parsed.mol.delete();
        assert.equal(result.text, rendering.text);
    });
}

test("labels carry hydrogens, charges and isotopes, hydrogens away from the bonds", () => {
    const label = smiles => {
        const parsed = parse(RDKit, smiles);
        const labels = parsed.atoms.map((atom, i) => labelText(atom, parsed, i)?.[0]).filter(Boolean);
        parsed.mol.delete();
        return labels;
    };
    assert.deepEqual(label("O"), ["H₂O"]);
    const zwitterion = label("[NH3+]CC(=O)[O-]");
    assert.ok(zwitterion.includes("O⁻") && zwitterion.includes("O"), zwitterion.join(" "));
    assert.ok(zwitterion.includes("H₃N⁺") || zwitterion.includes("NH₃⁺"), zwitterion.join(" "));
    assert.deepEqual(label("[13CH4]"), ["¹³CH₄"]);
    assert.deepEqual(label("[Na+].[Cl-]"), ["Na⁺", "Cl⁻"]);
});

test("unbonded atoms are set as a line of labels", async () => {
    const parsed = parse(RDKit, "[Na+].[Cl-]");
    const result = render(parsed, { cols: 80, rows: 30, mode: "braille" });
    parsed.mol.delete();
    assert.equal(result.text, "Na⁺⠀Cl⁻");
    assert.equal(result.ansi, "\x1b[1mNa⁺\x1b[0m⠀\x1b[32mCl⁻\x1b[0m");  // chlorine green, as on the server
});

test("bad input is refused with a reason", () => {
    assert.throws(() => parse(RDKit, ""), ParseError);
    assert.throws(() => parse(RDKit, "C1CC"), ParseError);
    assert.throws(() => parse(RDKit, "CC\nCC"), /one SMILES/);
});

test("the Hill formula puts carbon and hydrogen first, and the charge last", () => {
    const atoms = (...specs) => specs.map(([symbol, hydrogens = 0, charge = 0]) => ({ symbol, hydrogens, charge }));
    assert.equal(formula(atoms(["N", 4, 1])), "H4N+");
    assert.equal(formula(atoms(["C", 3], ["O", 0, -1])), "CH3O-");
});

test("a Kitty picture is cut into chunks of at most 4096 characters", () => {
    const sequence = kittySequence("A".repeat(9000), 20);
    const chunks = [...sequence.matchAll(/\x1b_G([^;]*);([^\x1b]*)\x1b\\/g)];
    assert.equal(chunks.length, 3);
    assert.equal(chunks[0][1], "a=T,f=100,c=20,q=2,m=1");
    assert.deepEqual(chunks.map(c => c[2].length), [4096, 4096, 808]);
    assert.equal(chunks[2][1], "q=2,m=0");
});
