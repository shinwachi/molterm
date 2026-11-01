"""The commands behind the web terminal: a small medicinal chemistry session, computed live with RDKit.

This is not a shell. A line is split into words and looked up in COMMANDS; nothing is ever executed. The server keeps no
state: the working set of compounds travels with each request. Every drawing and number comes from RDKit, and there is no
assay data, so nothing here says how potent a compound is.
"""
import re
import shlex
import textwrap
from functools import lru_cache

from rdkit import Chem, DataStructs
from rdkit.Chem import Crippen, Descriptors, FilterCatalog, Lipinski, QED, rdFingerprintGenerator, rdFMCS
from rdkit.Chem.rdRGroupDecomposition import RGroupDecompose

import braille
import graphics
import molecule
import render as renderer

MAX_LINE = 500
COLS_RANGE = (40, 240)
DEFAULT_COLS = 100

RED, GREEN, YELLOW, CYAN, BOLD, DIM = 31, 32, 33, renderer.ANSI_HIGHLIGHT, 1, 90

TEXT = "text"
DISPLAYS = (TEXT, graphics.KITTY, graphics.ITERM, graphics.SIXEL)
IMAGE_BACKGROUND = (0x11, 0x11, 0x13)  # what Sixel's edges blend into: the .term background, unless the client reports its own
_IMAGE_INK = {"dark": {BOLD: "#eeeeee", CYAN: graphics.HIGHLIGHT}, "light": {BOLD: "#1a1a1a", CYAN: "#00737a"}}

# name: (SMILES, what it is known for). Each structure's InChIKey was matched to this name on PubChem.
COMPOUNDS = {
    "gefitinib": ("COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1", "EGFR"),
    "erlotinib": ("COCCOc1cc2ncnc(Nc3cccc(C#C)c3)c2cc1OCCOC", "EGFR"),
    "icotinib": ("C#Cc1cccc(Nc2ncnc3cc4OCCOCCOCCOc4cc23)c1", "EGFR"),
    "lapatinib": ("CS(=O)(=O)CCNCc1ccc(o1)-c1ccc2ncnc(Nc3ccc(OCc4cccc(F)c4)c(Cl)c3)c2c1", "EGFR, HER2"),
    "vandetanib": ("COc1cc2c(Nc3ccc(Br)cc3F)ncnc2cc1OCC1CCN(C)CC1", "VEGFR2, EGFR, RET"),
    "afatinib": ("CN(C)C/C=C/C(=O)Nc1cc2c(Nc3ccc(F)c(Cl)c3)ncnc2cc1O[C@H]1CCOC1", "EGFR, HER2; covalent"),
    "dacomitinib": ("COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1NC(=O)/C=C/CN1CCCCC1", "EGFR, HER2, HER4; covalent"),
    "neratinib": ("CCOc1cc2ncc(C#N)c(Nc3ccc(OCc4ccccn4)c(Cl)c3)c2cc1NC(=O)/C=C/CN(C)C", "HER2, EGFR; covalent"),
    "osimertinib": ("COc1cc(N(C)CCN(C)C)c(NC(=O)C=C)cc1Nc1nccc(n1)-c1cn(C)c2ccccc12", "EGFR T790M; covalent"),
    "imatinib": ("Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1", "BCR-ABL"),
    "staurosporine": ("C[C@@]12[C@@H]([C@@H](C[C@@H](O1)n3c4ccccc4c5c3c6n2c7ccccc7c6c8c5C(=O)NC8)NC)OC",
                      "broad kinase inhibitor, research tool"),
    "quercetin": ("O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12", "natural flavonol"),
    "curcumin": ("COc1cc(/C=C/C(=O)CC(=O)/C=C/c2ccc(O)c(OC)c2)ccc1O", "natural product"),
    "epalrestat": ("C/C(=C\\c1ccccc1)/C=C1\\SC(=S)N(CC(=O)O)C1=O", "aldose reductase"),
    "nitrofurantoin": ("O=C1CN(/N=C/c2ccc([N+](=O)[O-])o2)C(=O)N1", "antibacterial"),
}
ALL_NAMES = list(COMPOUNDS)

