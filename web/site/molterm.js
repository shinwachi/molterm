// molterm's text renderer, ported from server/render.py, braille.py, octant.py and asciiart.py to run in the browser.
//
// RDKit (compiled to WebAssembly as RDKit.js) parses the molecule, lays it out and draws its bonds on a canvas, with each element in a
// pure colour and no labels. This module turns the canvas's pixels into Braille or octant dots, colours each cell by the element whose ink
// covers most of it, and sets the atom labels as real letters. ASCII draws each bond as characters instead, and needs no canvas.
//
// Nothing here touches the page: the caller gives the canvas to draw on.

export const MODES = ["braille", "octant", "ascii"];
export const MAX_ATOMS = 500;  // as the server: layout time grows steeply with size

const SUPERSAMPLE = 4;  // RDKit pixels per dot, per axis
const INK_LEVEL = 128;  // a pixel is ink when its darkest channel is below this, so pure colours count as well as black
const DOT_COVERAGE = 0.3;  // share of a dot's pixels that must be ink to set the dot
const MAX_BOND_DOTS = 6;
const CELL_WIDTH = 2, CELL_HEIGHT = 4;
const PADDING = 0.08;

const SUPERSCRIPTS = Object.fromEntries([..."0123456789+-"].map((c, i) => [c, "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"[i]]));
const SUBSCRIPTS = Object.fromEntries([..."0123456789"].map((c, i) => [c, "₀₁₂₃₄₅₆₇₈₉"[i]]));
const PLAIN = Object.fromEntries([..."₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"].map((c, i) => [c, "01234567890123456789+-"[i]]));
const translate = (text, table) => [...text].map(c => table[c] ?? c).join("");

// Terminal colours rather than fixed RGB, so they follow the terminal's theme.
export const ANSI_COLORS = { O: 31, N: 34, S: 33, P: 35, F: 32, Cl: 32, Br: 32, I: 32 };
const ANSI_DEFAULT = 1;  // bold
// RDKit draws each element in a pure colour, which identifies the element again in the pixels. Black is plain carbon.
const DRAW_COLORS = { 31: [1, 0, 0], 32: [0, 1, 0], 33: [1, 1, 0], 34: [0, 0, 1], 35: [1, 0, 1] };
const INK_CODES = [null, ...Object.keys(DRAW_COLORS).map(Number)];
const INK_RGB = [[0, 0, 0], ...Object.values(DRAW_COLORS)].map(rgb => rgb.map(v => v * 255));

export const SYMBOLS = ("* H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr " +
    "Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po " +
    "At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og").split(" ");

// every element black but the coloured ones, as setAtomPalette does on the server
const PALETTE = Object.fromEntries([["-1", [0, 0, 0]], ...SYMBOLS.map((_, z) => [String(z), [0, 0, 0]])]);
for (const [symbol, code] of Object.entries(ANSI_COLORS)) PALETTE[SYMBOLS.indexOf(symbol)] = DRAW_COLORS[code];

const GLYPHS = {
    braille: { blank: "⠀", bits: [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]], char: mask => String.fromCodePoint(0x2800 + mask) },
    octant: { blank: " ", bits: [[0x01, 0x02], [0x04, 0x08], [0x10, 0x20], [0x40, 0x80]], char: mask => OCTANTS[mask] },
    ascii: { blank: " " },
};

// The 26 octant patterns Unicode encoded elsewhere; the octant block (U+1CD00) leaves them out and runs through the rest in order.
const ELSEWHERE = {
    0x00: " ", 0x01: "\u{1CEA8}", 0x02: "\u{1CEAB}", 0x03: "\u{1FB82}", 0x05: "▘", 0x0A: "▝", 0x0F: "▀", 0x14: "\u{1FBE6}",
    0x28: "\u{1FBE7}", 0x3F: "\u{1FB85}", 0x40: "\u{1CEA3}", 0x50: "▖", 0x55: "▌", 0x5A: "▞", 0x5F: "▛", 0x80: "\u{1CEA0}",
    0xA0: "▗", 0xA5: "▚", 0xAA: "▐", 0xAF: "▜", 0xC0: "▂", 0xF0: "▄", 0xF5: "▙", 0xFA: "▟", 0xFC: "▆", 0xFF: "█",
};
const OCTANTS = (() => {
    let next = 0x1CD00;
    return Array.from({ length: 256 }, (_, mask) => ELSEWHERE[mask] ?? String.fromCodePoint(next++));
})();

