'use strict';
// The terminal viewer's steps: input, turning, and frames, drawn by Mol* for real (run under xvfb-run, as `make test` does).
const test = require('node:test');
const assert = require('node:assert');
const { Quat, Vec3 } = require('molstar/lib/commonjs/mol-math/linear-algebra');
const kitty = require('../src/kitty');
const viewer = require('../src/viewer');
const renderer = require('../src/renderer');

// Ethanol in 3D, as molterm's /api/v1/conformer.sdf gives it, so these need no other service
const ETHANOL = `CCO
     RDKit          3D

  9  8  0  0  0  0  0  0  0  0999 V2000
   -0.9233    0.0285    0.0435 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.4855   -0.4864   -0.1863 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.3378    0.5875    0.1355 O   0  0  0  0  0  0  0  0  0  0  0  0
   -1.0599    0.9536   -0.5214 H   0  0  0  0  0  0  0  0  0  0  0  0
   -1.6460   -0.7154   -0.3027 H   0  0  0  0  0  0  0  0  0  0  0  0
   -1.0884    0.2364    1.1048 H   0  0  0  0  0  0  0  0  0  0  0  0
    0.6527   -1.3592    0.4513 H   0  0  0  0  0  0  0  0  0  0  0  0
    0.6211   -0.7633   -1.2391 H   0  0  0  0  0  0  0  0  0  0  0  0
    1.1979    1.3366   -0.4859 H   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
  1  4  1  0
  1  5  1  0
  1  6  1  0
  2  7  1  0
  2  8  1  0
  3  9  1  0
M  END
$$$$
`;
const hex = (text) => Buffer.from(text, 'latin1').toString('hex');
const size = { cols: '80', rows: '24', cell_width: '10', cell_height: '20' };
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47]).toString('base64').slice(0, 4); // how every picture's data starts: iVBO

test('keys and SGR mouse reports are read, and other reports skipped', () => {
    assert.deepStrictEqual(viewer.events('q'), [{ key: 'q' }]);
    assert.deepStrictEqual(viewer.events('\x1b[A\x1b[1;2C\x1bOD'), [{ key: 'up', shift: false }, { key: 'right', shift: true },
        { key: 'left', shift: false }]);
    const [press, drag, up, wheel] = viewer.events('\x1b[<0;10;5M\x1b[<32;12;5M\x1b[<0;12;5m\x1b[<65;3;3M').map((e) => e.mouse);
    assert.ok(press.press && !press.motion && press.button === 0 && press.x === 10 && press.y === 5);
    assert.ok(drag.motion && drag.x === 12);
    assert.ok(!up.press);
    assert.strictEqual(wheel.wheel, 'down');
    assert.deepStrictEqual(viewer.events('\x1b[I\x1b[?1016;1$y\x1b'), [{ key: 'escape' }]);
});

test('a drag across half the picture turns a quarter, about the vertical', () => {
    const state = { q: [0, 0, 0, 1], z: 1, p: [0, 0], d: null };
    const home = { up: Vec3.create(0, 1, 0), direction: Vec3.create(0, 0, 1) };
    viewer.apply(state, home, viewer.events('\x1b[<0;20;10M\x1b[<32;60;10M\x1b[<0;60;10m'), [80, 23]);
    const angle = Quat.getAxisAngle(Vec3(), Quat.create(...state.q));
    assert.ok(Math.abs(angle - Math.PI / 2) < 1e-6, angle);
    assert.strictEqual(state.d, null);
    // moving with no button down does nothing
    const before = [...state.q];
    viewer.apply(state, home, viewer.events('\x1b[<35;30;10M\x1b[<35;50;10M'), [80, 23]);
    assert.deepStrictEqual(state.q, before);
});

