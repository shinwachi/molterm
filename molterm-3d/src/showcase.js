'use strict';
// The automated demo at /showcase, in a web terminal with Kitty graphics, as molterm's own: the viewer turned with the mouse, the
// lightings and styles side by side, and pictures printed into a shell that scroll with its text.
//
// Every frame is real output: the viewer's frames are mouse and key input put through viewer.step, as the client would send it, and the
// printed pictures come from viewer.print, as `molterm3d -p` gets them. The page only prints the frames in turn and moves a pointer to
// show where the input went. The demo comes in parts, so that the page can play the first while the server draws the rest.

const kitty = require('./kitty');
const renderer = require('./renderer');
const structures = require('./structures');
const viewer = require('./viewer');

const MIN_COLS = 90;
const MIN_ROWS = 30;
const BACKGROUND = 0xffffff;
const HOME = '\x1b_Ga=d,q=2\x1b\\\x1b[0m\x1b[2J\x1b[3J\x1b[H'; // no pictures, nothing on screen and nothing to scroll back to
const BOLD = '\x1b[1m';
const PLAIN = '\x1b[0m';
const PRINT_CELL = 10; // pixels per column of a printed picture: several stay in the web terminal's store of images at once
const PRINT_ROWS = 12; // each printed picture's -r, so that a few of them fill the screen

const CAFFEINE = 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C';
const IMATINIB = 'Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1';
const IBUPROFEN = 'CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O';

const PRINTED = [
    ['-p 1crn', { input: '1crn' },
        'molterm3d -p prints a structure into the shell, the way a command prints text. Crambin, in illustrative lighting, the default.'],
    ['-p -l standard 1crn', { input: '1crn', lighting: 'standard' },
        'Each command adds its picture to the shell\'s history. The same protein in -l standard, Mol*\'s usual light.'],
    ['-p -s spacefill 4hhb', { input: '4hhb', style: 'spacefill' },
        'Haemoglobin as spacefill: in illustrative lighting each chain takes Goodsell\'s colours, with lighter carbons.'],
    [`-p '${CAFFEINE}'`, { input: CAFFEINE },
        'A SMILES works too: molterm\'s RDKit puts it in 3D, and Mol* draws it with balls and sticks.'],
    ['-p -s surface 1stp', { input: '1stp', style: 'surface' },
        'When the screen fills, the shell scrolls, and the pictures scroll with their lines...'],
    [`-p -s licorice -l outline '${IBUPROFEN}'`, { input: IBUPROFEN, style: 'licorice', lighting: 'outline' },
        '...as many as are printed, into the terminal\'s own scrollback.'],
];

class ShowcaseError extends Error {}

function frame(caption, output, { pointer = null, click = false, key = null, pause = 2200, scroll = 0 } = {}) {
    return { caption, output, pointer, click, key, pause, scroll };
}

function at(row, col) {
    return `\x1b[${row + 1};${col + 1}H`;
}

function hex(text) {
    return Buffer.from(text, 'latin1').toString('hex');
}

/** A picture over cols by rows cells at the cursor, with no id, the cursor staying put. */
function still(png, cols, rows) {
    const payload = Buffer.from(png).toString('base64');
    const parts = [];
    for (let i = 0; i < payload.length; i += 4096) parts.push(payload.slice(i, i + 4096));
    return parts.map((chunk, index) => `\x1b_G${index === 0 ? `a=T,f=100,c=${cols},r=${rows},C=1,` : ''}q=2,m=${index < parts.length - 1 ? 1 : 0};${chunk}\x1b\\`).join('');
}

/** The viewer, driven the way the client would drive it, collecting a frame for each step. */
class Session {
    constructor(grid, input, options = {}) {
        this.grid = grid;
        this.input = input;
        this.options = options;
        this.state = '';
        this.frames = [];
    }

    get centre() {
        return [this.grid.cols / 2, (this.grid.rows - 1) / 2];
    }

    async send(raw) {
        const form = { cols: this.grid.cols, rows: this.grid.rows, cell_width: this.grid.cellWidth, cell_height: this.grid.cellHeight,
            background: BACKGROUND.toString(16).padStart(6, '0'), events: hex(raw) };
        if (this.state) form.state = this.state;
        else Object.assign(form, { input: this.input }, this.options);
        const answer = await viewer.step(form);
        if (answer.status === 2) throw new ShowcaseError(answer.output);
        this.state = answer.state;
        return answer.output;
    }

    report(code, [col, row], end = 'M') {
        return `\x1b[<${code};${Math.floor(col) + 1};${Math.floor(row) + 1}${end}`;
    }

    async open(command, caption, pause = 2600) {
        // the shell's line, then the viewer on the other screen, as the client switches to it
        const output = await this.send('');
        this.frames.push(frame(caption, `${HOME}\x1b[?25h$ ${command}\r\n\x1b[?1049h\x1b[?25l${output}`, { pause }));
    }

    async key(name, caption, { pause = 2200, shown } = {}) {
        const output = await this.send(name);
        this.frames.push(frame(caption, output, { key: shown || name, pause }));
    }