export class ParseError extends Error {}

/** A molecule from SMILES or a molblock: RDKit's own object for drawing, and the facts the renderer needs, with 2D coordinates. */
export function parse(RDKit, text) {
    text = String(text ?? "");
    const molblock = /M {2}END|V2000|V3000/.test(text);
    if (!molblock) text = text.trim();
    if (!text) throw new ParseError("Enter a SMILES string or a molblock.");
    if (!molblock && /[\r\n]/.test(text)) throw new ParseError("Enter one SMILES string at a time.");
    let mol = RDKit.get_mol(text);
    if (!mol && !molblock) mol = RDKit.get_qmol(text);  // a SMARTS query
    if (!mol) throw new ParseError(`Not a valid ${molblock ? "molblock" : "SMILES"}, or not chemically sensible: check it for typos.`);
    try {
        if (mol.has_coords() !== 2) mol.set_new_coords(false);
        const json = JSON.parse(mol.get_json());
        const { atom: atomDefaults, bond: bondDefaults } = json.defaults;
        const record = json.molecules[0];
        const atoms = record.atoms.map(a => ({ ...atomDefaults, ...a }));
        if (!atoms.length) throw new ParseError("The input contains no atoms.");
        if (atoms.length > MAX_ATOMS) throw new ParseError(`The molecule has ${atoms.length} atoms; the limit is ${MAX_ATOMS}.`);
        const bonds = (record.bonds || []).map(b => ({ ...bondDefaults, ...b })).map(b => ({ a: b.atoms[0], b: b.atoms[1], order: b.bo }));
        const coords = record.conformers[0].coords.map(([x, y]) => [x, y]);
        const degree = atoms.map(() => 0);
        const neighbours = atoms.map(() => []);
        for (const { a, b } of bonds) { degree[a]++; degree[b]++; neighbours[a].push(b); neighbours[b].push(a); }
        const facts = atoms.map((a, i) => ({ symbol: SYMBOLS[a.z] ?? "*", z: a.z, hydrogens: a.impHs, charge: a.chg, isotope: a.isotope,
                                             degree: degree[i], neighbours: neighbours[i] }));
        return { mol, atoms: facts, bonds, coords, formula: formula(facts) };
    } catch (error) {
        mol.delete();
        throw error;
    }
}

/** The Hill formula, as RDKit's CalcMolFormula writes it. */
export function formula(atoms) {
    const counts = {};
    let charge = 0;
    for (const atom of atoms) {
        counts[atom.symbol] = (counts[atom.symbol] || 0) + 1;
        if (atom.hydrogens) counts.H = (counts.H || 0) + atom.hydrogens;
        charge += atom.charge;
    }
    const order = counts.C ? ["C", ...(counts.H ? ["H"] : []), ...Object.keys(counts).filter(s => s !== "C" && s !== "H").sort()]
                           : Object.keys(counts).sort();
    const text = order.map(s => s + (counts[s] > 1 ? counts[s] : "")).join("");
    return text + (charge ? (charge > 0 ? "+" : "-") + (Math.abs(charge) > 1 ? Math.abs(charge) : "") : "");
}

function meanBondLength({ bonds, coords }) {
    return bonds.reduce((sum, { a, b }) => sum + Math.hypot(coords[a][0] - coords[b][0], coords[a][1] - coords[b][1]), 0) / bonds.length;
}

/** The options for RDKit's drawing of the bonds alone: pure element colours, no labels, kekulized, no wedges or added stereo hydrogens. */
function bondOptions(parsed, width, height, bondDots) {
    const mean = meanBondLength(parsed);
    return JSON.stringify({
        width, height, noAtomLabels: true, atomColourPalette: PALETTE, scaleBondWidth: false, bondLineWidth: SUPERSAMPLE,
        ...(mean > 0 ? { fixedBondLength: bondDots * SUPERSAMPLE / mean } : {}), padding: PADDING,
        kekulize: true, addChiralHs: false, wedgeBonds: false,
    });
}

