'use strict';
// The terminal viewer, one step at a time, as molterm's editor works: the client sends what was typed and clicked (raw, as hex) with
// the state from the last answer; this turns the view, draws it with Mol*, and answers with what to print and the new state.

const { Quat, Vec3 } = require('molstar/lib/commonjs/mol-math/linear-algebra');
const kitty = require('./kitty');
const renderer = require('./renderer');
const structures = require('./structures');

const IDS = [3301, 3302]; // the picture's ids, taken in turn, so the new one is up before the old goes
const STEP = Math.PI / 12; // an arrow key turns this far
const ZOOM = 1.15; // a wheel click or + zooms this much
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 25;

class StateError extends Error {}

function encode(state) {
    return Buffer.from(JSON.stringify(state)).toString('base64url');
}

function decode(text) {
    try {
        const state = JSON.parse(Buffer.from(text, 'base64url').toString());
        if (typeof state.k !== 'string' || !Array.isArray(state.q) || state.q.length !== 4 || !state.q.every(Number.isFinite)
            || !Number.isFinite(state.z) || !Array.isArray(state.p) || state.p.length !== 2 || !state.p.every(Number.isFinite)) throw new Error();
        return state;
    } catch {
        throw new StateError('The state is damaged: start again.');
    }
}

/** Keys and mouse reports in what the terminal sent: [{ key }] or [{ mouse: { button, x, y, press, motion, shift, wheel } }]. */
function events(raw) {
    const out = [];
    let i = 0;
    while (i < raw.length) {
        const rest = raw.slice(i);
        let match = rest.match(/^\x1b\[<(\d+);(\d+);(\d+)([Mm])/);
        if (match) {
            const code = +match[1];
            const wheel = code & 64 ? (code & 1 ? 'down' : 'up') : null;
            out.push({ mouse: { button: code & 3, x: +match[2], y: +match[3], press: match[4] === 'M', motion: !!(code & 32),
                shift: !!(code & 4), ctrl: !!(code & 16), wheel } });
            i += match[0].length;
            continue;
        }
        match = rest.match(/^\x1b(?:\[(?:1;(\d))?|O)([ABCD])/);
        if (match) {
            out.push({ key: { A: 'up', B: 'down', C: 'right', D: 'left' }[match[2]], shift: match[1] === '2' });
            i += match[0].length;
            continue;
        }
        match = rest.match(/^\x1b\[[0-9;?]*[ -\/]*[@-~]/) || rest.match(/^\x1b[P\]_^][\s\S]*?(?:\x1b\\|\x07)/);
        if (match) { // another report, such as a focus change, that means nothing here
            i += match[0].length;
            continue;
        }
        if (rest[0] === '\x1b') {
            out.push({ key: 'escape' });
            i += 1;
            continue;
        }
        out.push({ key: rest[0] });
        i += 1;
    }
    return out;
}

/** Turn by `angle` about an axis given in the screen's terms ([1,0,0] across, [0,1,0] up), from the current view. */
function turn(state, info, screenAxis, angle) {
    const q = Quat.create(...state.q);
    const up = Vec3.transformQuat(Vec3(), info.up, q);
    const direction = Vec3.transformQuat(Vec3(), info.direction, q);
    const right = Vec3.normalize(Vec3(), Vec3.cross(Vec3(), up, direction));
    const axis = Vec3.normalize(Vec3(), Vec3.add(Vec3(), Vec3.scale(Vec3(), right, screenAxis[0]), Vec3.scale(Vec3(), up, screenAxis[1])));
    const delta = Quat.setAxisAngle(Quat(), axis, angle);
    state.q = Array.from(Quat.normalize(Quat(), Quat.multiply(Quat(), delta, q)));
}

function zoom(state, factor) {
    state.z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, state.z * factor));
}

/** The state's lighting: states from before there was a choice have none, and were drawn in Mol*'s standard light. */
function lighting(state) {
    return renderer.LIGHTINGS.includes(state.l) ? state.l : 'standard';
}

function cycle(list, value, by) {
    return list[(list.indexOf(value) + by + list.length) % list.length];
}

/** Apply the events to the state. Returns 'quit' to finish, else whether anything changed. `size` is the picture's size, in the units the
 * mouse reports come in, to turn a drag across it into half a turn. */