test('the near side follows the drag, as the arrows turn it', () => {
    const home = { up: Vec3.create(0, 1, 0), direction: Vec3.create(0, 0, 1) };
    // where the camera goes: to see the near side move right or down, it goes left or up
    const camera = (input) => {
        const state = { q: [0, 0, 0, 1], z: 1, p: [0, 0], d: null };
        viewer.apply(state, home, viewer.events(input), [80, 23]);
        return Vec3.transformQuat(Vec3(), home.direction, Quat.create(...state.q));
    };
    assert.ok(camera('\x1b[<0;20;10M\x1b[<32;30;10M\x1b[<0;30;10m')[0] < -0.1, 'drag right');
    assert.ok(camera('\x1b[<0;20;10M\x1b[<32;20;14M\x1b[<0;20;14m')[1] > 0.1, 'drag down');
    assert.ok(camera('\x1b[C')[0] < -0.1, 'right arrow');
    assert.ok(camera('\x1b[B')[1] > 0.1, 'down arrow');
});

test('the wheel zooms within bounds, r resets, s and c step, q quits', () => {
    const state = { q: [0, 0, 0, 1], z: 1, p: [0, 0], d: null, s: 'auto', c: 'auto', h: true };
    const home = { up: Vec3.create(0, 1, 0), direction: Vec3.create(0, 0, 1) };
    viewer.apply(state, home, viewer.events('\x1b[<64;1;1M'.repeat(100)), [80, 23]);
    assert.strictEqual(state.z, 25);
    viewer.apply(state, home, viewer.events('rsSsch'), [80, 23]);
    assert.deepStrictEqual([state.z, state.s, state.c, state.h], [1, 'ball-and-stick', 'element', false]);
    assert.strictEqual(viewer.apply(state, home, viewer.events('\x1b[Aq'), [80, 23]), 'quit');
});

test('a damaged state is refused', async () => {
    await assert.rejects(viewer.step({ ...size, state: 'not-a-state' }), viewer.StateError);
});

test('a file opens, draws, turns, changes style and quits', async () => {
    let answer = await viewer.step({ ...size, data: ETHANOL });
    assert.strictEqual(answer.status, 0);
    assert.ok(answer.output.startsWith('\x1b[2J'));
    assert.ok(answer.output.includes(`a=T,f=100,i=${viewer.IDS[1]},c=80,r=23,C=1,q=2,m=`) && answer.output.includes(PNG));
    assert.ok(answer.output.includes('9 atoms') && answer.output.includes('ball-and-stick'));

    answer = await viewer.step({ ...size, state: answer.state, events: hex('\x1b[<0;30;10M\x1b[<32;40;10M') });
    assert.ok(answer.output.includes(`i=${viewer.IDS[0]},c=80`), 'the next picture takes the other id');
    assert.ok(answer.output.indexOf(`a=T,f=100,i=${viewer.IDS[0]}`) < answer.output.indexOf(`a=d,d=I,i=${viewer.IDS[1]},`),
        'the old picture goes after the new one is up');

    const still = await viewer.step({ ...size, state: answer.state, events: hex('\x1b[I') });
    assert.ok(!still.output.includes('\x1b_G'), 'nothing changed, no picture');
    assert.ok(still.output.startsWith('\x1b[24;1H') && still.output.includes('9 atoms'), 'the status line again, over the spinner');

    answer = await viewer.step({ ...size, state: answer.state, events: hex('ss'), placeholders: '1' }); // auto, ball-and-stick, spacefill
    assert.ok(answer.output.includes('spacefill'));
    assert.ok(answer.output.includes('\x1bPtmux;') && answer.output.includes(kitty.PLACEHOLDER), 'through tmux, placeholders');
    assert.ok(!answer.output.replace(/\x1bPtmux;[\s\S]*?\x1b\\(?!\\)/g, '').includes('\x1b_G'), 'every picture command wrapped');

    assert.strictEqual((await viewer.step({ ...size, state: answer.state, events: hex('q') })).status, 1);
});

/** The PNG a step's output sends, from its Kitty payloads. */
function picture(output) {
    return Buffer.from([...output.matchAll(/\x1b_G[^;\x1b]*;([A-Za-z0-9+/=]*)\x1b\\/g)].map((m) => m[1]).join(''), 'base64');
}