/** A canvas for RDKit.js to draw on that also notes every point its lines pass through, at full precision. Given a real canvas, the
 *  drawing goes on to it as well; without one (as in Node), only the points are kept. */
export function recordingCanvas(width, height, real = null) {
    const points = [];
    const context = real?.getContext("2d", { willReadFrequently: true });
    const recorder = new Proxy({}, {
        get(_, key) {
            if (key === "moveTo" || key === "lineTo") {
                return (x, y) => { points.push([x, y]); context?.[key](x, y); };
            }
            if (context) {
                const value = context[key];
                return typeof value === "function" ? value.bind(context) : value;
            }
            return () => ({ width: 0 });
        },
        set(_, key, value) {
            if (context) context[key] = value;
            return true;
        },
    });
    if (real) { real.width = width; real.height = height; }
    return { points, canvas: { width, height, style: {}, getContext: () => recorder }, context };
}

/** RDKit draws the bonds; returns the SVG (whose lines are tagged with their bonds), the exact points of the canvas drawing, and the
 *  canvas's context, if a real canvas was given. */
function drawBonds(parsed, width, height, bondDots, real) {
    const options = bondOptions(parsed, width, height, bondDots);
    const svg = parsed.mol.get_svg_with_highlights(options);
    const recording = recordingCanvas(width, height, real);
    parsed.mol.draw_to_canvas_with_highlights(recording.canvas, options);
    return { svg, points: recording.points, context: recording.context };
}

/** A similarity transform (scale, then y flipped, then offset) fitted to [molecule point, drawing point] pairs. With too few points to
 *  measure a scale, `scale` is kept; and it is kept when the measure comes within `keep` of it. */
function fitTransform(pairs, scale, keep) {
    const n = pairs.length;
    const mx = pairs.reduce((s, p) => s + p[0][0], 0) / n, my = pairs.reduce((s, p) => s + p[0][1], 0) / n;
    const dx = pairs.reduce((s, p) => s + p[1][0], 0) / n, dy = pairs.reduce((s, p) => s + p[1][1], 0) / n;
    const spread = pairs.reduce((s, [m]) => s + (m[0] - mx) ** 2 + (m[1] - my) ** 2, 0);
    if (spread > 1e-6) {
        const measured = pairs.reduce((s, [m, d]) => s + (m[0] - mx) * (d[0] - dx) - (m[1] - my) * (d[1] - dy), 0) / spread;
        if (Math.abs(measured / scale - 1) > keep) scale = measured;
    }
    return ([x, y]) => [dx + (x - mx) * scale, dy - (y - my) * scale];
}

/** Where RDKit drew each atom, in pixels, as the server's GetDrawCoords gives them. RDKit.js has no GetDrawCoords, so the drawing's scale
 *  and offset are fitted to its bond lines: a single bond's line runs from one atom to the other, and the lines of any bond are centred on
 *  its middle. The SVG says which line is which bond but gives a tenth of a pixel, so the atoms are then moved to the nearest point of the
 *  canvas drawing (`points`, exact), and the transform is fitted again to those. */
