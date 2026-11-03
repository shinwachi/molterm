"""The editor's tool palette, drawn as one image so that its buttons can show real bonds and rings rather than words for them."""
import math
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw

_OVERSAMPLE = 4
_COLOURS = {"dark": {"ink": (240, 239, 236, 255), "hover": (58, 58, 55, 255), "active": (57, 135, 229, 255), "on_active": (255, 255, 255, 255)},
            "light": {"ink": (29, 29, 27, 255), "hover": (220, 218, 211, 255), "active": (42, 120, 214, 255), "on_active": (255, 255, 255, 255)},
            "mono": {"ink": (0, 0, 0, 255), "hover": (225, 225, 225, 255), "active": (0, 0, 0, 255), "on_active": (255, 255, 255, 255)}}


def _polygon(sides, radius=0.4, centre=(0.5, 0.5)):
    return [(centre[0] + radius * math.sin(2 * math.pi * i / sides), centre[1] - radius * math.cos(2 * math.pi * i / sides))
            for i in range(sides)]


def _ring(pen, sides):
    corners = _polygon(sides)
    pen.closed(corners)


def _benzene(pen):
    corners = _polygon(6)
    pen.closed(corners)
    inner = _polygon(6, 0.27)
    for i in (0, 2, 4):  # shorter than the side, as a double bond is drawn
        (x0, y0), (x1, y1) = inner[i], inner[i + 1]
        pen.line((x0 + (x1 - x0) * 0.12, y0 + (y1 - y0) * 0.12), (x1 - (x1 - x0) * 0.12, y1 - (y1 - y0) * 0.12))


def _cyclopentadiene(pen):
    pen.closed(_polygon(5))
    inner = _polygon(5, 0.27)
    for i in (0, 2):  # as for benzene
        (x0, y0), (x1, y1) = inner[i], inner[i + 1]
        pen.line((x0 + (x1 - x0) * 0.12, y0 + (y1 - y0) * 0.12), (x1 - (x1 - x0) * 0.12, y1 - (y1 - y0) * 0.12))


def _select(pen):
    x0, y0, x1, y1 = 0.22, 0.22, 0.78, 0.78
    for i in range(4):
        low, high = 0.22 + i * 0.15, 0.22 + i * 0.15 + 0.09
        pen.line((low, y0), (high, y0))
        pen.line((low, y1), (high, y1))
        pen.line((x0, low), (x0, high))
        pen.line((x1, low), (x1, high))


def _erase(pen):
    pen.closed([(0.18, 0.62), (0.52, 0.24), (0.82, 0.46), (0.48, 0.84)])
    pen.fill([(0.18, 0.62), (0.35, 0.43), (0.65, 0.65), (0.48, 0.84)])


def _pan(pen):
    pen.line((0.5, 0.16), (0.5, 0.84))
    pen.line((0.16, 0.5), (0.84, 0.5))
    for tip, left, right in (((0.5, 0.14), (0.4, 0.26), (0.6, 0.26)), ((0.5, 0.86), (0.4, 0.74), (0.6, 0.74)),
                             ((0.14, 0.5), (0.26, 0.4), (0.26, 0.6)), ((0.86, 0.5), (0.74, 0.4), (0.74, 0.6))):
        pen.fill([tip, left, right])


def _text(pen):
    pen.line((0.28, 0.8), (0.5, 0.2))
    pen.line((0.5, 0.2), (0.72, 0.8))
    pen.line((0.36, 0.58), (0.64, 0.58))


def _parallel(pen, offsets):
    for offset in offsets:
        pen.line((0.22 + offset * 0.5, 0.78 + offset * 0.86), (0.78 + offset * 0.5, 0.22 + offset * 0.86))


def _wedge(pen):
    pen.fill([(0.2, 0.8), (0.72, 0.16), (0.86, 0.3)])


def _hash(pen):
    for i in range(1, 7):
        along, half = i / 6.5, i / 6.5 * 0.1
        x, y = 0.2 + along * 0.6, 0.8 - along * 0.6
        pen.line((x - half, y - half), (x + half, y + half))


def _charge(pen, plus):
    pen.circle((0.5, 0.5), 0.3)
    pen.line((0.35, 0.5), (0.65, 0.5))
    if plus:
        pen.line((0.5, 0.35), (0.5, 0.65))


_ICONS = {"select": _select, "erase": _erase, "pan": _pan, "text": _text,
          "single": lambda pen: _parallel(pen, [0]), "double": lambda pen: _parallel(pen, [-0.07, 0.07]),
          "triple": lambda pen: _parallel(pen, [-0.12, 0, 0.12]), "wedge": _wedge, "hash": _hash,
          "plus": lambda pen: _charge(pen, True), "minus": lambda pen: _charge(pen, False),
          "ring3": lambda pen: _ring(pen, 3), "ring4": lambda pen: _ring(pen, 4), "ring5": lambda pen: _ring(pen, 5),
          "ring6": lambda pen: _ring(pen, 6), "ring7": lambda pen: _ring(pen, 7), "ring8": lambda pen: _ring(pen, 8), "cyclopentadiene": _cyclopentadiene, "benzene": _benzene}


class _Pen:
    """Draws in a button's own coordinates, 0 to 1 each way."""

    def __init__(self, draw, x, y, width, height, colour):
        self.draw, self.colour = draw, colour
        self.size = min(width, height)
        self.x, self.y = x + (width - self.size) / 2, y + (height - self.size) / 2
        self.thickness = max(int(self.size * 0.05), 1)

    def point(self, p):
        return self.x + p[0] * self.size, self.y + p[1] * self.size

    def line(self, start, end):
        self.draw.line([self.point(start), self.point(end)], fill=self.colour, width=self.thickness)

    def closed(self, points):
        for i, point in enumerate(points):
            self.line(point, points[(i + 1) % len(points)])

    def fill(self, points):
        self.draw.polygon([self.point(p) for p in points], fill=self.colour)

    def circle(self, centre, radius):
        x, y = self.point(centre)
        r = radius * self.size
        self.draw.ellipse([x - r, y - r, x + r, y + r], outline=self.colour, width=self.thickness)


@lru_cache(maxsize=64)
def palette_image(names, per_row, button_width, button_height, active, hover, theme):
    """A PNG of the buttons, in rows of `per_row`. `active` and `hover` are indices or None. The rest of the image is transparent."""
    colours = _COLOURS.get(theme, _COLOURS["dark"])
    rows = math.ceil(len(names) / per_row)
    width, height = button_width * _OVERSAMPLE, button_height * _OVERSAMPLE
    image = Image.new("RGBA", (per_row * width, rows * height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index, name in enumerate(names):
        x, y = index % per_row * width, index // per_row * height
        gap, ink = _OVERSAMPLE * 2, colours["ink"]
        box = [x + gap, y + gap, x + width - gap, y + height - gap]
        if index == active:
            draw.rounded_rectangle(box, radius=_OVERSAMPLE * 4, fill=colours["active"])
            ink = colours["on_active"]
        elif index == hover:
            draw.rounded_rectangle(box, radius=_OVERSAMPLE * 4, fill=colours["hover"])
        _ICONS[name](_Pen(draw, x + gap, y + gap, width - 2 * gap, height - 2 * gap, ink))
    image = image.resize((per_row * button_width, rows * button_height), Image.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=False)
    return buffer.getvalue()