test('a slow structure is drawn rough while a drag goes on, and in full when the button is up', async () => {
    const above = renderer.ROUGH.above;
    renderer.ROUGH.above = -1; // every structure counts as slow
    try {
        const width = (answer) => picture(answer.output).readUInt32BE(16); // from the PNG's header
        const first = await viewer.step({ ...size, data: ETHANOL });
        assert.strictEqual(width(first), 800, 'the first picture in full');
        const dragging = await viewer.step({ ...size, state: first.state, events: hex('\x1b[<0;30;10M\x1b[<32;40;10M') });
        assert.strictEqual(width(dragging), 400, 'rough while the button is down');
        const up = await viewer.step({ ...size, state: dragging.state, events: hex('\x1b[<0;40;10m') });
        assert.strictEqual(width(up), 800, 'in full once it is up, though the view did not change');
        const key = await viewer.step({ ...size, state: up.state, events: hex('+') });
        assert.strictEqual(width(key), 800, 'keys are drawn in full');
    } finally {
        renderer.ROUGH.above = above;
    }
    // a quick structure is never drawn rough
    const first = await viewer.step({ ...size, data: ETHANOL, style: 'licorice' });
    const dragging = await viewer.step({ ...size, state: first.state, events: hex('\x1b[<0;30;10M\x1b[<32;40;10M') });
    assert.strictEqual(picture(dragging.output).readUInt32BE(16), 800);
});

test('l steps through the lightings, from illustrative, and the status line says which', async () => {
    const wide = { ...size, cols: '140' }; // room on the status line for everything
    const first = await viewer.step({ ...wide, data: ETHANOL });
    assert.ok(first.output.includes(' · illustrative · '), 'illustrative is the default');
    const next = await viewer.step({ ...wide, state: first.state, events: hex('l') });
    assert.ok(next.output.includes(' · standard · '));
    const back = await viewer.step({ ...wide, state: next.state, events: hex('L') });
    assert.ok(back.output.includes(' · illustrative · '));
    // a state from before there was a choice was drawn in Mol*'s standard light, and stays so
    const old = viewer.decode(first.state);
    delete old.l;
    const kept = await viewer.step({ ...wide, state: viewer.encode(old), cols: '150' });
    assert.ok(kept.output.includes(' · standard · '));
});

test('a printed picture goes at the cursor, which goes below it, so that it scrolls with the text', async () => {
    const text = await viewer.print({ data: ETHANOL, rows: '10', cols: '200', cell_width: '10', cell_height: '20', lighting: 'outline' });
    // 10 rows of 20 pixels, 4:3, in 10-pixel columns
    assert.ok(text.startsWith('\x1b_Ga=T,f=100,c=27,r=10,q=2,m='), text.slice(0, 40));
    assert.ok(!text.includes('C=1') && !text.includes(',i=') && text.endsWith('\x1b\\\r\n'));
    const narrow = await viewer.print({ data: ETHANOL, rows: '10', cols: '12' });
    assert.ok(narrow.includes('c=12,r=10,'), 'never wider than the terminal');
    const through = await viewer.print({ data: ETHANOL, rows: '3', cols: '80', placeholders: '1' });
    assert.ok(through.startsWith('\x1bPtmux;') && through.endsWith('\x1b[39m\r\n'));
    assert.strictEqual(through.split(kitty.PLACEHOLDER).length - 1, 3 * Math.round(3 * 20 * 4 / 3 / 10), 'a cell for each of rows by cols');
    assert.strictEqual(through.split('\r\n').length, 4, 'one line for each row');
});

test('a window of another size is drawn again to fit it', async () => {
    const first = await viewer.step({ ...size, data: ETHANOL });
    const resized = await viewer.step({ ...size, cols: '120', rows: '40', state: first.state });
    assert.ok(resized.output.includes('c=120,r=39,'));
});