export function atomPositions(svg, parsed, width, height, bondDots, points = []) {
    const lines = {};
    for (const [, bond, d] of svg.matchAll(/<path class='bond-(\d+) [^']*' d='([^']*)'/g)) {
        const ends = [...d.matchAll(/(-?[\d.]+),(-?[\d.]+)/g)].map(m => [+m[1], +m[2]]);
        if (ends.length >= 2) (lines[bond] ||= []).push([ends[0], ends.at(-1)]);
    }
    const pairs = [];  // [molecule x, y] -> [drawing x, y]
    parsed.bonds.forEach(({ a, b, order }, index) => {
        if (order === 1 && lines[index]) pairs.push([parsed.coords[a], lines[index][0][0]], [parsed.coords[b], lines[index].at(-1)[1]]);
    });
    if (!pairs.length) {
        // no single bonds, as in O=C=O: a double bond outside a ring and a triple bond are drawn evenly about the line between their atoms
        parsed.bonds.forEach(({ a, b }, index) => {
            const ends = (lines[index] || []).flat();
            if (!ends.length) return;
            const middle = [ends.reduce((s, p) => s + p[0], 0) / ends.length, ends.reduce((s, p) => s + p[1], 0) / ends.length];
            pairs.push([[(parsed.coords[a][0] + parsed.coords[b][0]) / 2, (parsed.coords[a][1] + parsed.coords[b][1]) / 2], middle]);
        });
    }
    const xs = parsed.coords.map(p => p[0]), ys = parsed.coords.map(p => p[1]);
    // the scale RDKit chooses: the fixed bond length, unless the molecule would not fit
    const mean = meanBondLength(parsed);
    const fits = Math.min(width * (1 - 2 * PADDING) / Math.max(Math.max(...xs) - Math.min(...xs), 1e-6),
                          height * (1 - 2 * PADDING) / Math.max(Math.max(...ys) - Math.min(...ys), 1e-6));
    const scale = mean > 0 ? Math.min(bondDots * SUPERSAMPLE / mean, fits) : fits;
    const rough = pairs.length ? fitTransform(pairs, scale, 0.005) : null;
    if (!rough) return parsed.coords.map(([x, y]) => [width / 2 + x * scale, height / 2 - y * scale]);
    const fitted = parsed.coords.map(rough);
    const exact = [];
    fitted.forEach(([x, y], index) => {
        let best = null, distance = 0.15;  // the SVG's rounding and the fit's error stay well inside this
        for (const p of points) {
            const d = Math.hypot(p[0] - x, p[1] - y);
            if (d < distance) { best = p; distance = d; }
        }
        if (best) exact.push([parsed.coords[index], best]);
    });
    return exact.length >= 2 ? parsed.coords.map(fitTransform(exact, scale, 1e-9)) : fitted;
}

function cellColors(pixels, width, height, cols, rows) {
    const dotW = CELL_WIDTH * SUPERSAMPLE, dotH = CELL_HEIGHT * SUPERSAMPLE;
    const counts = new Int32Array(INK_CODES.length * rows * cols);
    for (let y = 0; y < rows * dotH; y++) {
        for (let x = 0; x < cols * dotW; x++) {
            const i = (y * width + x) * 4, r = pixels[i], g = pixels[i + 1], b = pixels[i + 2];
            if (Math.min(r, g, b) >= INK_LEVEL) continue;
            let best = 0, bestDistance = Infinity;
            INK_RGB.forEach(([R, G, B], k) => {
                const distance = (r - R) ** 2 + (g - G) ** 2 + (b - B) ** 2;
                if (distance < bestDistance) { best = k; bestDistance = distance; }
            });
            counts[(best * rows + Math.floor(y / dotH)) * cols + Math.floor(x / dotW)]++;
        }
    }
    return Array.from({ length: rows }, (_, row) => Array.from({ length: cols }, (_, col) => {
        let best = 0;
        for (let k = 1; k < INK_CODES.length; k++) if (counts[(k * rows + row) * cols + col] > counts[(best * rows + row) * cols + col]) best = k;
        return INK_CODES[best];
    }));
}

/** Each dot's coverage by ink, as dots set or not, then two by four dots to a character. */
function dotGrid(pixels, width, height, cols, rows, glyphs) {
    const grid = [];
    for (let row = 0; row < rows; row++) {
        const line = [];
        for (let col = 0; col < cols; col++) {
            let mask = 0;
            for (let dy = 0; dy < CELL_HEIGHT; dy++) {
                for (let dx = 0; dx < CELL_WIDTH; dx++) {
                    const x0 = (col * CELL_WIDTH + dx) * SUPERSAMPLE, y0 = (row * CELL_HEIGHT + dy) * SUPERSAMPLE;
                    let ink = 0;
                    for (let y = y0; y < y0 + SUPERSAMPLE; y++) {
                        for (let x = x0; x < x0 + SUPERSAMPLE; x++) {
                            const i = (y * width + x) * 4;
                            if (Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) < INK_LEVEL) ink++;
                        }
                    }
                    if (ink / (SUPERSAMPLE * SUPERSAMPLE) >= DOT_COVERAGE) mask |= glyphs.bits[dy][dx];
                }
            }
            line.push(glyphs.char(mask));
        }
        grid.push(line);
    }
    return grid;
}