# name, heading, format, the limit above which it counts against a rule of thumb
PROPERTIES = [
    ("mw", "MW", "{:.0f}", 500), ("logp", "cLogP", "{:.1f}", 5), ("tpsa", "TPSA", "{:.0f}", 140), ("hbd", "HBD", "{:d}", 5),
    ("hba", "HBA", "{:d}", 10), ("rotb", "RotB", "{:d}", 10), ("qed", "QED", "{:.2f}", None), ("ro5", "Ro5", "{:d}", 0),
    ("pains", "PAINS", "{:d}", 0), ("brenk", "Brenk", "{:d}", 0),
]
PROPERTY_NAMES = [p[0] for p in PROPERTIES]
_FILTER = re.compile(r"^(\w+)(<=|>=|<|>|=)(-?\d+(?:\.\d+)?)$")
_COMPARE = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, ">": lambda a, b: a > b,
            "=": lambda a, b: a == b}


class Problem(Exception):
    """A mistake in a command, shown in the terminal like a shell's error message."""


def _catalog(which):
    params = FilterCatalog.FilterCatalogParams()
    params.AddCatalog(which)
    return FilterCatalog.FilterCatalog(params)


_CATALOGS = {"pains": _catalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.PAINS),
             "brenk": _catalog(FilterCatalog.FilterCatalogParams.FilterCatalogs.BRENK)}
_MORGAN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


@lru_cache(maxsize=None)
def mol(name):
    return molecule.parse(COMPOUNDS[name][0]).mol


@lru_cache(maxsize=None)
def alerts(name, catalog):
    """Return [(description, atom indices)] for each alert a compound trips, in the catalogue's order."""
    found = []
    for entry in _CATALOGS[catalog].GetMatches(mol(name)):
        matches = entry.GetFilterMatches(mol(name))
        found.append((entry.GetDescription(), frozenset(index for m in matches for _, index in m.atomPairs)))
    return found


@lru_cache(maxsize=None)
def properties(name):
    m = mol(name)
    values = {"mw": Descriptors.MolWt(m), "logp": Crippen.MolLogP(m), "tpsa": Descriptors.TPSA(m),
              "hbd": Lipinski.NumHDonors(m), "hba": Lipinski.NumHAcceptors(m), "rotb": Lipinski.NumRotatableBonds(m),
              "qed": QED.qed(m)}
    values["ro5"] = sum((values["mw"] > 500, values["logp"] > 5, values["hbd"] > 5, values["hba"] > 10))
    values["pains"] = len(alerts(name, "pains"))
    values["brenk"] = len(alerts(name, "brenk"))
    return values


# The output is built as rows of (character, ANSI code), so that blocks of any kind can be set side by side.


def text(string, code=None):
    return [(char, code) for char in string]


def lines(*rows):
    return [text(row) if isinstance(row, str) else row for row in rows]


@lru_cache(maxsize=512)
def _drawing(smiles, highlight):
    rendering = renderer.render(molecule.parse(smiles).mol, 60, 24, highlight=highlight)
    return [list(zip(chars, codes)) for chars, codes in zip(rendering.grid, rendering.colors)]


def drawing(name, highlight=frozenset()):
    return _drawing(COMPOUNDS[name][0], frozenset(highlight))


def width(block):
    return max((len(row) for row in block), default=0)


def side_by_side(blocks, gap=3):
    height = max(len(block) for block in blocks)
    rows = []
    for index in range(height):
        row = []
        for position, block in enumerate(blocks):
            cells = block[index] if index < len(block) else []
            row += cells
            if position < len(blocks) - 1:
                row += text(" " * (width(block) - len(cells) + gap))
        rows.append(row)
    return rows


def tiles(blocks, cols, gap=3):
    """Set blocks side by side, starting a new row when the next one would not fit in cols."""
    rows, shelf, used = [], [], 0
    for block in blocks:
        if shelf and used + gap + width(block) > cols:
            rows += side_by_side(shelf, gap) + [[]]
            shelf, used = [], 0
        used += (gap if shelf else 0) + width(block)
        shelf.append(block)
    return rows + side_by_side(shelf, gap) if shelf else rows


