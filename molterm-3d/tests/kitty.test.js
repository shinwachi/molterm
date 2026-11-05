'use strict';
// Kitty graphics, as molterm's server/placeholders.py sends them: the same ids, marks and passthrough.
const test = require('node:test');
const assert = require('node:assert');
const kitty = require('../src/kitty');

test('the table of marks is Kitty\'s', () => {
    assert.strictEqual(kitty.DIACRITICS.length, 297);
    assert.strictEqual(kitty.DIACRITICS[0], '̅');
    assert.strictEqual(kitty.DIACRITICS[2], '̎');
});

test('ids are molterm\'s: the low byte a 256-colour index, never 0, the high byte a mark', () => {
    assert.strictEqual(kitty.wireId(7001), 452984949); // placeholders.wire_id(7001)
    assert.strictEqual(kitty.wireId(3301) & 255, 3301 % 255 + 1);
    assert.strictEqual(kitty.colour(3301), `\x1b[38;5;${3301 % 255 + 1}m`);
});

test('passthrough doubles every escape', () => {
    assert.strictEqual(kitty.wrap('\x1b_Ga=d\x1b\\'), '\x1bPtmux;\x1b\x1b_Ga=d\x1b\x1b\\\x1b\\');
});

test('a picture is sent in pieces, the cursor left where it was', () => {
    const sent = kitty.place(Buffer.alloc(10000, 120), 3301, 20, 6);
    const parts = sent.split('\x1b\\').filter(Boolean);
    assert.ok(parts[0].startsWith('\x1b_Ga=T,f=100,i=3301,c=20,r=6,C=1,q=2,m=1;'));
    assert.ok(parts.at(-1).startsWith('\x1b_Gq=2,m=0;'));
    assert.strictEqual(parts.length, 4);
});

test('through tmux, the id is cleared before the picture comes again, and the cells name it', () => {
    const sent = kitty.transmit(Buffer.from('x'), 3301, 3, 2);
    assert.ok(sent.startsWith(kitty.wrap(`\x1b_Ga=d,d=I,i=${kitty.wireId(3301)},q=2\x1b\\`)));
    assert.ok(sent.includes(`a=T,f=100,U=1,i=${kitty.wireId(3301)},c=3,r=2,q=2,m=0;`));
    const cells = kitty.cells(3301, 3, 2, (row) => `<${row}>`);
    const high = kitty.DIACRITICS[kitty.wireId(3301) >>> 24];
    assert.ok(cells.startsWith('<0>' + kitty.colour(3301) + kitty.PLACEHOLDER + kitty.DIACRITICS[0] + kitty.DIACRITICS[0] + high));
    assert.strictEqual([...cells.matchAll(new RegExp(kitty.PLACEHOLDER, 'gu'))].length, 6);
    assert.ok(cells.includes('<1>'));
});