/** The label for an atom and the index of its element's first character, or null for a plain skeletal carbon. */
export function labelText(atom, parsed, index) {
    const { symbol, charge, isotope, degree, hydrogens } = atom;
    if (symbol === "C" && degree && !charge && !isotope) return null;
    const hText = hydrogens ? translate("H" + (hydrogens > 1 ? hydrogens : ""), SUBSCRIPTS) : "";
    const chargeText = charge ? translate(`${Math.abs(charge) > 1 ? Math.abs(charge) : ""}${charge > 0 ? "+" : "-"}`, SUPERSCRIPTS) : "";
    const prefix = isotope ? translate(String(isotope), SUPERSCRIPTS) : "";
    // hydrogens go on the side away from the bonds
    const x = parsed.coords[index][0];
    const spread = atom.neighbours.reduce((s, n) => s + parsed.coords[n][0] - x, 0);
    const hydrideFirst = !degree && ["O", "S", "Se", "F", "Cl", "Br", "I"].includes(symbol);  // H2O, not OH2
    if (hText && (spread > 0 || hydrideFirst)) return [`${hText}${prefix}${symbol}${chargeText}`, [...hText].length + [...prefix].length];
    return [`${prefix}${symbol}${hText}${chargeText}`, [...prefix].length];
}

const labelColor = symbol => ANSI_COLORS[symbol] ?? ANSI_DEFAULT;

function placeLabels(grid, colors, parsed, positions) {
    const rows = grid.length, cols = grid[0].length;
    parsed.atoms.forEach((atom, index) => {
        const placed = labelText(atom, parsed, index);
        if (!placed) return;
        let [text, anchor] = placed;
        let chars = [...text];
        const [x, y] = positions[index];
        const col = Math.min(Math.max(Math.trunc(x) - anchor, 0), Math.max(cols - chars.length, 0));
        const row = Math.min(Math.max(Math.trunc(y), 0), rows - 1);
        chars = chars.slice(0, cols - col);
        chars.forEach((c, k) => { grid[row][col + k] = c; colors[row][col + k] = labelColor(atom.symbol); });
    });
}

function crop(grid, colors, blank) {
    const usedRows = grid.map((row, r) => row.some(c => c !== blank) ? r : -1).filter(r => r >= 0);
    const usedCols = grid[0].map((_, c) => grid.some(row => row[c] !== blank) ? c : -1).filter(c => c >= 0);
    if (!usedRows.length) return [[[blank]], [[null]]];
    const [top, bottom, left, right] = [usedRows[0], usedRows.at(-1), usedCols[0], usedCols.at(-1)];
    return [grid.slice(top, bottom + 1).map(r => r.slice(left, right + 1)), colors.slice(top, bottom + 1).map(r => r.slice(left, right + 1))];
}

/** Rows of characters and their ANSI codes, joined into text with escape sequences. */
export function ansiText(grid, colors) {
    return grid.map((chars, r) => {
        let line = "", current = null;
        chars.forEach((char, c) => {
            const code = colors[r][c];
            if (code !== current) {
                if (current !== null) line += "\x1b[0m";
                if (code !== null) line += `\x1b[${code}m`;
                current = code;
            }
            line += char;
        });
        return line + (current !== null ? "\x1b[0m" : "");
    }).join("\n");
}

function renderUnbonded(parsed, cols, rows, mode) {
    const blank = GLYPHS[mode].blank;
    const lines = [[]], tints = [[]];
    let col = 0;
    parsed.atoms.forEach((atom, index) => {
        let text = [...labelText(atom, parsed, index)[0]];
        if (mode === "ascii") text = [...translate(text.join(""), PLAIN)];
        if (col && col + text.length > cols) {
            if (lines.length === rows) return;
            lines.push([]); tints.push([]); col = 0;
        }
        text = text.slice(0, cols);
        lines.at(-1).push(...text, blank);
        tints.at(-1).push(...text.map(() => labelColor(atom.symbol)), null);
        col += text.length + 1;
    });
    const width = Math.max(...lines.map(l => l.length)) - 1;
    const pad = (line, fill) => [...line, ...Array(width).fill(fill)].slice(0, width);
    return { grid: lines.map(l => pad(l, blank)), colors: tints.map(t => pad(t, null)) };
}

