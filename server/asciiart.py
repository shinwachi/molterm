"""Draw a molecule's bonds in plain ASCII, as lines of characters, so that it survives anything that can carry text: logs, email, old terminals.

Unlike Braille and octants this doesn't map dots to characters. A character has too few shapes to stand for a block of dots, and double bonds turn
to noise. So each bond is drawn from its atoms' positions: a shallow one as .-' by how high it runs through each cell, a steep one as / or \\.
"""
import math

BLANK_CHAR = " "
BOND_DOTS = (16, 12, 10, 8, 6)  # dots per bond to try, largest first: at 2 dots a column, 16 is a bond of 8 columns
_FLAT, _STEEP = 0.3, 1.5  # a bond's rise over its run on screen, a row being as tall as two columns
_SIDE = 0.9  # cells between the lines of a double bond
_TRIM = 0.14  # of a bond at each end, left for the atoms


def _put(grid, row, col, char):
    if 0 <= row < len(grid) and 0 <= col < len(grid[0]) and grid[row][col] == BLANK_CHAR:
        grid[row][col] = char


def _shallow_char(row):
    """The character for a line passing at this height through a cell, whose middle is at a whole number."""
    offset = row - round(row)
    return "." if offset > 0.17 else "'" if offset < -0.17 else "-"


def _line(grid, start, end):
    (x1, y1), (x2, y2) = start, end
    run, rise = x2 - x1, y2 - y1
    steepness = abs(rise) * 2 / max(abs(run), 1e-6)
    if steepness >= _STEEP:  # one character per row
        char = "|" if steepness > 4 else "/" if run * rise < 0 else "\\"
        low, high = (start, end) if y1 < y2 else (end, start)
        for row in range(math.ceil(low[1] - 0.5), math.floor(high[1] + 0.5) + 1):
            place = (row - y1) / rise if rise else 0
            _put(grid, row, round(x1 + run * place), char)
        return
    for col in range(math.ceil(min(x1, x2) - 0.5), math.floor(max(x1, x2) + 0.5) + 1):
        row = y1 + rise * (col - x1) / run
        _put(grid, round(row), col, "-" if steepness < _FLAT else _shallow_char(row))


def _sides(order, start, end, towards):
    """Offsets, in cells, of a bond's lines. A double bond's second line goes on the side of the rest of the molecule."""
    if order == 1:
        return [(0, 0)]
    run, rise = (end[0] - start[0]), (end[1] - start[1]) * 2  # in columns, so that the offset is square on screen
    length = math.hypot(run, rise) or 1
    normal = -rise / length * _SIDE * 2, run / length * _SIDE
    middle = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
    if (towards[0] - middle[0]) * normal[0] + (towards[1] - middle[1]) * normal[1] < 0:
        normal = -normal[0], -normal[1]
    if order == 2:
        return [(0, 0), normal]
    return [(0, 0), normal, (-normal[0], -normal[1])]


def draw(bonds, positions, rows, cols):
    """Return a 2D list of characters. `bonds` holds (first atom, second atom, order) and `positions` each atom's (col, row), as floats."""
    grid = [[BLANK_CHAR] * cols for _ in range(rows)]
    middle = sum(p[0] for p in positions) / len(positions), sum(p[1] for p in positions) / len(positions)
    for first, second, order in bonds:
        a, b = positions[first], positions[second]
        near = [positions[o if f in (first, second) else f] for f, o, _ in bonds if f in (first, second) or o in (first, second)]
        near = [p for p in near if p not in (a, b)]
        towards = (sum(p[0] for p in near) / len(near), sum(p[1] for p in near) / len(near)) if near else middle
        for index, (dx, dy) in enumerate(_sides(order, a, b, towards)):
            trim = _TRIM if index == 0 else _TRIM * 2
            _line(grid, (a[0] + (b[0] - a[0]) * trim + dx, a[1] + (b[1] - a[1]) * trim + dy),
                  (b[0] + (a[0] - b[0]) * trim + dx, b[1] + (a[1] - b[1]) * trim + dy))
    return grid
