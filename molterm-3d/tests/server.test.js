'use strict';
// The web service: health, the client, structures and pictures. SMILES go through the molterm server at $MOLTERM_URL.
const test = require('node:test');
const assert = require('node:assert');
const { server, warmUp } = require('../src/server');

let base;
test.before(async () => {
    await new Promise((resolve) => server.listen(0, resolve));
    base = `http://localhost:${server.address().port}`;
    await warmUp();
});
test.after(() => server.close());

test('health says whether Mol* can draw', async () => {
    const answer = await fetch(`${base}/health`);
    const body = await answer.json();
    assert.strictEqual(answer.status, 200, JSON.stringify(body));
    assert.strictEqual(body.status, 'ok');
    assert.strictEqual(body.service, 'molterm-3d');
});

test('the client is served with this server as its default', async () => {
    const text = await (await fetch(`${base}/molterm3d`)).text();
    assert.ok(text.startsWith('#!/bin/sh') && !text.includes('@@URL@@'));
});

test('the page and Mol*\'s viewer are served from here', async () => {
    const page = await (await fetch(`${base}/`)).text();
    assert.ok(page.includes('/vendor/molstar/molstar.js') && !page.includes('{{') && !/https?:\/\/cdn/.test(page));
    assert.strictEqual((await fetch(`${base}/vendor/molstar/molstar.js`)).status, 200);
    assert.strictEqual((await fetch(`${base}/vendor/house.css`)).status, 200);
    assert.strictEqual((await fetch(`${base}/vendor/molstar/../../package.json`)).status, 404);
});

test('a SMILES comes back in 3D, from molterm', async () => {
    const answer = await fetch(`${base}/api/v1/structure?input=CCO`);
    assert.strictEqual(answer.status, 200, await answer.clone().text());
    assert.strictEqual(answer.headers.get('x-molterm-format'), 'sdf');
    assert.match(await answer.text(), / 9  8  0/);
    const bad = await fetch(`${base}/api/v1/structure?input=C1CC`);
    assert.strictEqual(bad.status, 400);
});

test('a picture comes as a PNG of the size asked for', async () => {
    const answer = await fetch(`${base}/api/v1/render.png?input=CCO&width=300&height=200&turn=30`);
    assert.strictEqual(answer.headers.get('content-type'), 'image/png');
    const png = Buffer.from(await answer.arrayBuffer());
    assert.deepStrictEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [300, 200]);
});

test('the viewer answers in the editor\'s shape', async () => {
    const answer = await fetch(`${base}/api/v1/view`, { method: 'POST', body: new URLSearchParams({ input: 'CCO', cols: 80, rows: 24 }) });
    const text = await answer.text();
    assert.match(text, /^0 [A-Za-z0-9_-]+\n\x1b\[2J/);
    const failed = await fetch(`${base}/api/v1/view`, { method: 'POST', body: new URLSearchParams({ input: 'C1CC', cols: 80, rows: 24 }) });
    assert.match(await failed.text(), /^2 \n.+SMILES/);
});