// ASCII: each bond drawn as characters from its atoms' positions, since a character has too few shapes to stand for a block of dots.
const ASCII_BOND_DOTS = [16, 12, 10, 8, 6];
const FLAT = 0.3, STEEP = 1.5, SIDE = 0.9, TRIM = 0.14;

function put(grid, row, col, char) {
    if (row >= 0 && row < grid.length && col >= 0 && col < grid[0].length && grid[row][col] === " ") grid[row][col] = char;
}

const roundHalfEven = v => { const r = Math.round(v); return Math.abs(v % 1) === 0.5 && r % 2 ? r - 1 : r; };  // as Python's round

function shallowChar(row) {
    const offset = row - roundHalfEven(row);
    return offset > 0.17 ? "." : offset < -0.17 ? "'" : "-";
}

function line(grid, [x1, y1], [x2, y2]) {
    const run = x2 - x1, rise = y2 - y1;
    const steepness = Math.abs(rise) * 2 / Math.max(Math.abs(run), 1e-6);
    if (steepness >= STEEP) {
        const char = steepness > 4 ? "|" : run * rise < 0 ? "/" : "\\";
        const [low, high] = y1 < y2 ? [[x1, y1], [x2, y2]] : [[x2, y2], [x1, y1]];
        for (let row = Math.ceil(low[1] - 0.5); row <= Math.floor(high[1] + 0.5); row++) {
            const place = rise ? (row - y1) / rise : 0;
            put(grid, row, roundHalfEven(x1 + run * place), char);
        }
        return;
    }
    for (let col = Math.ceil(Math.min(x1, x2) - 0.5); col <= Math.floor(Math.max(x1, x2) + 0.5); col++) {
        const row = y1 + rise * (col - x1) / run;
        put(grid, roundHalfEven(row), col, steepness < FLAT ? "-" : shallowChar(row));
    }
}

function sides(order, start, end, towards) {
    if (order === 1) return [[0, 0]];
    const run = end[0] - start[0], rise = (end[1] - start[1]) * 2;
    const length = Math.hypot(run, rise) || 1;
    let normal = [-rise / length * SIDE * 2, run / length * SIDE];
    const middle = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
    if ((towards[0] - middle[0]) * normal[0] + (towards[1] - middle[1]) * normal[1] < 0) normal = [-normal[0], -normal[1]];
    return order === 2 ? [[0, 0], normal] : [[0, 0], normal, [-normal[0], -normal[1]]];
}

export function asciiDraw(bonds, positions, rows, cols) {
    const grid = Array.from({ length: rows }, () => Array(cols).fill(" "));
    const middle = [positions.reduce((s, p) => s + p[0], 0) / positions.length, positions.reduce((s, p) => s + p[1], 0) / positions.length];
    for (const [first, second, order] of bonds) {
        const a = positions[first], b = positions[second];
        let near = bonds.filter(([f, o]) => [first, second].includes(f) || [first, second].includes(o))
                        .map(([f, o]) => positions[[first, second].includes(f) ? o : f]);
        near = near.filter(p => p !== a && p !== b);
        const towards = near.length ? [near.reduce((s, p) => s + p[0], 0) / near.length, near.reduce((s, p) => s + p[1], 0) / near.length] : middle;
        sides(order, a, b, towards).forEach(([dx, dy], index) => {
            const trim = index === 0 ? TRIM : TRIM * 2;
            line(grid, [a[0] + (b[0] - a[0]) * trim + dx, a[1] + (b[1] - a[1]) * trim + dy],
                       [b[0] + (a[0] - b[0]) * trim + dx, b[1] + (a[1] - b[1]) * trim + dy]);
        });
    }
    return grid;
}