    /** Press at `start`, move to `end` in a few steps, each drawn as the client would get it, and let go there. */
    async drag(start, end, caption, { button = 0, steps = 4, pause = 1800 } = {}) {
        for (let step = 1; step <= steps; step++) {
            const along = start.map((a, i) => a + (end[i] - a) * step / steps);
            let raw = (step === 1 ? this.report(button, start) : '') + this.report(32 + button, along);
            if (step === steps) raw += this.report(button, along, 'm');
            this.frames.push(frame(caption, await this.send(raw), { pointer: along, pause: step === steps ? pause : 120 }));
        }
    }

    async wheel(up, times, caption, pause = 1600) {
        for (let i = 0; i < times; i++) {
            const output = await this.send(this.report(up ? 64 : 65, this.centre));
            this.frames.push(frame(caption, output, { pointer: this.centre, key: up ? 'Wheel ↑' : 'Wheel ↓', pause: i === times - 1 ? pause : 250 }));
        }
    }

    async quit(caption, pause = 2600) {
        await this.send('q');
        const gone = viewer.IDS.map((id) => kitty.remove(id, false)).join('');
        this.frames.push(frame(caption, `${gone}\x1b[?1049l\x1b[?25h$ `, { key: 'q', pause }));
    }
}

async function viewing(grid) {
    const run = new Session(grid, '4hhb');
    const [x, y] = run.centre;
    const span = grid.cols / 5;
    await run.open('molterm3d 4hhb', 'molterm3d opens a structure in the terminal: haemoglobin, fetched from the PDB and drawn by Mol* on the '
        + 'server, in illustrative lighting. The status line says what it is and how it\'s drawn.', 4200);
    await run.drag([x - span, y], [x + span, y], 'Dragging turns it, as a trackball: the side nearest you follows the pointer.');
    await run.drag([x, y - span / 3], [x, y + span / 3], 'Up and down too. Each movement goes to the server, which draws the new view in about a tenth of a second.');
    await run.wheel(true, 3, 'The wheel zooms...');
    await run.drag([x + span / 2, y], [x - span / 2, y + 2], '...a right-drag moves it across...', { button: 2, steps: 3 });
    await run.key('r', '...and r puts it back where it started.', { pause: 1800 });
    await run.key('l', 'l steps through the lighting. Standard is Mol*\'s usual light, with shading and highlights...', { pause: 2600 });
    await run.key('l', '...occlusion darkens the crevices, as ambient light would...', { pause: 2600 });
    await run.key('l', '...outline adds the black edges alone...', { pause: 2400 });
    await run.key('l', '...and illustrative has both, with flat colour, after David Goodsell\'s drawings.', { pause: 2600 });
    await run.key('c', 'c steps through the colours: by element...', { pause: 1400 });
    await run.key('c', '...by chain...', { pause: 1400 });
    await run.key('c', '...by secondary structure...', { pause: 2000 });
    await run.key('c', '...along the sequence, as a rainbow, and on to residues, B-factors and Goodsell\'s own.', { pause: 2600 });
    await run.quit('q leaves the viewer. Its pictures go with it, and the shell is as it was.');
    return run.frames;
}

async function styling(grid) {
    const run = new Session(grid, '4hhb', { style: 'spacefill' });
    const [x, y] = run.centre;
    const span = grid.cols / 5;
    await run.open('molterm3d -s spacefill 4hhb', 'Options choose the first look: -s spacefill. In illustrative lighting, spacefill takes Goodsell\'s colours, '
        + 'each chain its own with lighter carbons.', 4200);
    await run.drag([x - span / 2, y + span / 4], [x + span / 2, y - span / 4], 'It turns like the cartoon did, every atom drawn.', { steps: 3 });
    await run.key('s', 's steps through the styles: licorice...', { pause: 2000 });
    await run.key('s', '...cartoon...', { pause: 1400 });
    await run.key('s', '...the molecular surface...', { pause: 2600 });
    await run.drag([x + span / 2, y], [x - span / 2, y], 'A surface shows the shape a ligand meets.', { steps: 3, pause: 1400 });
    await run.key('s', '...and back to auto: a cartoon for a protein, balls and sticks for anything else.', { pause: 2400 });
    await run.quit('Back to the shell.', 1600);
    return run.frames;
}

async function molecule(grid) {
    const run = new Session(grid, IMATINIB);
    const [x, y] = run.centre;
    const span = grid.cols / 5;
    await run.open(`molterm3d '${IMATINIB}'`, 'Anything molterm reads opens the same way: here imatinib\'s SMILES, put into 3D by RDKit, with its hydrogens.', 3600);
    await run.drag([x - span / 2, y - 1], [x + span / 2, y + 1], 'A drug can be turned over like a protein.');
    await run.key('h', 'h hides the hydrogens...', { pause: 2000 });
    await run.key('l', '...and l changes the light, here to Mol*\'s standard.', { pause: 2400 });
    await run.quit('Back to the shell.', 1800);
    return run.frames;
}