function apply(state, info, list, size) {
    let changed = false;
    for (const event of list) {
        if (event.key) {
            const key = event.key;
            if (key === 'q' || key === 'Q' || key === 'escape' || key === '\x03' || key === '\x11') return 'quit';
            if (['up', 'down', 'left', 'right'].includes(key)) {
                const [dx, dy] = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[key];
                if (event.shift) state.p = [state.p[0] + dx * 0.1 / state.z, state.p[1] - dy * 0.1 / state.z];
                else if (dx) turn(state, info, [0, 1], -dx * STEP);
                else turn(state, info, [1, 0], -dy * STEP);
            } else if (key === '+' || key === '=') zoom(state, ZOOM);
            else if (key === '-' || key === '_') zoom(state, 1 / ZOOM);
            else if (key === 's' || key === 'S') state.s = cycle(renderer.STYLES, state.s, key === 's' ? 1 : -1);
            else if (key === 'c' || key === 'C') state.c = cycle(renderer.COLOURS, state.c, key === 'c' ? 1 : -1);
            else if (key === 'l' || key === 'L') state.l = cycle(renderer.LIGHTINGS, lighting(state), key === 'l' ? 1 : -1);
            else if (key === 'h' || key === 'H') state.h = !state.h;
            else if (key === 'r' || key === 'R') Object.assign(state, { q: [0, 0, 0, 1], z: 1, p: [0, 0] });
            else if (key === '?') state.help = !state.help;
            else continue;
            changed = true;
            continue;
        }
        const m = event.mouse;
        if (m.wheel) {
            zoom(state, m.wheel === 'up' ? ZOOM : 1 / ZOOM);
            changed = true;
            continue;
        }
        if (m.press && !m.motion) { // a button goes down: a drag starts here
            state.d = [m.x, m.y, m.button === 2 || m.shift ? 'move' : m.button === 1 ? 'zoom' : 'turn'];
            continue;
        }
        if (!m.press) {
            state.d = null;
            continue;
        }
        if (!state.d || m.button === 3) continue; // moving with no button held
        const [x0, y0, kind] = state.d;
        const dx = (m.x - x0) / size[0];
        const dy = (m.y - y0) / size[1];
        state.d = [m.x, m.y, kind];
        if (!dx && !dy) continue;
        if (kind === 'turn') {
            const length = Math.hypot(dx, dy);
            // about the axis across the drag, as a trackball: the near side follows the pointer (the camera goes the other way)
            turn(state, info, [dy / length, dx / length], -Math.PI * length);
        } else if (kind === 'move') {
            const aspect = size[0] / size[1];
            state.p = [state.p[0] + 2 * dx / state.z * Math.max(aspect, 1), state.p[1] - 2 * dy / state.z / Math.min(aspect, 1)];
        } else {
            zoom(state, Math.exp(-dy * 3));
        }
        changed = true;
    }
    return changed;
}

function at(row, col) {
    return `\x1b[${row + 1};${col + 1}H`;
}

function clip(text, width) {
    return [...text].length > width ? [...text].slice(0, Math.max(0, width - 1)).join('') + '…' : text;
}

const HELP = 'drag turn · right-drag move · wheel zoom · arrows turn · s style · c colour · l lighting · h hydrogens · r reset · q quit';

function statusLine(state, structure, info, cols) {
    // a PDB title can be long, and the facts matter more: what the keys have changed
    const label = structure.label && structure.label !== structure.name ? clip(structure.label, Math.max(12, Math.floor(cols / 4))) : '';
    const what = [structure.name, label].filter(Boolean).join(' · ');
    const facts = [`${info.atoms.toLocaleString()} atoms`, info.residues > 2 ? `${info.residues.toLocaleString()} residues` : '',
        state.s === 'auto' ? `auto (${info.style})` : info.style, `colour ${state.c}`, lighting(state),
        state.h ? 'H shown' : 'H hidden'].filter(Boolean).join(' · ');
    const hint = state.help ? HELP : '? keys · q quit';
    const left = clip(`${what}  ${facts}`, Math.max(0, cols - [...hint].length - 2));
    const gap = Math.max(1, cols - [...left].length - [...hint].length);
    return `\x1b[1m${left}\x1b[22m` + ' '.repeat(gap) + `\x1b[2m${clip(hint, cols - [...left].length - 1)}\x1b[22m`;
}

function colourNumber(hex) {
    return /^[0-9a-fA-F]{6}$/.test(hex || '') ? parseInt(hex, 16) : null;
}

/**
 * One step. `form` has cols, rows, events (hex), state or input/data/format to begin, and optionally cell_width, cell_height, pixels
 * (mouse reports in pixels), placeholders (through tmux), background and theme. Returns { status, state, output }: status 0 to go on,
 * 1 finished, 2 failed (output is the reason).
 */