function asciiBondDots(parsed, cols, rows) {
    const mean = meanBondLength(parsed), xs = parsed.coords.map(p => p[0]), ys = parsed.coords.map(p => p[1]);
    const wide = (Math.max(...xs) - Math.min(...xs)) / mean, tall = (Math.max(...ys) - Math.min(...ys)) / mean;
    const margin = 0.84;
    return ASCII_BOND_DOTS.find(dots => wide * dots / CELL_WIDTH <= cols * margin && tall * dots / CELL_HEIGHT <= rows * margin) ?? ASCII_BOND_DOTS.at(-1);
}

/** Render a parsed molecule into at most cols x rows characters. Returns { grid, colors, text, ansi }.
 *  Braille and octants need `canvas`, a real canvas for RDKit to draw on and the browser to rasterize; ASCII needs none. */
export function render(parsed, { cols = 80, rows = 30, mode = "braille", canvas = null }) {
    let grid, colors;
    if (!parsed.bonds.length) {
        ({ grid, colors } = renderUnbonded(parsed, cols, rows, mode));
    } else {
        const glyphs = GLYPHS[mode];
        const width = cols * CELL_WIDTH * SUPERSAMPLE, height = rows * CELL_HEIGHT * SUPERSAMPLE;
        const bondDots = mode === "ascii" ? asciiBondDots(parsed, cols, rows) : MAX_BOND_DOTS;
        if (mode !== "ascii" && !canvas) throw new Error(`${mode} needs a canvas to draw on`);
        const drawn = drawBonds(parsed, width, height, bondDots, mode === "ascii" ? null : canvas);
        const positions = atomPositions(drawn.svg, parsed, width, height, bondDots, drawn.points)
            .map(([x, y]) => [x / (CELL_WIDTH * SUPERSAMPLE), y / (CELL_HEIGHT * SUPERSAMPLE)]);
        if (mode === "ascii") {
            const bonds = parsed.bonds.map(({ a, b, order }) => [a, b, Math.round(order)]);
            grid = asciiDraw(bonds, positions.map(([x, y]) => [x - 0.5, y - 0.5]), rows, cols);
            colors = grid.map(row => row.map(() => null));
        } else {
            const pixels = drawn.context.getImageData(0, 0, width, height).data;
            grid = dotGrid(pixels, width, height, cols, rows, glyphs);
            colors = cellColors(pixels, width, height, cols, rows);
        }
        colors = colors.map((row, r) => row.map((code, c) => grid[r][c] !== glyphs.blank ? code : null));
        placeLabels(grid, colors, parsed, positions);
        if (mode === "ascii") grid = grid.map(row => row.map(c => PLAIN[c] ?? c));
        [grid, colors] = crop(grid, colors, glyphs.blank);
    }
    return { grid, colors, text: grid.map(r => r.join("")).join("\n"), ansi: ansiText(grid, colors) };
}

/** RDKit's own picture of the molecule, with labels and its usual colours, for terminals that show images. */
export function pictureSvg(parsed, width, height, bondPixels) {
    const mean = meanBondLength(parsed);
    return parsed.mol.get_svg_with_highlights(JSON.stringify({
        width, height, padding: 0.05, ...(mean > 0 ? { fixedBondLength: bondPixels / mean } : {}),
        bondLineWidth: Math.max(1, Math.round(bondPixels / 18)), scaleBondWidth: false, addStereoAnnotation: false,
    }));
}

const CHUNK = 4096;  // the Kitty protocol's limit for one escape sequence's payload

/** Kitty graphics escape sequences that place a PNG (as base64) at the cursor, `cols` cells wide, then move to the next line. */
export function kittySequence(base64, cols) {
    const chunks = [];
    for (let i = 0; i < base64.length; i += CHUNK) chunks.push(base64.slice(i, i + CHUNK));
    return chunks.map((chunk, index) => {
        const keys = index === 0 ? `a=T,f=100,c=${cols},q=2,` : "q=2,";
        return `\x1b_G${keys}m=${index < chunks.length - 1 ? 1 : 0};${chunk}\x1b\\`;
    }).join("");
}