def table(headings, body, right=()):
    """Align columns. A body cell is a string or (string, code); columns listed in `right` are right-aligned."""
    cells = [[(c, None) if isinstance(c, str) else c for c in row] for row in body]
    sizes = [max([len(heading)] + [len(row[i][0]) for row in cells]) for i, heading in enumerate(headings)]

    def align(string, i):
        return string.rjust(sizes[i]) if i in right else string.ljust(sizes[i])

    rows = [text("  ".join(align(h, i) for i, h in enumerate(headings)), DIM)]
    for row in cells:
        out = []
        for i, (string, code) in enumerate(row):
            out += text(align(string, i), code) + (text("  ") if i < len(row) - 1 else [])
        rows.append(out)
    return rows


def _labelled(name, highlight=frozenset(), code=BOLD):
    return drawing(name, highlight) + [[]] + [text(name, code)]


def _caption(block, caption, code=DIM):
    return block + [[]] + [text(caption, code)]


# Commands. Each takes (session, words) and returns rows.


class Session:
    def __init__(self, names, cols, display=TEXT, cell_pixels=None, theme=graphics.DEFAULT_THEME, background=None, line_editing=True):
        self.names = names
        self.cols = cols
        self.display = display
        self.cell_pixels = cell_pixels
        self.theme = theme
        self.background = background or IMAGE_BACKGROUND
        self.line_editing = line_editing
        self.clear = False

    def gallery(self, entries):
        """Show compounds side by side. An entry is (name, atoms to mark, code for the name, lines of alert names)."""
        if self.display == TEXT:
            return tiles([_labelled(name, atoms, code) + [text(line, CYAN) for line in extra]
                          for name, atoms, code, extra in entries], self.cols)
        ink = _IMAGE_INK[self.theme]
        return self.picture([(mol(name), atoms, [(name, ink[code])] + [(line, ink[CYAN]) for line in extra])
                             for name, atoms, code, extra in entries])

    def picture(self, items):
        """One image, as a single row holding its whole escape sequence, which prints like any other text."""
        # PNGs are scaled by the terminal, so they can carry more pixels than the screen for sharpness. Sixel can't.
        # A web terminal can ask for fewer, since restty only keeps about 10 MB of decoded images.
        default = graphics.SIXEL_CELL_PIXELS if self.display == graphics.SIXEL else graphics.CELL_PIXELS
        cell = default if self.display == graphics.ITERM else self.cell_pixels or default
        sheet, cols = graphics.gallery(items, self.cols, cell, self.theme)
        return [[(graphics.sequence(sheet, cols, self.display, self.background, self.theme), None)]]

    def require_names(self, words, least=1, most=None):
        if len(words) < least or (most is not None and len(words) > most):
            raise Problem("Name " + (f"{least} compound" + ("s" if least > 1 else "")) + ". See: ls")
        return [resolve(word) for word in words]


def resolve(word):
    name = word.lower()
    if name not in COMPOUNDS:
        raise Problem(f"No compound called {word}. See: ls")
    return name


def cmd_help(session, words):
    body = [(usage, summary) for usage, summary in USAGE]
    editing = [text("Names complete with Tab. Up and down recall commands.", DIM)] if session.line_editing else []
    return table(["command", "what it does"], body) + [[]] + editing + lines(
        text("MW, cLogP, TPSA, QED and alerts are computed from structure. There is no assay data here.", DIM))


def cmd_ls(session, words):
    body = [(name, COMPOUNDS[name][1]) for name in session.names]
    return table(["compound", "known for"], body) + [[]] + lines(text(_count(session), DIM))


def _count(session):
    return f"{len(session.names)} of {len(ALL_NAMES)} compounds in the working set"


def cmd_grid(session, words):
    names = [resolve(word) for word in words] or session.names
    if not names:
        raise Problem("The working set is empty. Start again with: reset")
    return session.gallery([(name, frozenset(), BOLD, []) for name in names])


