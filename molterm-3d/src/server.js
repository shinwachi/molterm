'use strict';
// molterm-3d's web service: the terminal viewer's steps, structures for the web page's Mol* viewer, pictures, and the client script.

const fs = require('fs');
const http = require('http');
const path = require('path');
const renderer = require('./renderer');
const structures = require('./structures');
const viewer = require('./viewer');

const ROOT = path.join(__dirname, '..');
const PORT = +process.env.PORT || 8930;
const PUBLIC_URL = (process.env.PUBLIC_URL || `http://localhost:${PORT}`).replace(/\/$/, '');
const MOLTERM_URL = (process.env.MOLTERM_URL || 'http://molterm.localhost').replace(/\/$/, '');
const MAX_BODY = 25 * 1024 * 1024;
const VERSION = require('../package.json').version;
const MOLSTAR = require('molstar/package.json').version;

// Files served as they are: the page's own, the house stylesheet, and Mol*'s viewer from its package
const FILES = {
    '/static/': path.join(ROOT, 'public'),
    '/vendor/molstar/': path.join(ROOT, 'node_modules', 'molstar', 'build', 'viewer'),
    '/vendor/': path.join(ROOT, 'vendor'),
};
const TYPES = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png', '.ico': 'image/x-icon',
    '.svg': 'image/svg+xml', '.json': 'application/json', '.map': 'application/json' };

// The health check draws a small molecule once at start, so a container whose OpenGL doesn't work is reported as such
let health = { status: 'starting' };
const WARMUP = `water
     RDKit          3D

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.1173 O   0  0
    0.0000    0.7572   -0.4692 H   0  0
    0.0000   -0.7572   -0.4692 H   0  0
  1  2  1  0
  1  3  1  0
M  END
$$$$
`;

async function warmUp() {
    try {
        const structure = await structures.resolve({ data: WARMUP, format: 'sdf' });
        const { png } = await renderer.draw(structure, { style: 'auto', colour: 'auto', hydrogens: true }, { q: [0, 0, 0, 1], z: 1, p: [0, 0] }, 64, 64);
        health = png.length > 100 ? { status: 'ok' } : { status: 'error', message: 'Mol* drew nothing.' };
    } catch (e) {
        health = { status: 'error', message: `Mol* can't draw here: ${e.message}` };
    }
}

function send(res, status, body, headers = {}) {
    res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8', ...headers });
    res.end(body);
}

function json(res, status, value) {
    send(res, status, JSON.stringify(value), { 'Content-Type': 'application/json' });
}

function readBody(req) {
    return new Promise((resolve, reject) => {
        const parts = [];
        let size = 0;
        req.on('data', (chunk) => {
            size += chunk.length;
            if (size > MAX_BODY) {
                reject(Object.assign(new Error('The request is too large.'), { status: 413 }));
                req.destroy();
                return;
            }
            parts.push(chunk);
        });
        req.on('end', () => resolve(Buffer.concat(parts).toString('utf8')));
        req.on('error', reject);
    });
}

async function form(req, url) {
    const values = Object.fromEntries(url.searchParams);
    if (req.method === 'POST') Object.assign(values, Object.fromEntries(new URLSearchParams(await readBody(req))));
    return values;
}

