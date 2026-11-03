"""Colour themes: what the pictures, the editor and the list draw with, so that they suit the terminal's own colours.

Text modes use the terminal's ANSI colours and follow its theme by themselves. Pictures and the editor's bars carry their own colours, so a
theme names them. A theme can also be fitted to the terminal: `name:RRGGBB:RRGGBB` takes the terminal's background and foreground (either
may be left empty) over the named one's, and shades the bars between them, as the client does when it can ask the terminal.
"""
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache


def _hex(text):
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, amount):
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


@dataclass(frozen=True)
class Theme:
    name: str
    background: tuple  # the terminal's, as assumed: what Sixel blends edges into
    ink: tuple  # bonds, carbon and the editor's text
    elements: tuple  # N, O, S, halogens and the rest, in the order of ANSI red, green, yellow, blue and magenta, like the text modes
    highlight: tuple  # selected atoms, and the session's lists
    bar: tuple  # the editor's menu and status bars
    hover: tuple  # a button under the pointer
    dim: tuple  # hints
    accent: tuple  # the item picked in a menu, and the tool in use
    on_accent: tuple = (255, 255, 255)
    err: tuple = (239, 83, 80)
    warn: tuple = (240, 165, 0)
    panel: tuple = None  # the boxed editor's own background; the terminal's unless given
    accent_ink: tuple = None  # the accent as text; the accent unless given
    button: tuple = None  # the tool in use, if not the accent
    underline_pick: bool = False  # for a menu pick that colour alone can't show
    dark: bool = field(default=True, compare=False)


def _named(name, background, ink, elements, highlight, bar, hover, dim, accent, **rest):
    return Theme(name, *(_hex(c) for c in (background, ink)), tuple(_hex(c) for c in elements.split()), _hex(highlight), _hex(bar), _hex(hover),
                 _hex(dim), _hex(accent), **{k: (_hex(v) if isinstance(v, str) else v) for k, v in rest.items()})


_THEMES = [
    _named("dark", "000000", "f0efec", "ff8a80 7ddc7d f0d060 86b6ef e0a0f0", "5fe3e3", "2a2a28", "3a3a37", "8f8e86", "3987e5", panel="20201e"),
    _named("light", "ffffff", "1d1d1b", "c62828 2e7d32 8a6d00 1565c0 8e24aa", "00737a", "eeede8", "dcdad3", "5c5b55", "2a78d6",
           err="c62828", warn="b45309", panel="faf9f5", dark=False),
    # black on white, like a printed page; a menu's pick is grey and underlined, and the tool in use is white on black
    Theme("mono", (255, 255, 255), (0, 0, 0), ((0, 0, 0),) * 5, (0, 0, 0), (235, 235, 235), (225, 225, 225), (70, 70, 70), (205, 205, 205),
          on_accent=(0, 0, 0), err=(198, 40, 40), warn=(0, 0, 0), accent_ink=(0, 0, 0), button=(0, 0, 0), underline_pick=True, dark=False),
    Theme("mono-dark", (0, 0, 0), (255, 255, 255), ((255, 255, 255),) * 5, (255, 255, 255), (30, 30, 30), (50, 50, 50), (175, 175, 175),
          (75, 75, 75), err=(239, 83, 80), warn=(255, 255, 255), accent_ink=(255, 255, 255), button=(255, 255, 255), underline_pick=True),
    _named("solarized-dark", "002b36", "93a1a1", "dc322f 859900 b58900 268bd2 d33682", "2aa198", "073642", "0e4a59", "839496", "268bd2",
           on_accent="fdf6e3", err="dc322f", warn="cb4b16"),
    _named("solarized-light", "fdf6e3", "586e75", "dc322f 859900 b58900 268bd2 d33682", "2aa198", "eee8d5", "e2dcc8", "657b83", "268bd2",
           on_accent="fdf6e3", err="dc322f", warn="cb4b16", dark=False),
    _named("gruvbox-dark", "282828", "ebdbb2", "fb4934 b8bb26 fabd2f 83a598 d3869b", "8ec07c", "3c3836", "504945", "a89984", "458588",
           on_accent="fbf1c7", err="fb4934", warn="fe8019", accent_ink="83a598"),
    _named("gruvbox-light", "fbf1c7", "3c3836", "9d0006 79740e b57614 076678 8f3f71", "427b58", "ebdbb2", "d5c4a1", "7c6f64", "458588",
           on_accent="fbf1c7", err="9d0006", warn="af3a03", accent_ink="076678", dark=False),
    _named("nord", "2e3440", "eceff4", "bf616a a3be8c ebcb8b 81a1c1 b48ead", "88c0d0", "3b4252", "434c5e", "9aa3b5", "5e81ac",
           on_accent="eceff4", err="bf616a", warn="d08770", accent_ink="88c0d0"),
    _named("dracula", "282a36", "f8f8f2", "ff5555 50fa7b f1fa8c bd93f9 ff79c6", "8be9fd", "343746", "44475a", "8a95c4", "bd93f9",
           on_accent="282a36", err="ff5555", warn="ffb86c"),
    _named("catppuccin-mocha", "1e1e2e", "cdd6f4", "f38ba8 a6e3a1 f9e2af 89b4fa cba6f7", "94e2d5", "313244", "45475a", "9399b2", "89b4fa",
           on_accent="1e1e2e", err="f38ba8", warn="fab387"),
    _named("catppuccin-latte", "eff1f5", "4c4f69", "d20f39 40a02b df8e1d 1e66f5 8839ef", "179299", "e6e9ef", "ccd0da", "6c6f85", "1e66f5",
           on_accent="eff1f5", err="d20f39", warn="fe640b", dark=False),
    _named("tokyo-night", "1a1b26", "c0caf5", "f7768e 9ece6a e0af68 7aa2f7 bb9af7", "7dcfff", "24283b", "414868", "8a91b8", "7aa2f7",
           on_accent="1a1b26", err="f7768e", warn="ff9e64"),
]
THEMES = {theme.name: theme for theme in _THEMES}
NAMES = tuple(THEMES)
DEFAULT = "dark"
_SPEC = re.compile(r"([a-z-]+)(?::([0-9a-fA-F]{6})?:([0-9a-fA-F]{6})?)?")


