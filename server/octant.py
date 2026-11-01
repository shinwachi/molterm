"""Map a boolean bitmap to Unicode octants: solid cells, two dots wide and four tall (Unicode 16, 2024).

Terminals need to draw or have a font for U+1CD00-U+1CDE5. Ghostty does.
"""
import numpy as np

import braille

BLANK_CHAR = " "

# Octant names number the dots across each row, unlike Braille:
#   1 2      0x01 0x02
#   3 4  ->  0x04 0x08
#   5 6      0x10 0x20
#   7 8      0x40 0x80
DOT_BITS = np.array([[0x01, 0x02], [0x04, 0x08], [0x10, 0x20], [0x40, 0x80]])

# The 26 patterns Unicode encoded elsewhere; the octant block leaves them out.
ELSEWHERE = {
    0x00: " ",
    0x01: "\U0001CEA8",  # left half upper one quarter
    0x02: "\U0001CEAB",  # right half upper one quarter
    0x03: "\U0001FB82",  # upper one quarter
    0x05: "▘",
    0x0A: "▝",
    0x0F: "▀",
    0x14: "\U0001FBE6",  # middle left one quarter
    0x28: "\U0001FBE7",  # middle right one quarter
    0x3F: "\U0001FB85",  # upper three quarters
    0x40: "\U0001CEA3",  # left half lower one quarter
    0x50: "▖",
    0x55: "▌",
    0x5A: "▞",
    0x5F: "▛",
    0x80: "\U0001CEA0",  # right half lower one quarter
    0xA0: "▗",
    0xA5: "▚",
    0xAA: "▐",
    0xAF: "▜",
    0xC0: "▂",
    0xF0: "▄",
    0xF5: "▙",
    0xFA: "▟",
    0xFC: "▆",
    0xFF: "█",
}


def _build_table():
    """Everything not encoded elsewhere runs through the block in ascending order of its bits."""
    next_code = iter(range(0x1CD00, 0x1CDE6))
    return [ELSEWHERE.get(mask) or chr(next(next_code)) for mask in range(256)]


CHARS = _build_table()


def bitmap_to_grid(bitmap):
    """Return a 2D list of characters. A bitmap that doesn't fill its last cell is padded with empty dots."""
    return [[CHARS[mask] for mask in row] for row in braille.cell_masks(bitmap, DOT_BITS).tolist()]
