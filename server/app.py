"""Molterm TUI Renderer — TUI renderer for chemical structures using RDKit and advanced text graphics"""
import os
import re

import rdkit
from flask import Flask, jsonify, render_template, request, send_from_directory

import board
import editor
import graphics
import medchem
import molecule
import render as renderer
import samples
import showcase as showcase_script
import story

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)


def version():
    try:
        with open(os.path.join(HERE, "..", "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "dev"


SERVICE = {"service": "molterm", "version": version(), "env": os.environ.get("APP_ENV", "dev")}


@app.route("/")
def index():
    return render_template("index.html", page="renderer", cols=renderer.DEFAULT_COLS, rows=renderer.DEFAULT_ROWS,
                           cols_range=renderer.COLS_RANGE, rows_range=renderer.ROWS_RANGE, **SERVICE)


@app.route("/demo")
def demo():
    default = next(s for _, items in samples.GROUPS for name, s, _ in items if name == samples.DEFAULT)
    return render_template("demo.html", page="demo", groups=samples.GROUPS, default=default, **SERVICE)


@app.route("/terminal")
def terminal():
    completions = sorted({*medchem.COMMANDS, *medchem.ALL_NAMES, *medchem.PROPERTY_NAMES})
    return render_template("terminal.html", page="terminal", steps=story.STEPS, completions=completions, **SERVICE)


@app.route("/compare")
def compare():
    completions = sorted({*medchem.COMMANDS, *medchem.ALL_NAMES, *medchem.PROPERTY_NAMES})
    return render_template("compare.html", page="compare", steps=story.COMPARE_STEPS, completions=completions, **SERVICE)


@app.route("/kitty")
def kitty():
    completions = sorted({*medchem.COMMANDS, *medchem.ALL_NAMES, *medchem.PROPERTY_NAMES})
    return render_template("kitty.html", page="kitty", steps=story.STEPS, completions=completions, host=request.host_url.rstrip("/"),
                           **SERVICE)


@app.route("/showcase")
def showcase():
    return render_template("showcase.html", page="showcase", **SERVICE)


@app.route("/vendor/<path:name>")
def vendor(name):
    """Front-end libraries, fetched and checked when the image is built."""
    return send_from_directory(os.path.join(HERE, "..", "vendor"), name)


@app.route("/api/v1/term", methods=["POST"])
def term():
    """JSON body {"line": "<command>", "selection": [names] or null, "cols": width, "display": "text|kitty|iterm|sixel",
    "cell_width": pixels}; only `line` is required. Returns output, selection and clear."""
    body = request.get_json(silent=True)
    line = body.get("line") if isinstance(body, dict) else None
    if not isinstance(line, str):
        return jsonify(error={"code": "bad_request", "message": "Send a JSON object with a \"line\" field."}), 400
    selection, cols = body.get("selection"), body.get("cols", medchem.DEFAULT_COLS)
    if selection is not None and not (isinstance(selection, list) and all(isinstance(n, str) for n in selection)):
        return jsonify(error={"code": "bad_request", "message": "selection must be a list of names."}), 400
    if isinstance(cols, bool) or not isinstance(cols, int):
        return jsonify(error={"code": "bad_request", "message": "cols must be a whole number."}), 400
    display, cell_pixels = body.get("display", medchem.TEXT), body.get("cell_width")
    if display not in medchem.DISPLAYS:
        return jsonify(error={"code": "bad_request", "message": f"display must be one of {', '.join(medchem.DISPLAYS)}."}), 400
    low, high = graphics.CELL_PIXELS_RANGE
    if cell_pixels is not None and (isinstance(cell_pixels, bool) or not isinstance(cell_pixels, int) or not low <= cell_pixels <= high):
        return jsonify(error={"code": "bad_request", "message": f"cell_width must be a whole number from {low} to {high}."}), 400
    return jsonify(medchem.run(line, selection, cols, display, cell_pixels))


@app.route("/health")
def health():
    return jsonify(status="ok", rdkit=rdkit.__version__, **SERVICE)


@app.route("/api/v1/hello")
def hello():
    return jsonify(message="hello from molterm")


@app.route("/api/v1/parse", methods=["POST"])
def parse():
    """JSON body {"input": "<SMILES or molblock>", "format": "auto|smiles|molblock"}; `format` is optional."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error={"code": "bad_request", "message": "Send a JSON object with an \"input\" field."}), 400
    try:
        return jsonify(molecule.parse(body.get("input"), body.get("format", "auto")).to_dict())
    except molecule.ParseError as e:
        return jsonify(error={"code": e.code, "message": e.message}), 400


def render_options(values, modes=renderer.MODES):
    """Read cols, rows and mode from a mapping of request values, or raise ParseError."""
    mode = values.get("mode", renderer.DEFAULT_MODE)
    if mode not in modes:
        raise molecule.ParseError("bad_request", f"mode must be one of {', '.join(modes)}.")
    sizes = []
    for name, default, (low, high) in (("cols", renderer.DEFAULT_COLS, renderer.COLS_RANGE),
                                       ("rows", renderer.DEFAULT_ROWS, renderer.ROWS_RANGE)):
        value = values.get(name, default)
        try:
            if isinstance(value, (bool, float)):
                raise ValueError
            size = int(value)
        except (TypeError, ValueError):
            size = None
        if size is None or not low <= size <= high:
            raise molecule.ParseError("bad_request", f"{name} must be a whole number from {low} to {high}.")
        sizes.append(size)
    return sizes[0], sizes[1], mode


def sixel_options(values):
    """Read the optional cell_width and background, or raise ParseError."""
    cell_pixels, background = values.get("cell_width"), values.get("background")
    if cell_pixels is not None:
        low, high = graphics.CELL_PIXELS_RANGE
        if not cell_pixels.isascii() or not cell_pixels.isdigit() or not low <= int(cell_pixels) <= high:
            raise molecule.ParseError("bad_request", f"cell_width must be a whole number from {low} to {high}.")
        cell_pixels = int(cell_pixels)
    if background is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{6}", background):
            raise molecule.ParseError("bad_request", "background must be a colour as RRGGBB.")
        background = tuple(int(background[i:i + 2], 16) for i in (0, 2, 4))
    return cell_pixels, background


@app.route("/molterm")
def client_script():
    """The terminal client, set to call back to this server."""
    with open(os.path.join(HERE, "client", "molterm.sh")) as f:
        script = f.read().replace("@@URL@@", request.host_url.rstrip("/"))
    return script, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/api/v1/term.txt")
def term_text():
    """The same commands for the client, which can't read JSON: ?line=<command>[&selection=a,b][&cols=][&display=][&theme=], and
    for Sixel [&cell_width=][&background=]. The first line is "<1 to clear the screen, or 0> <selection>", then the output."""
    plain = {"Content-Type": "text/plain; charset=utf-8"}
    line, display, theme = request.args.get("line"), request.args.get("display", medchem.TEXT), request.args.get("theme", graphics.DEFAULT_THEME)
    selection = request.args.get("selection")
    try:
        if line is None:
            raise molecule.ParseError("bad_request", "Give a command as line.")
        if display not in medchem.DISPLAYS:
            raise molecule.ParseError("bad_request", f"display must be one of {', '.join(medchem.DISPLAYS)}.")
        if theme not in graphics.THEMES:
            raise molecule.ParseError("bad_request", f"theme must be one of {', '.join(graphics.THEMES)}.")
        cols = request.args.get("cols", str(medchem.DEFAULT_COLS))
        if not cols.isascii() or not cols.isdigit() or len(cols) > 6:
            raise molecule.ParseError("bad_request", "cols must be a whole number.")
        cell_pixels, background = sixel_options(request.args)
    except molecule.ParseError as e:
        return e.message + "\n", 400, plain
    names = None if selection is None else [name for name in selection.split(",") if name]
    result = medchem.run(line, names, int(cols), display, cell_pixels, theme, background, line_editing=False)
    return f"{int(result['clear'])} {','.join(result['selection'])}\n{result['output']}", 200, plain


@app.route("/api/v1/story.txt")
def story_text():
    """The guided session for the client: one step per line, as title, command, before and after, separated by tabs."""
    rows = ["\t".join((step["title"], step["command"], step["before"], step["after"])) for step in story.STEPS]
    return "\n".join(rows) + "\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


def terminal_form(form):
    """What the editor and the list both need from a request: cols, rows, raw input, cell width, cell height, pixels and theme."""

    def whole(name, limits, required=True):
        value = form.get(name)
        if value is None and not required:
            return None
        if value is None or not value.isascii() or not value.isdigit() or not limits[0] <= int(value) <= limits[1]:
            raise molecule.ParseError("bad_request", f"{name} must be a whole number from {limits[0]} to {limits[1]}.")
        return int(value)

    cols, rows = whole("cols", editor.COLS_RANGE), whole("rows", editor.ROWS_RANGE)
    cell_width = whole("cell_width", editor.CELL_WIDTH_RANGE, required=False)
    cell_height = whole("cell_height", editor.CELL_HEIGHT_RANGE, required=False)
    theme = form.get("theme", graphics.DEFAULT_THEME)
    if theme not in graphics.THEMES:
        raise molecule.ParseError("bad_request", f"theme must be one of {', '.join(graphics.THEMES)}.")
    events = form.get("events", "")
    if len(events) > editor.MAX_EVENTS * 2 or not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", events):
        raise molecule.ParseError("bad_request", "events must be hexadecimal bytes, and not too many.")
    return cols, rows, bytes.fromhex(events).decode("latin-1"), cell_width, cell_height, form.get("pixels") == "1", theme


def editor_box(text):
    """A box as row,col,rows,cols, counted from 0 at the top left of the window."""
    if not text:
        return None
    if not re.fullmatch(r"[0-9]{1,4},[0-9]{1,4},[0-9]{1,4},[0-9]{1,4}", text, re.ASCII):
        raise molecule.ParseError("bad_request", "box must be row,col,rows,cols, as whole numbers.")
    return tuple(int(part) for part in text.split(","))


@app.route("/api/v1/edit", methods=["POST"])
def edit():
    """One step of the molecular editor, for the client. Form fields: `cols` and `rows`, `events` (raw input, as hex), `state` (from
    the last answer) or `smiles` to begin, and optional `cell_width`, `cell_height`, `pixels=1` (mouse reports are in pixels), `theme`.
    To stay inside part of a window that a host program owns, `box` (row,col,rows,cols, from 0 and counting the border) and `title`.

    The first line of the answer is "<0 to go on, 1 finished, 2 failed, 3 cancelled> <state>". After it comes what to print: on finishing,
    the SMILES.
    """
    plain = {"Content-Type": "text/plain; charset=utf-8"}
    form = request.form
    try:
        cols, rows, raw, cell_width, cell_height, pixels, theme = terminal_form(form)
        status, state, output = editor.step(form.get("state", ""), form.get("smiles", ""), raw, cols, rows, cell_width, cell_height, pixels, theme,
                                            editor_box(form.get("box", "")), form.get("title", "")[:200])
    except (molecule.ParseError, editor.StateError) as e:
        return (e.message if isinstance(e, molecule.ParseError) else str(e)) + "\n", 400, plain
    return f"{status} {state}\n{output}", 200, plain


@app.route("/api/v1/tui", methods=["POST"])
def tui():
    """One step of the list of structures, whose editable ones open the editor in a box. The fields and answer are those of /api/v1/edit,
    with `items` instead of `smiles` to begin: lines of "SMILES name", with ro: in front of what can't be edited. On finishing, it prints
    the list as it now stands, in the same form without the ro:."""
    plain = {"Content-Type": "text/plain; charset=utf-8"}
    form = request.form
    try:
        cols, rows, raw, cell_width, cell_height, pixels, theme = terminal_form(form)
        status, state, output = board.step(form.get("state", ""), form.get("items", ""), raw, cols, rows, cell_width, cell_height, pixels, theme)
    except (molecule.ParseError, editor.StateError) as e:
        return (e.message if isinstance(e, molecule.ParseError) else str(e)) + "\n", 400, plain
    return f"{status} {state}\n{output}", 200, plain


@app.route("/api/v1/showcase")
def showcase_frames():
    """The automated demo, for a window of ?cols=&rows=&cell_width=&cell_height=. Each frame has `caption`, `output` (what to print), `pointer`
    ([col, row] in cells, or null), `click`, `key` and `pause` (milliseconds to wait afterwards)."""
    try:
        cols, rows, _, cell_width, cell_height, _, _ = terminal_form(request.args)
        if not cell_width or not cell_height:
            raise molecule.ParseError("bad_request", "Give cell_width and cell_height.")
        return jsonify(frames=showcase_script.frames(cols, rows, cell_width, cell_height))
    except molecule.ParseError as e:
        return jsonify(error={"code": e.code, "message": e.message}), 400
    except showcase_script.ShowcaseError as e:
        return jsonify(error={"code": "bad_request", "message": str(e)}), 400


@app.route("/api/v1/render", methods=["POST"])
def render_json():
    """Like /api/v1/parse, with optional `cols`, `rows` and `mode`; the result adds `render` (text, ansi, labels, ...)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error={"code": "bad_request", "message": "Send a JSON object with an \"input\" field."}), 400
    try:
        cols, rows, mode = render_options(body)
        parsed = molecule.parse(body.get("input"), body.get("format", "auto"))
    except molecule.ParseError as e:
        return jsonify(error={"code": e.code, "message": e.message}), 400
    return jsonify(**parsed.to_dict(), render=renderer.render(parsed.mol, cols, rows, mode).to_dict())


@app.route("/api/v1/render.txt")
def render_text():
    """For terminals: ?input=<SMILES or molblock>[&format=][&cols=][&rows=][&mode=][&color=1][&theme=light], and for Sixel
    [&cell_width=<the cell's pixels>][&background=<RRGGBB>]. Graphics modes also take [&fog=<0 to 100>], which text modes ignore."""
    plain = {"Content-Type": "text/plain; charset=utf-8"}
    theme = request.args.get("theme", graphics.DEFAULT_THEME)
    try:
        cols, rows, mode = render_options(request.args, (*renderer.MODES, *graphics.MODES))
        if theme not in graphics.THEMES:
            raise molecule.ParseError("bad_request", f"theme must be one of {', '.join(graphics.THEMES)}.")
        cell_pixels, background = sixel_options(request.args)
        fog = request.args.get("fog", "0")
        low, high = graphics.FOG_RANGE
        if not fog.isascii() or not fog.isdigit() or not low <= int(fog) <= high:
            raise molecule.ParseError("bad_request", f"fog must be a whole number from {low} to {high}.")
        parsed = molecule.parse(request.args.get("input"), request.args.get("format", "auto"))
    except molecule.ParseError as e:
        return e.message + "\n", 400, plain
    if mode in graphics.MODES:
        return graphics.render(parsed.mol, mode, cols, rows, theme, cell_pixels, background, int(fog)), 200, plain
    result = renderer.render(parsed.mol, cols, rows, mode)
    return (result.ansi if request.args.get("color") == "1" else result.text) + "\n", 200, plain


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8910)
