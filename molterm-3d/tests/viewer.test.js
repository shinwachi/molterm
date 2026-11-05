'use strict';
// The terminal viewer's steps: input, turning, and frames, drawn by Mol* for real (run under xvfb-run, as `make test` does).
const test = require('node:test');
const assert = require('node:assert');
const { Quat, Vec3 } = require('molstar/lib/commonjs/mol-math/linear-algebra');
const kitty = require('../src/kitty');
const viewer = require('../src/viewer');

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
    assert.strictEqual(still.output, '', 'nothing changed, nothing printed');

    answer = await viewer.step({ ...size, state: answer.state, events: hex('ss'), placeholders: '1' }); // auto, ball-and-stick, spacefill
    assert.ok(answer.output.includes('spacefill'));
    assert.ok(answer.output.includes('\x1bPtmux;') && answer.output.includes(kitty.PLACEHOLDER), 'through tmux, placeholders');
    assert.ok(!answer.output.replace(/\x1bPtmux;[\s\S]*?\x1b\\(?!\\)/g, '').includes('\x1b_G'), 'every picture command wrapped');

    assert.strictEqual((await viewer.step({ ...size, state: answer.state, events: hex('q') })).status, 1);
});

test('a window of another size is drawn again to fit it', async () => {
    const first = await viewer.step({ ...size, data: ETHANOL });
    const resized = await viewer.step({ ...size, cols: '120', rows: '40', state: first.state });
    assert.ok(resized.output.includes('c=120,r=39,'));
});