function serveFile(res, prefix, rest) {
    const base = FILES[prefix];
    const file = path.normalize(path.join(base, rest));
    if (!file.startsWith(base + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, 'Not found.\n');
    send(res, 200, fs.readFileSync(file), { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream',
        'Cache-Control': 'max-age=3600' });
}

function page(name) {
    return fs.readFileSync(path.join(ROOT, 'public', name), 'utf8')
        .replaceAll('{{PUBLIC_URL}}', PUBLIC_URL).replaceAll('{{MOLTERM_URL}}', MOLTERM_URL)
        .replaceAll('{{STYLES}}', renderer.STYLES.map((s) => `<option>${s}</option>`).join(''))
        .replaceAll('{{COLOURS}}', renderer.COLOURS.map((c) => `<option>${c}</option>`).join(''));
}

function failure(res, e) {
    if (e instanceof structures.StructureError) return send(res, e.code === 'unavailable' ? 502 : 400, e.message + '\n');
    if (e instanceof viewer.StateError) return send(res, 400, e.message + '\n');
    if (e.status) return send(res, e.status, e.message + '\n');
    console.error(e);
    return send(res, 500, 'Something went wrong drawing that.\n');
}

const routes = {
    'GET /': (req, res) => send(res, 200, page('index.html'), { 'Content-Type': 'text/html; charset=utf-8' }),

    'GET /health': (req, res) => json(res, health.status === 'ok' ? 200 : 503,
        { service: 'molterm-3d', version: VERSION, molstar: MOLSTAR, env: process.env.APP_ENV || 'dev', ...health }),

    // The client, with this server as its default
    'GET /molterm3d': (req, res) => send(res, 200,
        fs.readFileSync(path.join(ROOT, 'client', 'molterm3d.sh'), 'utf8').replace('@@URL@@', PUBLIC_URL),
        { 'Content-Type': 'text/x-shellscript; charset=utf-8' }),

    // A structure's file, for the web page's Mol* viewer: ?input=<PDB id, SMILES or molblock>
    'GET /api/v1/structure': async (req, res, url) => {
        const structure = await structures.resolve(await form(req, url));
        send(res, 200, structure.data, { 'X-Molterm-Format': structure.format, 'X-Molterm-Name': encodeURIComponent(structure.name),
            'Cache-Control': 'max-age=3600' });
    },
    'POST /api/v1/structure': (...args) => routes['GET /api/v1/structure'](...args),

    // A picture: ?input=&style=&colour=&width=&height=&background=RRGGBB&hydrogens=0&turn=<degrees about the vertical>&tilt=<degrees>
    'GET /api/v1/render.png': async (req, res, url) => {
        const values = await form(req, url);
        const structure = await structures.resolve(values);
        const width = Math.min(Math.max(parseInt(values.width, 10) || 600, 16), 2000);
        const height = Math.min(Math.max(parseInt(values.height, 10) || 450, 16), 2000);
        const radians = (name) => (parseFloat(values[name]) || 0) * Math.PI / 180;
        const look = { style: renderer.STYLES.includes(values.style) ? values.style : 'auto',
            colour: renderer.COLOURS.includes(values.colour) ? values.colour : 'auto', hydrogens: values.hydrogens !== '0' };
        const background = /^[0-9a-fA-F]{6}$/.test(values.background || '') ? parseInt(values.background, 16) : undefined;
        const { png } = await renderer.draw(structure, look, { turn: radians('turn'), tilt: radians('tilt'), z: 1, p: [0, 0] }, width, height, background);
        send(res, 200, png, { 'Content-Type': 'image/png', 'Cache-Control': 'max-age=3600' });
    },

    // One step of the terminal viewer. First line: "<0 go on, 1 finished, 2 failed> <state>"; then what to print.
    'POST /api/v1/view': async (req, res, url) => {
        let answer;
        try {
            answer = await viewer.step(await form(req, url));
        } catch (e) {
            if (!(e instanceof structures.StructureError || e instanceof viewer.StateError)) throw e;
            answer = { status: 2, state: '', output: e.message };
        }
        send(res, 200, `${answer.status} ${answer.state}\n${answer.output}`);
    },
};

const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    try {
        const route = routes[`${req.method} ${url.pathname}`];
        if (route) return await route(req, res, url);
        if (req.method === 'GET') {
            const prefix = Object.keys(FILES).find((p) => url.pathname.startsWith(p));
            if (prefix) return serveFile(res, prefix, decodeURIComponent(url.pathname.slice(prefix.length)));
        }
        send(res, 404, 'Not found.\n');
    } catch (e) {
        failure(res, e);
    }
});

if (require.main === module) {
    server.listen(PORT, () => console.log(`molterm-3d ${VERSION} (Mol* ${MOLSTAR}) on :${PORT}, structures from ${MOLTERM_URL}`));
    warmUp();
}

module.exports = { server, routes, warmUp };
