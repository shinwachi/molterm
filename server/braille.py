"""Map a boolean bitmap to Unicode Braille patterns (U+2800-U+28FF), two dots wide and four dots tall per character."""
import numpy as np

CELL_WIDTH = 2
CELL_HEIGHT = 4
BLANK = 0x2800
BLANK_CHAR = chr(BLANK)

# Unicode numbers the dots down the left column, then the right, with the bottom row added last:
#   1 4      0x01 0x08
#   2 5  ->  0x02 0x10
#   3 6      0x04 0x20
#   7 8      0x40 0x80
DOT_BITS = np.array([[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]])


def cell_masks(bitmap, dot_bits):
    """Combine each 2x4 block of dots into one integer per cell, padding a partial last cell with empty dots."""
    bitmap = np.asarray(bitmap, dtype=bool)
    height, width = bitmap.shape
    rows, cols = -(-height // CELL_HEIGHT), -(-width // CELL_WIDTH)
    padded = np.zeros((rows * CELL_HEIGHT, cols * CELL_WIDTH), dtype=bool)
    padded[:height, :width] = bitmap
    cells = padded.reshape(rows, CELL_HEIGHT, cols, CELL_WIDTH).transpose(0, 2, 1, 3)
    return (cells * dot_bits).sum(axis=(2, 3))


def bitmap_to_grid(bitmap):
    """Return a 2D list of characters."""
    return [[chr(mask + BLANK) for mask in row] for row in cell_masks(bitmap, DOT_BITS).tolist()]
