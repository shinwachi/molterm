"""Record the demo: VisiData with the plugin, run in a pseudo-terminal of a fixed size, driven by the keys below.

Every frame is the terminal's real output between one key and the next, so the page that plays it shows exactly what a terminal would.
It writes data/recording.json, which the demo's page plays. Run it with `make record`; the demo also runs it when it starts.
"""
import codecs
import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "recording.json")
COLS, ROWS = 116, 34
QUIET, LONGEST = 0.9, 20.0  # a step ends once the output has paused this long, or after the longest wait
DOWN, UP, ENTER, ESC = "j", "k", "\r", "\x1b"

# (keys to send, what the viewer sees pressed, caption, pause on the frame in ms)
STEPS = [
    (None, None, "VisiData, a spreadsheet for the terminal, has opened drugs.csv: names and SMILES, as plain text.", 3500),
    ("l", "l", "The cursor moves to the smiles column.", 1600),
    ("\x1bm", "Alt+m", "Alt+m adds a structure column beside it, and every row grows tall enough to show its molecule.", 4500),
    (DOWN, "j", "The rows are still rows: the cursor moves down them...", 1400),
    (DOWN * 3, "j j j", "...and the sheet scrolls, the pictures with it. They're Kitty graphics, drawn by the molterm server.", 3500),
    ("\x1bt", "Alt+t", "Alt+t draws them as coloured Braille instead, which any terminal shows: over SSH, in tmux, anywhere.", 4500),
    ("\x1bt", "Alt+t", "And back to pictures.", 2500),
    ("\x1by", "Alt+y", "Alt+y adds properties computed from each structure: formula, MW, cLogP, TPSA, H-bond donors and acceptors, QED...", 4500),
    ("ll", "l l", "They are real columns, typed as numbers...", 2000),
    ("]", "]", "...so ] sorts by cLogP, highest first, and the structures follow their rows.", 4000),
    ("gk", "g k", "The top of the sheet: the most lipophilic.", 3000),
    ("\x1bo", "Alt+o", "Alt+o opens a molecule on its own sheet, large, with its properties.", 5000),
    ("q", "q", "q goes back to the spreadsheet.", 2500),
    ("\x1bg", "Alt+g", "Alt+g charts cLogP against MW below the table, on a split screen: a dot for each drug, coloured by its class.", 5000),
    ("\x1bn", "Alt+n", "Alt+n puts the cursor on a dot: the card names its molecule, and the table's cursor goes to its row.", 4500),
    ("\x1bn", "Alt+n", "The next dot, the next row...", 2800),
    ("\x1bn", "Alt+n", "...and the next. Alt+b goes back; the arrow keys move the cursor anywhere on the chart.", 3500),
    ("\t", "Tab", "Tab goes up to the table...", 2200),
    ("j", "j", "...and as its cursor moves down a row, the chart's moves to that row's dot.", 3500),
    ("j", "j", "Row and dot stay together, whichever is moved.", 3000),
    ("\t", "Tab", "Tab goes back to the chart.", 2000),
    ("\r", "Enter", "Enter opens the rows under the chart's cursor as a sheet of their own, structures and all.", 4500),
    ("qq", "q q", "q twice closes it and the chart.", 2500),
    ("\x1bt", "Alt+t", "In Braille once more, for the last look.", 3500),
    ("\x1bo", "Alt+o", "The molecule's sheet works in Braille as well, in colour.", 5000),
    ("q", "q", "That's the plugin: import molterm_visidata in ~/.visidatarc.", 4000),
]


def _drain(fd, decoder, longest, quiet=QUIET):
    """What the terminal printed until it went quiet."""
    out, started, last = [], time.monotonic(), time.monotonic()
    while time.monotonic() - started < longest:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            out.append(decoder.decode(data))
            last = time.monotonic()
        elif out and time.monotonic() - last > quiet:
            break
    return "".join(out)


def record(steps=STEPS, cols=COLS, rows=ROWS, csv=os.path.join(HERE, "drugs.csv"), env=None):
    """Run VisiData through the steps; return the frames."""
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        environment = {**os.environ, "TERM": "xterm-kitty", "COLUMNS": str(cols), "LINES": str(rows), "ESCDELAY": "25", **(env or {})}
        environment.pop("TMUX", None)
        os.execvpe("vd", ["vd", "--config", os.path.join(HERE, "visidatarc"), csv], environment)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    frames = []
    try:
        for keys, shown, caption, pause in steps:
            if keys:
                os.write(fd, keys.encode())
            # the first screen waits for VisiData to start; later ones for the server's drawings too
            output = _drain(fd, decoder, LONGEST)
            frames.append({"caption": caption, "key": shown, "output": output, "pause": pause})
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
    return frames


def main():
    started = time.time()
    frames = record()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    temporary = OUT + ".part"
    with open(temporary, "w") as f:
        json.dump({"cols": COLS, "rows": ROWS, "recorded": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "frames": frames}, f)
    os.replace(temporary, OUT)
    size = sum(len(frame["output"]) for frame in frames)
    print(f"recorded {len(frames)} frames, {size // 1024} KB, in {time.time() - started:.0f} s -> {os.path.normpath(OUT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
