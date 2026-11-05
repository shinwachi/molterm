'use strict';
// What to draw: a PDB entry by its id (fetched from RCSB once, kept in data/pdb), a file's contents (PDB, mmCIF, SDF/MOL, MOL2, XYZ),
// or anything molterm reads (SMILES, a molblock), which molterm's /api/v1/conformer.sdf puts in 3D.

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const DATA = path.join(__dirname, '..', 'data');
const MOLTERM_URL = (process.env.MOLTERM_URL || 'http://molterm.localhost').replace(/\/$/, '');
const RCSB = 'https://files.rcsb.org/download';
const MAX_BYTES = 20 * 1024 * 1024;
const KEPT = 64; // structures held in memory
const PDB_ID = /^[0-9][A-Za-z0-9]{3}$/;

class StructureError extends Error {
    constructor(code, message) {
        super(message);
        this.code = code;
    }
}

const memory = new Map(); // key -> structure, oldest first

function keep(structure) {
    memory.delete(structure.key);
    memory.set(structure.key, structure);
    while (memory.size > KEPT) memory.delete(memory.keys().next().value);
    return structure;
}

/** The format of a file's text, as Mol* names it, or null. */
function detect(text) {
    const head = text.slice(0, 4000);
    if (/^data_/m.test(head) && /^_atom_site\.|^loop_/m.test(text.slice(0, 200000))) return 'mmcif';
    if (/^@<TRIPOS>/m.test(head)) return 'mol2';
    if (/^(ATOM  |HETATM|HEADER|CRYST1|MODEL )/m.test(head)) return 'pdb';
    if (/V[23]000/.test(head) || /^\$\$\$\$/m.test(text)) return 'sdf';
    if (/^\s*\d+\s*\n.*\n\s*[A-Z][a-z]?\s+-?\d/.test(head)) return 'xyz';
    return null;
}

/** The first title a file gives itself, for the status line. */
function title(text, format) {
    if (format === 'mmcif') {
        const match = text.match(/^_struct\.title\s+(?:'([^'\n]*)'|"([^"\n]*)"|([^\s'"].*?))[ \t]*$/m);
        return match ? (match[1] || match[2] || match[3]).trim() : '';
    }
    if (format === 'pdb') return text.split('\n').filter((l) => l.startsWith('TITLE ')).map((l) => l.slice(10).trim()).join(' ');
    if (format === 'sdf' || format === 'xyz') return (text.split('\n')[format === 'xyz' ? 1 : 0] || '').trim();
    if (format === 'mol2') return (text.split('@<TRIPOS>MOLECULE')[1] || '').split('\n')[1]?.trim() || '';
    return '';
}

function hash(text) {
    return crypto.createHash('sha256').update(text).digest('hex').slice(0, 24);
}

async function fetchText(url, options = {}) {
    let answer;
    try {
        answer = await fetch(url, { ...options, signal: AbortSignal.timeout(30000) });
    } catch (e) {
        throw new StructureError('unavailable', `${new URL(url).host} can't be reached.`);
    }
    const text = await answer.text();
    return { status: answer.status, text };
}

async function pdbEntry(id) {
    id = id.toUpperCase();
    const key = `pdb:${id}`;
    if (memory.has(key)) return keep(memory.get(key));
    const file = path.join(DATA, 'pdb', `${id}.cif`);
    let text = null;
    try {
        text = fs.readFileSync(file, 'utf8');
    } catch {
        const answer = await fetchText(`${RCSB}/${id}.cif`);
        if (answer.status === 404) throw new StructureError('not_found', `There's no PDB entry ${id}.`);
        if (answer.status !== 200) throw new StructureError('unavailable', `RCSB answered ${answer.status} for ${id}.`);
        if (answer.text.length > MAX_BYTES) throw new StructureError('too_large', `PDB entry ${id} is too large to draw here.`);
        text = answer.text;
        try {
            fs.mkdirSync(path.dirname(file), { recursive: true });
            fs.writeFileSync(file, text);
        } catch { /* kept in memory only */ }
    }
    return keep({ key, kind: 'pdb', name: id, label: title(text, 'mmcif'), format: 'mmcif', data: text });
}

async function molecule(input) {
    const key = `mol:${hash(input)}`;
    if (memory.has(key)) return keep(memory.get(key));
    const answer = await fetchText(`${MOLTERM_URL}/api/v1/conformer.sdf`, { method: 'POST', body: new URLSearchParams({ input }) });
    if (answer.status === 400) throw new StructureError('bad_input', answer.text.trim());
    if (answer.status !== 200) throw new StructureError('unavailable', `molterm answered ${answer.status}.`);
    const name = (answer.text.split('\n')[0] || '').trim();
    return keep({ key, kind: 'molecule', name: name.length > 60 ? name.slice(0, 57) + '...' : name, label: '', format: 'sdf',
        data: answer.text, input });
}

function file(text, format) {
    if (text.length > MAX_BYTES) throw new StructureError('too_large', `The file is ${text.length.toLocaleString()} bytes; the limit is ${MAX_BYTES.toLocaleString()}.`);
    format = format || detect(text);
    if (!format) throw new StructureError('format', "That isn't a structure file this reads: PDB, mmCIF, SDF or MOL, MOL2 or XYZ.");
    if (format === 'mol') format = 'sdf';
    const key = `file:${hash(text)}`;
    const label = title(text, format);
    return keep({ key, kind: 'file', name: label || format.toUpperCase(), label, format, data: text });
}

/** A structure for an input: { key, kind, name, label, format, data }. `data`, when given, is a file's contents. */
async function resolve({ input = '', data = '', format = '' } = {}) {
    if (data) return file(data, format);
    input = input.trim();
    if (!input) throw new StructureError('empty', 'Give a PDB id, a SMILES or a structure file.');
    if (PDB_ID.test(input)) return pdbEntry(input);
    const detected = input.includes('\n') ? detect(input) : null;
    if (detected && detected !== 'sdf') return file(input, detected);
    return molecule(input);
}

/** A structure seen before, by its key, while it's still held: what a state names after the first step. */
function known(key) {
    if (memory.has(key)) return keep(memory.get(key));
    if (key.startsWith('pdb:')) return pdbEntry(key.slice(4));
    return null;
}

module.exports = { resolve, known, detect, title, StructureError, PDB_ID };