def cmd_show(session, words):
    if len(words) != 1:
        raise Problem("Name one compound, or give a SMILES. See: ls")
    if words[0].lower() in COMPOUNDS:
        name = words[0].lower()
        values, flagged = properties(name), [(c, a) for c in ("pains", "brenk") for a in alerts(name, c)]
        rows = table(["property", "value"], [(_heading(k), _formatted(k, values[k])) for k in PROPERTY_NAMES], right=(1,))
        side = lines(text(name, BOLD), text(COMPOUNDS[name][1], DIM), text(COMPOUNDS[name][0], DIM)) + [[]] + rows
        if flagged:
            side += [[]] + [text(f"{c}: {a[0]}", YELLOW) for c, a in flagged]
        if session.display != TEXT:
            return session.picture([(mol(name), frozenset(), [])]) + side
        return tiles([drawing(name), side], session.cols, gap=4)
    try:
        parsed = molecule.parse(words[0], "smiles")
    except molecule.ParseError as error:
        raise Problem(f"{words[0]} is not a compound here or a valid SMILES. {error.message}") from error
    info = parsed.to_dict()
    side = lines(text(info["formula"], BOLD), text(info["smiles"], DIM))
    if session.display != TEXT and parsed.mol.GetNumBonds():  # atoms without bonds have nothing to draw, so stay as text
        return session.picture([(parsed.mol, frozenset(), [])]) + side
    return tiles([_drawing(words[0], frozenset()), side], session.cols, gap=4)


def _heading(key):
    return next(p[1] for p in PROPERTIES if p[0] == key)


def _formatted(key, value):
    prop = next(p for p in PROPERTIES if p[0] == key)
    string = prop[2].format(value)
    return (string, RED) if prop[3] is not None and value > prop[3] else string


def cmd_props(session, words):
    if len(words) > 1 or (words and words[0] not in PROPERTY_NAMES):
        raise Problem(f"Sort by one of: {', '.join(PROPERTY_NAMES)}")
    names = list(session.names)
    if words:
        names.sort(key=lambda n: -properties(n)[words[0]])
    body = [[name] + [_formatted(k, properties(name)[k]) for k in PROPERTY_NAMES] for name in names]
    headings = ["compound"] + [_heading(k) for k in PROPERTY_NAMES]
    return table(headings, body, right=range(1, len(headings))) + [[]] + lines(
        text("Red: MW>500, cLogP>5, TPSA>140, HBD>5, HBA>10, RotB>10,", DIM),
        text("any Ro5 violation or alert. Higher QED is more drug-like.", DIM))


def cmd_alerts(session, words):
    if len(words) != 1 or words[0] not in _CATALOGS:
        raise Problem("Choose a filter set: alerts pains, or alerts brenk")
    catalog = words[0]
    flagged = [n for n in session.names if alerts(n, catalog)]
    if not flagged:
        return lines(text(f"Nothing in the working set trips {catalog.upper()}.", GREEN))
    entries = []
    for name in flagged:
        found = alerts(name, catalog)
        entries.append((name, frozenset(a for _, group in found for a in group), BOLD, [d for d, _ in found]))
    clean = len(session.names) - len(flagged)
    return session.gallery(entries) + [[]] + lines(text(f"{len(flagged)} flagged, {clean} clear of {catalog.upper()}.", DIM))


def cmd_filter(session, words):
    if not words:
        raise Problem("Give conditions, such as: filter pains=0 mw<=500")
    conditions = []
    for word in words:
        match = _FILTER.match(word)
        if not match or match[1] not in PROPERTY_NAMES:
            raise Problem(f"{word} is not a condition. Use one of {', '.join(PROPERTY_NAMES)}, such as mw<=500")
        conditions.append((match[1], _COMPARE[match[2]], float(match[3])))
    kept = [n for n in session.names if all(test(properties(n)[key], limit) for key, test, limit in conditions)]
    gone = [n for n in session.names if n not in kept]
    session.names = kept
    keys = [key for key, _, _ in conditions]
    rows = [text(f"Removed {len(gone)}, kept {len(kept)}.", BOLD)]
    if gone:
        rows += [[]] + table(["removed"] + [_heading(k) for k in keys],
                             [[n] + [_formatted(k, properties(n)[k]) for k in keys] for n in gone],
                             right=range(1, len(keys) + 1))
    return rows