/** A structure several ways, side by side, each labelled with the option that gives it. */
async function sideBySide(grid, title, input, looks, across) {
    const structure = await structures.resolve({ input });
    const down = Math.ceil(looks.length / across);
    const width = Math.floor((grid.cols - (across - 1) * 2) / across);
    const height = Math.floor((grid.rows - 2) / down) - 1;
    let out = HOME + at(0, 0) + BOLD + title + PLAIN + '\x1b[?25l';
    for (const [index, [label, look]] of looks.entries()) {
        const top = 2 + Math.floor(index / across) * (height + 1);
        const left = (index % across) * (width + 2);
        const { png } = await renderer.draw(structure, { style: 'auto', colour: 'auto', hydrogens: true, lighting: 'illustrative', ...look },
            { turn: 0.5, tilt: 0.2, z: 1, p: [0, 0] }, width * grid.cellWidth, (height - 1) * grid.cellHeight, BACKGROUND);
        out += at(top, left) + `\x1b[2m${label}\x1b[22m` + at(top + 1, left) + still(png, width, height - 1);
    }
    return out;
}

async function comparing(grid) {
    const lightings = renderer.LIGHTINGS.map((l) => [`-l ${l}`, { lighting: l }]);
    const styles = [['(auto) cartoon', {}], ['-s ball-and-stick', { style: 'ball-and-stick' }], ['-s licorice', { style: 'licorice' }],
        ['-s spacefill', { style: 'spacefill' }], ['-s surface', { style: 'surface' }], ['-c rainbow', { colour: 'rainbow' }]];
    const colours = [['-s spacefill', { style: 'spacefill' }], ['-s spacefill -l standard', { style: 'spacefill', lighting: 'standard' }],
        ['-s surface', { style: 'surface' }], ['-s surface -c rainbow', { style: 'surface', colour: 'rainbow' }]];
    return [
        frame('The four lightings side by side: illustrative, standard, occlusion and outline.',
            await sideBySide(grid, 'Haemoglobin (4hhb), in each lighting', '4hhb', lightings, 2), { pause: 5500 }),
        frame('The styles, all in illustrative lighting. Auto draws a protein as a cartoon and anything else as balls and sticks.',
            await sideBySide(grid, 'Streptavidin (1stp), in each style', '1stp', styles, 3), { pause: 5500 }),
        frame('Illustrative lighting is at its best on the solid styles, in Goodsell\'s colours; other lights and colours are an option away.',
            await sideBySide(grid, 'Lysozyme (1lyz), solid', '1lyz', colours, 2), { pause: 5500 }),
    ];
}

async function scrolled(grid) {
    const shown = [];
    for (const [index, [options, form, caption]] of PRINTED.entries()) {
        const command = `$ molterm3d ${options}`.slice(0, grid.cols - 1);
        const picture = await viewer.print({ ...form, rows: PRINT_ROWS, cols: grid.cols, cell_width: PRINT_CELL,
            cell_height: Math.round(PRINT_CELL * grid.cellHeight / grid.cellWidth), background: BACKGROUND.toString(16) });
        const start = index === 0 ? HOME + '\x1b[?25h' : '';
        shown.push(frame(caption, `${start}${command}\r\n${picture}\r\n`, { pause: index < 4 ? 3600 : 2400 }));
    }
    shown.push(frame('The prompt waits below the last one.', '$ ', { pause: 2400 }));
    shown.push(frame('Scrolling back up...', '', { pause: 1400, scroll: -15 }));
    shown.push(frame('...the earlier pictures come back, still beside their commands, as the terminal keeps them in its history...', '', { pause: 2400, scroll: -15 }));
    shown.push(frame('...all the way to the first.', '', { pause: 2800, scroll: -100 }));
    shown.push(frame('And down again to the prompt. Nothing is redrawn: the terminal itself holds the pictures.', '', { pause: 3200, scroll: 150 }));
    return shown;
}

const PARTS = [viewing, styling, comparing, molecule, scrolled];
const made = new Map(); // grid and part -> promise of its frames, so that a size asked for again, or twice at once, is drawn once
const KEPT = 24;

/** One part of the demo for a grid { cols, rows, cellWidth, cellHeight }: its frames. Raises ShowcaseError if the grid is too small. */
function part(grid, index) {
    if (grid.cols < MIN_COLS || grid.rows < MIN_ROWS) throw new ShowcaseError(`The demo needs at least ${MIN_COLS} columns and ${MIN_ROWS} rows.`);
    if (!(index >= 0 && index < PARTS.length)) throw new ShowcaseError(`There are ${PARTS.length} parts, from 0.`);
    const key = `${grid.cols}x${grid.rows}x${grid.cellWidth}x${grid.cellHeight}:${index}`;
    if (!made.has(key)) {
        const work = PARTS[index](grid);
        work.catch(() => made.delete(key));
        made.set(key, work);
        while (made.size > KEPT) made.delete(made.keys().next().value);
    }
    return made.get(key);
}

module.exports = { part, PARTS, ShowcaseError, MIN_COLS, MIN_ROWS };