class ThemeError(ValueError):
    pass


def spec(name, background=None, foreground=None):
    """A theme as one string, which is what passes through the code and into caches: the name, and the terminal's colours if known."""
    if background or foreground:
        return f"{name}:{(background or '').lower()}:{(foreground or '').lower()}"
    return name


@lru_cache(maxsize=64)
def get(text=DEFAULT):
    """The theme a spec names. Raises ThemeError for one that doesn't."""
    match = _SPEC.fullmatch(text or "")
    if not match or match[1] not in THEMES:
        raise ThemeError(f"theme must be one of {', '.join(NAMES)}, optionally followed by :RRGGBB:RRGGBB for the terminal's background and text.")
    theme = THEMES[match[1]]
    background, ink = (_hex(c) if c else None for c in match.group(2, 3))
    if not (background or ink):
        return theme
    background, ink = background or theme.background, ink or theme.ink
    # the bars and hints keep to the terminal's colours, as far from them as the named theme keeps from its own
    return replace(theme, name=text, background=background, ink=ink, panel=background, bar=_mix(background, ink, 0.08),
                   hover=_mix(background, ink, 0.16), dim=_mix(background, ink, 0.55),
                   button=ink if theme.button == theme.ink else theme.button)


def check(text):
    get(text)
    return text


def _sgr(prefix, colour, extra=""):
    return f"{prefix};2;{colour[0]};{colour[1]};{colour[2]}{extra}"


@lru_cache(maxsize=64)
def chrome(text):
    """SGR parameters for the editor's and the list's text, by role."""
    t = get(text)
    return {"bar": _sgr(48, t.bar), "ink": _sgr(38, t.ink), "dim": _sgr(38, t.dim), "pick": _sgr(48, t.accent),
            "on_pick": _sgr(38, t.on_accent, ";4" if t.underline_pick else ""), "err": _sgr(38, t.err), "warn": _sgr(38, t.warn),
            "pick_ink": _sgr(38, t.accent_ink or t.accent)}


def icons(text):
    """The tool palette's colours, as RGBA."""
    t = get(text)
    button = t.button or t.accent
    on_button = (255, 255, 255) if t.button and sum(t.button) < 384 else (0, 0, 0) if t.button else t.on_accent
    return {"ink": (*t.ink, 255), "hover": (*t.hover, 255), "active": (*button, 255), "on_active": (*on_button, 255)}


def panel(text):
    t = get(text)
    return (*(t.panel or t.background), 255)


def hex_colour(colour):
    return "#" + "".join(f"{c:02x}" for c in colour)