def cmd_reset(session, words):
    session.names = list(ALL_NAMES)
    return lines(text(_count(session), DIM))


def cmd_clear(session, words):
    session.clear = True
    return []


def cmd_plot(session, words):
    if len(words) != 2 or any(w not in PROPERTY_NAMES for w in words):
        raise Problem(f"Choose two properties, x then y, from: {', '.join(PROPERTY_NAMES)}")
    if len(session.names) < 2:
        raise Problem("A plot needs at least two compounds. Start again with: reset")
    return scatter(session.names, *words, session.cols)


def scatter(names, x_key, y_key, cols):
    """A scatter plot in Braille dots, with limit lines and names set as letters beside their points."""
    import numpy as np

    limits = {p[0]: p[3] for p in PROPERTIES}
    x_limit, y_limit = limits[x_key], limits[y_key]
    points = {n: (properties(n)[x_key], properties(n)[y_key]) for n in names}

    def span(values, limit):
        values = list(values) + ([limit] if limit else [])
        low, high = min(values), max(values)
        margin = (high - low) * 0.08 or 1
        return low - margin, high + margin

    x_low, x_high = span([p[0] for p in points.values()], x_limit)
    y_low, y_high = span([p[1] for p in points.values()], y_limit)
    plot_cols, plot_rows, gutter = min(max(cols - 8, 30), 70), 18, 7
    dots = np.zeros((plot_rows * braille.CELL_HEIGHT, plot_cols * braille.CELL_WIDTH), dtype=bool)
    tints = [[None] * plot_cols for _ in range(plot_rows)]

    def to_dot(x, y):
        col = round((x - x_low) / (x_high - x_low) * (dots.shape[1] - 2))
        row = round((y_high - y) / (y_high - y_low) * (dots.shape[0] - 2))
        return row, col

    if x_limit:
        col = to_dot(x_limit, y_low)[1]
        dots[::3, col] = True
        for row in tints:
            row[col // braille.CELL_WIDTH] = DIM
    if y_limit:
        row = to_dot(x_low, y_limit)[0]
        dots[row, ::3] = True
        tints[row // braille.CELL_HEIGHT] = [DIM] * plot_cols
    cells = {}
    for name, (x, y) in points.items():
        row, col = to_dot(x, y)
        dots[row:row + 2, col:col + 2] = True
        outside = (x_limit and x > x_limit) or (y_limit and y > y_limit)
        cells[name] = (row // braille.CELL_HEIGHT, col // braille.CELL_WIDTH, RED if outside else GREEN)
    grid = braille.bitmap_to_grid(dots)
    for row, col, code in cells.values():
        tints[row][col] = code

    taken = {(row, col) for row, col, _ in cells.values()}
    unplaced = []
    for name in sorted(names, key=lambda n: cells[n][:2]):
        row, col, code = cells[name]
        for r, c in ((row, col + 1), (row, col - len(name) - 1), (row - 1, col + 1), (row + 1, col + 1),
                     (row - 1, col - len(name)), (row + 1, col - len(name))):
            span_cells = {(r, c + i) for i in range(-1, len(name) + 1)}
            if 0 <= r < plot_rows and c >= 0 and c + len(name) <= plot_cols and not span_cells & taken:
                grid[r][c:c + len(name)] = list(name)
                tints[r][c:c + len(name)] = [code] * len(name)
                taken |= span_cells
                break
        else:
            unplaced.append(name)

    heading = next(p[1] for p in PROPERTIES if p[0] == y_key)
    rows = [text(" " * gutter + heading + " up, " + _heading(x_key) + " across", DIM)]
    for index, (chars, codes) in enumerate(zip(grid, tints)):
        tick = ""
        if index in (0, plot_rows // 2, plot_rows - 1):
            tick = f"{y_high - (y_high - y_low) * index / (plot_rows - 1):.1f}"
        rows.append(text(tick.rjust(gutter - 1) + " ", DIM) + list(zip(chars, codes)))
    left, middle, right = (f"{x_low + (x_high - x_low) * t:.1f}" for t in (0, 0.5, 1))
    axis = list((" " * gutter + left).ljust(gutter + plot_cols))
    axis[gutter + plot_cols // 2 - len(middle) // 2:gutter + plot_cols // 2 + len(middle) - len(middle) // 2] = list(middle)
    axis[gutter + plot_cols - len(right):gutter + plot_cols] = list(right)
    rows.append(text("".join(axis), DIM))
    if unplaced:
        rows += [[]] + [text("No room to label: " + ", ".join(unplaced), DIM)]
    rows += [[]] + [text("Green is inside the dotted limit lines, red is past one.", DIM)]
    return rows


def cmd_match(session, words):
    if len(words) != 1:
        raise Problem("Give one SMARTS pattern, such as: match C=CC(=O)N")
    query = Chem.MolFromSmarts(words[0])
    if query is None:
        raise Problem(f"{words[0]} is not valid SMARTS.")
    entries, misses = [], []
    for name in session.names:
        found = mol(name).GetSubstructMatches(query)
        if found:
            entries.append((name, frozenset(a for match in found for a in match), BOLD, []))
        else:
            misses.append(name)
    if not entries:
        return lines(text("No compound in the working set matches.", YELLOW))
    rows = session.gallery(entries) + [[]] + lines(text(f"{len(entries)} of {len(session.names)} match.", BOLD))
    wrapped = textwrap.wrap("No match: " + ", ".join(misses), session.cols) if misses else []
    return rows + [text(row, DIM) for row in wrapped]


def cmd_sim(session, words):
    name = session.require_names(words, 1, 1)[0]
    reference = _MORGAN.GetFingerprint(mol(name))
    scores = sorted(((DataStructs.TanimotoSimilarity(reference, _MORGAN.GetFingerprint(mol(n))), n)
                     for n in session.names if n != name), reverse=True)
    body = []
    for score, other in scores:
        bar = "⣿" * round(score * 30)
        body.append([other, f"{score:.2f}", (bar, CYAN)])
    closest = [other for _, other in scores[:3]]
    return table([f"like {name}", "Tanimoto", ""], body, right=(1,)) + [[]] + session.gallery(
        [(name, frozenset(), CYAN, [])] + [(other, frozenset(), BOLD, []) for other in closest]) + [[]] + lines(
        text("Morgan fingerprints, radius 2. Structure only: similar does not mean equally potent.", DIM))


def cmd_compare(session, words):
    first, second = session.require_names(words, 2, 2)
    common = rdFMCS.FindMCS([mol(first), mol(second)], timeout=2, ringMatchesRingOnly=True, completeRingsOnly=True)
    query = Chem.MolFromSmarts(common.smartsString)

    def differs(name):
        return frozenset(range(mol(name).GetNumAtoms())) - frozenset(mol(name).GetSubstructMatch(query))

    drawings = session.gallery([(first, differs(first), BOLD, []), (second, differs(second), BOLD, [])])
    body = []
    for key in PROPERTY_NAMES:
        a, b = properties(first)[key], properties(second)[key]
        form = next(p[2] for p in PROPERTIES if p[0] == key)
        # from the values as shown, so that the column adds up for someone reading it
        change = float(form.format(b)) - float(form.format(a))
        shown = form.format(int(change) if form == "{:d}" else change)
        body.append([_heading(key), _formatted(key, a), _formatted(key, b), ("+" if change > 0 else "") + shown])
    return drawings + [[]] + table(["property", first, second, "change"], body, right=(1, 2, 3)) + [[]] + lines(
        text(f"Cyan marks what differs. They share {common.numAtoms} atoms.", DIM))


def cmd_rgroups(session, words):
    if len(words) != 1:
        raise Problem("Give one core as SMARTS, such as: rgroups c1ccc2ncncc2c1")
    core = Chem.MolFromSmarts(words[0])
    if core is None:
        raise Problem(f"{words[0]} is not valid SMARTS.")
    names = [n for n in session.names if mol(n).HasSubstructMatch(core)]
    if not names:
        return lines(text("No compound in the working set has that core.", YELLOW))
    decomposed, _ = RGroupDecompose([core], [mol(n) for n in names], asSmiles=True, asRows=True)
    columns = sorted({key for row in decomposed for key in row if key != "Core"}, key=lambda k: int(k[1:]))

    def tidy(smiles):
        return re.sub(r"\[\*:\d+\]", "*", smiles)

    body = []
    varied = [c for c in columns if len({tidy(row.get(c, "")) for row in decomposed}) > 1]
    for name, row in zip(names, decomposed):
        body.append([name] + [tidy(row.get(c, "")) if tidy(row.get(c, "")) not in ("[H]*", "*[H]") else "-" for c in varied])
    rows = table(["compound"] + varied, body)
    hidden = len(columns) - len(varied)
    note = f"{len(names)} compounds on this core." + (f" {hidden} position{'s' if hidden > 1 else ''} never change{'s' if hidden == 1 else ''}, so not shown." if hidden else "")
    return rows + [[]] + lines(text(note, DIM))


COMMANDS = {"help": cmd_help, "ls": cmd_ls, "grid": cmd_grid, "show": cmd_show, "props": cmd_props, "alerts": cmd_alerts,
            "filter": cmd_filter, "plot": cmd_plot, "match": cmd_match, "rgroups": cmd_rgroups, "sim": cmd_sim,
            "compare": cmd_compare, "reset": cmd_reset, "clear": cmd_clear}
USAGE = [
    ("ls", "list the working set"),
    ("grid [names]", "draw compounds side by side"),
    ("show <name|SMILES>", "one structure with its properties"),
    ("props [sort key]", f"property table; sort by {', '.join(PROPERTY_NAMES)}"),
    ("alerts pains|brenk", "structural alerts, marked on the drawing"),
    ("filter <conditions>", "narrow the working set, such as: filter pains=0 mw<=500"),
    ("plot <x> <y>", "scatter plot, such as: plot mw logp"),
    ("match <SMARTS>", "substructure search, marked on the drawing"),
    ("rgroups <SMARTS>", "what varies around a shared core"),
    ("sim <name>", "the working set ranked by similarity"),
    ("compare <a> <b>", "two compounds, with what differs marked"),
    ("reset", "bring back all compounds"),
    ("clear", "clear the screen"),
]


def run(line, selection=None, cols=DEFAULT_COLS, display=TEXT, cell_pixels=None, theme=graphics.DEFAULT_THEME, background=None,
        line_editing=True):
    """Run one line; return {"output": text with ANSI colours, "selection": names, "clear": bool}.

    With a `display` other than text, drawings arrive as that graphics protocol's escape sequences instead of Braille.
    `theme` suits them to the terminal's background, and `line_editing` says whether its prompt has Tab and history.
    """
    names = [n for n in ALL_NAMES if n in set(selection)] if selection is not None else list(ALL_NAMES)
    session = Session(names, min(max(cols, COLS_RANGE[0]), COLS_RANGE[1]), display, cell_pixels, theme, background, line_editing)
    try:
        rows = _dispatch(line, session)
    except Problem as problem:
        rows = [text(str(problem), RED)]
    grid = [[char for char, _ in row] for row in rows]
    colors = [[code for _, code in row] for row in rows]
    return {"output": renderer.ansi_text(grid, colors), "selection": session.names, "clear": session.clear}


def _dispatch(line, session):
    if len(line) > MAX_LINE:
        raise Problem(f"That line is too long: the limit is {MAX_LINE} characters.")
    try:
        words = shlex.split(line)
    except ValueError as error:
        raise Problem(f"Could not read that line: {error}.") from error
    if not words:
        return []
    command = COMMANDS.get(words[0].lower())
    if command is None:
        raise Problem(f"Unknown command: {words[0]}. See: help")
    return command(session, words[1:])
