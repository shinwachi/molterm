// Write site/check.json: the molterm server's own renderings of every sample, which the port is checked against.
// Run by `make fixtures`, in a container on the traefik network, against $MOLTERM_URL.
//
// RDKit.js lays a SMILES out in its canonical orientation, while the server keeps RDKit's first layout, so the two draw the same molecule
// turned differently. Each sample therefore goes to the server as RDKit.js's molblock, whose layout the server keeps: both then render the
// same coordinates, and any difference is the renderer's.
import { writeFileSync } from "node:fs";
import initRDKit from "../vendor/RDKit_minimal.js";
import { GROUPS } from "../site/samples.js";

const RDKit = await initRDKit({ locateFile: () => new URL("../vendor/RDKit_minimal.wasm", import.meta.url).pathname });
const server = (process.env.MOLTERM_URL || "http://molterm-dev-molterm-1:8910").replace(/\/$/, "");
const SIZES = [[80, 30], [50, 18]];
const health = await (await fetch(`${server}/health`)).json();
const out = { server: health.version, rdkit: health.rdkit, renderings: [] };
for (const [, items] of GROUPS) {
    for (const [name, smiles] of items) {
        const mol = RDKit.get_mol(smiles);
        mol.set_new_coords(false);
        const molblock = mol.get_molblock();
        mol.delete();
        for (const mode of ["ascii", "braille"]) {
            for (const [cols, rows] of SIZES) {
                const query = new URLSearchParams({ input: molblock, mode, cols, rows });
                const response = await fetch(`${server}/api/v1/render.txt?${query}`);
                if (!response.ok) throw new Error(`${name}: ${response.status} ${await response.text()}`);
                out.renderings.push({ name, smiles, molblock, mode, cols, rows, text: (await response.text()).replace(/\n$/, "") });
            }
        }
    }
}
writeFileSync(new URL("../site/check.json", import.meta.url), JSON.stringify(out, null, 1) + "\n");
console.log(`${out.renderings.length} renderings from ${server}`);
