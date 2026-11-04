"""Kitty graphics through tmux: pictures shown with Unicode placeholders, which tmux keeps as text.

tmux passes Kitty's graphics commands on only when wrapped for passthrough (and `allow-passthrough` is on), and even then it doesn't
know a picture is there: it would land wherever the outer terminal's cursor happened to be, and stay when the pane changes. So a picture
is sent once as a virtual placement (U=1), and the cells it should cover are filled with U+10EEEE, each with combining marks giving its
row and column in the picture, in a colour that names the picture. tmux keeps those as ordinary text: it moves, redraws, scrolls and
hides them with the pane, and the terminal draws the picture in them. Kitty and Ghostty both support this.
"""
import base64
import contextlib
import contextvars

PLACEHOLDER = "\U0010EEEE"
CHUNK = 4096
# Kitty's gen/rowcolumn-diacritics.txt: the n-th mark stands for row or column n
_DIACRITICS = [
    0x0305, 0x030D, 0x030E, 0x0310, 0x0312, 0x033D, 0x033E, 0x033F, 0x0346, 0x034A, 0x034B, 0x034C, 0x0350, 0x0351, 0x0352, 0x0357,
    0x035B, 0x0363, 0x0364, 0x0365, 0x0366, 0x0367, 0x0368, 0x0369, 0x036A, 0x036B, 0x036C, 0x036D, 0x036E, 0x036F, 0x0483, 0x0484,
    0x0485, 0x0486, 0x0487, 0x0592, 0x0593, 0x0594, 0x0595, 0x0597, 0x0598, 0x0599, 0x059C, 0x059D, 0x059E, 0x059F, 0x05A0, 0x05A1,
    0x05A8, 0x05A9, 0x05AB, 0x05AC, 0x05AF, 0x05C4, 0x0610, 0x0611, 0x0612, 0x0613, 0x0614, 0x0615, 0x0616, 0x0617, 0x0657, 0x0658,
    0x0659, 0x065A, 0x065B, 0x065D, 0x065E, 0x06D6, 0x06D7, 0x06D8, 0x06D9, 0x06DA, 0x06DB, 0x06DC, 0x06DF, 0x06E0, 0x06E1, 0x06E2,
    0x06E4, 0x06E7, 0x06E8, 0x06EB, 0x06EC, 0x0730, 0x0732, 0x0733, 0x0735, 0x0736, 0x073A, 0x073D, 0x073F, 0x0740, 0x0741, 0x0743,
    0x0745, 0x0747, 0x0749, 0x074A, 0x07EB, 0x07EC, 0x07ED, 0x07EE, 0x07EF, 0x07F0, 0x07F1, 0x07F3, 0x0816, 0x0817, 0x0818, 0x0819,
    0x081B, 0x081C, 0x081D, 0x081E, 0x081F, 0x0820, 0x0821, 0x0822, 0x0823, 0x0825, 0x0826, 0x0827, 0x0829, 0x082A, 0x082B, 0x082C,
    0x082D, 0x0951, 0x0953, 0x0954, 0x0F82, 0x0F83, 0x0F86, 0x0F87, 0x135D, 0x135E, 0x135F, 0x17DD, 0x193A, 0x1A17, 0x1A75, 0x1A76,
    0x1A77, 0x1A78, 0x1A79, 0x1A7A, 0x1A7B, 0x1A7C, 0x1B6B, 0x1B6D, 0x1B6E, 0x1B6F, 0x1B70, 0x1B71, 0x1B72, 0x1B73, 0x1CD0, 0x1CD1,
    0x1CD2, 0x1CDA, 0x1CDB, 0x1CE0, 0x1DC0, 0x1DC1, 0x1DC3, 0x1DC4, 0x1DC5, 0x1DC6, 0x1DC7, 0x1DC8, 0x1DC9, 0x1DCB, 0x1DCC, 0x1DD1,
    0x1DD2, 0x1DD3, 0x1DD4, 0x1DD5, 0x1DD6, 0x1DD7, 0x1DD8, 0x1DD9, 0x1DDA, 0x1DDB, 0x1DDC, 0x1DDD, 0x1DDE, 0x1DDF, 0x1DE0, 0x1DE1,
    0x1DE2, 0x1DE3, 0x1DE4, 0x1DE5, 0x1DE6, 0x1DFE, 0x20D0, 0x20D1, 0x20D4, 0x20D5, 0x20D6, 0x20D7, 0x20DB, 0x20DC, 0x20E1, 0x20E7,
    0x20E9, 0x20F0, 0x2CEF, 0x2CF0, 0x2CF1, 0x2DE0, 0x2DE1, 0x2DE2, 0x2DE3, 0x2DE4, 0x2DE5, 0x2DE6, 0x2DE7, 0x2DE8, 0x2DE9, 0x2DEA,
    0x2DEB, 0x2DEC, 0x2DED, 0x2DEE, 0x2DEF, 0x2DF0, 0x2DF1, 0x2DF2, 0x2DF3, 0x2DF4, 0x2DF5, 0x2DF6, 0x2DF7, 0x2DF8, 0x2DF9, 0x2DFA,
    0x2DFB, 0x2DFC, 0x2DFD, 0x2DFE, 0x2DFF, 0xA66F, 0xA67C, 0xA67D, 0xA6F0, 0xA6F1, 0xA8E0, 0xA8E1, 0xA8E2, 0xA8E3, 0xA8E4, 0xA8E5,
    0xA8E6, 0xA8E7, 0xA8E8, 0xA8E9, 0xA8EA, 0xA8EB, 0xA8EC, 0xA8ED, 0xA8EE, 0xA8EF, 0xA8F0, 0xA8F1, 0xAAB0, 0xAAB2, 0xAAB3, 0xAAB7,
    0xAAB8, 0xAABE, 0xAABF, 0xAAC1, 0xFE20, 0xFE21, 0xFE22, 0xFE23, 0xFE24, 0xFE25, 0xFE26, 0x10A0F, 0x10A38, 0x1D185, 0x1D186, 0x1D187,
    0x1D188, 0x1D189, 0x1D1AA, 0x1D1AB, 0x1D1AC, 0x1D1AD, 0x1D242, 0x1D243, 0x1D244,
]
DIACRITICS = [chr(code) for code in _DIACRITICS]
MAX_CELLS = len(DIACRITICS)