async function step(form) {
    const cols = Math.min(Math.max(parseInt(form.cols, 10) || 80, 20), 500);
    const rows = Math.min(Math.max(parseInt(form.rows, 10) || 24, 5), 200);
    const cellWidth = Math.min(Math.max(parseInt(form.cell_width, 10) || 10, 4), 100);
    const cellHeight = Math.min(Math.max(parseInt(form.cell_height, 10) || 20, 8), 200);
    const pixels = form.pixels === '1';
    const through = form.placeholders === '1';
    const raw = Buffer.from((form.events || '').replace(/[^0-9a-fA-F]/g, '').replace(/^((?:..)*).?$/, '$1'), 'hex').toString('latin1');

    let state;
    let structure;
    let first = false;
    if (form.state) {
        state = decode(form.state);
        structure = await structures.known(state.k);
        if (!structure && state.i) structure = await structures.resolve({ input: state.i });
        if (!structure) return { status: 2, state: '', output: 'The structure is no longer held here: open it again.' };
    } else {
        structure = await structures.resolve({ input: form.input || '', data: form.data || '', format: form.format || '' });
        state = { k: structure.key, q: [0, 0, 0, 1], z: 1, p: [0, 0], s: renderer.STYLES.includes(form.style) ? form.style : 'auto',
            c: renderer.COLOURS.includes(form.colour) ? form.colour : 'auto', h: form.hydrogens !== '0', f: 0, d: null, size: '',
            l: renderer.LIGHTINGS.includes(form.lighting) ? form.lighting : renderer.LIGHTINGS[0] };
        if (structure.kind === 'molecule' && structure.input.length <= 2000) state.i = structure.input;
        first = true;
    }

    const imageRows = rows - 1;
    const look = () => ({ style: state.s, colour: state.c, hydrogens: state.h, lighting: lighting(state) });
    const view = () => ({ q: state.q, z: state.z, p: state.p, ...(state.home ? { u: state.home.u, d: state.home.d } : {}) });
    const background = colourNumber(form.background) ?? (form.theme === 'dark' ? 0x161615 : 0xffffff);
    const width = cols * cellWidth;
    const height = imageRows * cellHeight;

    // the turns need the structure's home view, which drawing it gives
    let drawn = null;
    if (first || !state.home) {
        drawn = await renderer.draw(structure, look(), view(), width, height, background);
        state.home = { u: Array.from(drawn.info.up), d: Array.from(drawn.info.direction) };
    }
    const info = { up: Vec3.create(...state.home.u), direction: Vec3.create(...state.home.d) };
    const size = pixels ? [width, height] : [cols, imageRows];
    const result = apply(state, info, events(raw), size);
    if (result === 'quit') return { status: 1, state: encode(state), output: '' };
    const sized = `${cols}x${rows}`;
    if (!result && !first && state.size === sized) return { status: 0, state: encode(state), output: '' };
    state.size = sized;
    if (result || !drawn) drawn = await renderer.draw(structure, look(), view(), width, height, background);

    const old = IDS[state.f];
    state.f = 1 - state.f;
    const id = IDS[state.f];
    let output = first ? '\x1b[2J' : '';
    if (through) {
        output += kitty.transmit(drawn.png, id, cols, imageRows) + kitty.cells(id, cols, imageRows, (row) => at(row, 0));
        output += kitty.remove(old, true);
    } else {
        output += kitty.remove(id, false) + at(0, 0) + kitty.place(drawn.png, id, cols, imageRows) + kitty.remove(old, false);
    }
    output += at(rows - 1, 0) + '\x1b[2K' + statusLine(state, structure, drawn.info, cols);
    return { status: 0, state: encode(state), output };
}

const PRINT_ROWS = 16;
const PRINT_ASPECT = 4 / 3; // a printed picture's shape, in pixels, when the terminal is wide enough

/**
 * A picture to print into the shell, for `molterm3d -p`: `form` has input/data/format, style, colour, lighting, hydrogens, turn and tilt
 * (degrees), rows (the picture's height), cols (the terminal's width), cell_width, cell_height, placeholders and background. The picture
 * goes at the cursor and the cursor below it, so pictures printed one after another scroll with the text. Returns the text to print.
 */
async function print(form) {
    const number = (name, fallback, low, high) => Math.min(Math.max(parseInt(form[name], 10) || fallback, low), high);
    const cols = number('cols', 80, 10, 500);
    const rows = number('rows', PRINT_ROWS, 2, kitty.MAX_CELLS);
    const cellWidth = number('cell_width', 10, 4, 100);
    const cellHeight = number('cell_height', 20, 8, 200);
    const structure = await structures.resolve({ input: form.input || '', data: form.data || '', format: form.format || '' });
    const look = { style: renderer.STYLES.includes(form.style) ? form.style : 'auto',
        colour: renderer.COLOURS.includes(form.colour) ? form.colour : 'auto', hydrogens: form.hydrogens !== '0',
        lighting: renderer.LIGHTINGS.includes(form.lighting) ? form.lighting : renderer.LIGHTINGS[0] };
    const radians = (name) => (parseFloat(form[name]) || 0) * Math.PI / 180;
    const pictureCols = Math.max(1, Math.min(cols, kitty.MAX_CELLS, Math.round(rows * cellHeight * PRINT_ASPECT / cellWidth)));
    const background = colourNumber(form.background) ?? (form.theme === 'dark' ? 0x161615 : 0xffffff);
    const { png } = await renderer.draw(structure, look, { turn: radians('turn'), tilt: radians('tilt'), z: 1, p: [0, 0] },
        pictureCols * cellWidth, rows * cellHeight, background);
    if (form.placeholders === '1') return kitty.lines(png, 256 + Math.floor(Math.random() * (255 * 297 - 257)), pictureCols, rows);
    return kitty.inline(png, pictureCols, rows);
}

module.exports = { step, print, events, apply, encode, decode, statusLine, StateError, IDS };