def wire_id(number):
    """The id a picture goes by in the terminal, for one of ours numbered from 1 to MAX_NUMBER. tmux turns 24-bit colours into 256 when it
    doesn't know the outer terminal has them (so with Ghostty's terminfo), and the colour is how a placeholder names its picture. So the
    id's lowest byte, never 0, is a 256-colour index, which tmux passes on as it is, and its highest byte is a third combining mark on
    each cell, as Kitty's protocol allows: id = high << 24 | low."""
    return (number // 255) << 24 | (number % 255 + 1)


MAX_NUMBER = 255 * 297 - 1


def wrap(sequence):
    """One escape sequence, for tmux to pass to the terminal as it is."""
    return "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"


def transmit(png, image_id, cols, rows):
    """Send a PNG as a virtual placement of cols by rows cells, to be shown wherever its placeholders are printed. Ids here, as in
    delete, cells and lines, are our numbers, which wire_id turns into the terminal's.

    Whatever had the id before is deleted first. Ghostty, sent a picture under an id it has, replaces the picture but keeps that id's
    placements, and adds the new one beside them; its placeholders then take their size in cells from whichever it finds first. An
    old one, from an earlier frame or run with another size, would squeeze the picture into the wrong number of cells, off the ones
    that show it, and away from where the mouse lands."""
    out = [delete(image_id)]
    image_id = wire_id(image_id)
    payload = base64.b64encode(png).decode("ascii")
    chunks = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)] or [""]
    for index, chunk in enumerate(chunks):
        keys = f"a=T,f=100,U=1,i={image_id},c={cols},r={rows},q=2," if index == 0 else "q=2,"
        out.append(wrap(f"\x1b_G{keys}m={int(index < len(chunks) - 1)};{chunk}\x1b\\"))
    return "".join(out)


def delete(image_id):
    return wrap(f"\x1b_Ga=d,d=I,i={wire_id(image_id)},q=2\x1b\\")


def colour(image_id):
    """The foreground colour that names a picture, in 256 colours: the low byte of its id."""
    return f"\x1b[38;5;{wire_id(image_id) & 255}m"


def _cell(image_id, row, col):
    high = wire_id(image_id) >> 24
    return PLACEHOLDER + DIACRITICS[row] + DIACRITICS[col] + DIACRITICS[high]


def cells(image_id, cols, rows, at, hidden=None):
    """The placeholders for a picture of cols by rows cells. `at(row, col)` moves the cursor to a cell of the picture, counted from its
    top left; cells for which `hidden(row, col)` is true are left alone, so that what covers them stays."""
    cols, rows = min(cols, MAX_CELLS), min(rows, MAX_CELLS)
    out = []
    for row in range(rows):
        col = 0
        while col < cols:
            if hidden and hidden(row, col):
                col += 1
                continue
            start = col
            while col < cols and not (hidden and hidden(row, col)):
                col += 1
            run = "".join(_cell(image_id, row, c) for c in range(start, col))
            out.append(at(row, start) + colour(image_id) + run + "\x1b[39m")
    return "".join(out)


def lines(png, image_id, cols, rows):
    """A picture as lines of text, for printing into a shell: it scrolls with the text, in tmux's history too."""
    rows_text = [colour(image_id) + "".join(_cell(image_id, row, c) for c in range(min(cols, MAX_CELLS))) + "\x1b[39m"
                 for row in range(min(rows, MAX_CELLS))]
    return transmit(png, image_id, cols, rows) + "\n".join(rows_text) + "\n"


# Whether this request's pictures go through tmux, for the code that draws them to ask without its being passed down everywhere
active = contextvars.ContextVar("placeholders", default=False)


@contextlib.contextmanager
def using(on):
    token = active.set(bool(on))
    try:
        yield
    finally:
        active.reset(token)


def delete_sequence(image_id):
    """Remove a picture, through tmux if this request's pictures go through it."""
    return delete(image_id) if active.get() else f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"
