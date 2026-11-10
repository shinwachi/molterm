"""A small agent that uses the TUI as its display: it streams text, asks the user a question, and shows panels of text and pictures.

On the server:  AGENT=examples.hello_agent:HelloAgent make dev
Elsewhere:      python examples/hello_agent.py http://molterm-agent.localhost

The standard library only (with sdk/ on the path), so it runs anywhere Python 3.9+ does. See TUTORIAL.md.
"""
import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk"))  # for running it as a script; harmless otherwise

from tuiagent import TuiAgent, run  # noqa: E402

COLOURS = {"Coral": (255, 127, 80), "Teal": (0, 128, 128), "Gold": (255, 200, 40), "Lavender": (181, 126, 220)}


def swatch(rgb, width=192, height=48):
    """A PNG of a colour fading to black, made with zlib alone: what a picture for tui.show looks like."""
    rows = b"".join(b"\x00" + bytes(int(c * (1 - x / width)) for x in range(width) for c in rgb) for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8 bits, RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


class HelloAgent(TuiAgent):
    def greet(self):
        self.say("Hi! I'm a small example agent. Type anything and I'll count its words, or type \"colour\" and I'll paint you a "
                 "swatch.")

    def respond(self, text, cols):
        if "colour" in text.lower() or "color" in text.lower():
            self.paint()
            return
        # a tool of the agent's own: the user sees it run, and its result
        words = self.use_server_tool("text.count", {"text": text}, lambda: len(text.split()), "Counting",
                                     summary=lambda n: f"{n} word{'s' * (n != 1)}")
        # a panel of text, with ANSI colours
        self.show("Your message", body=f"\x1b[1m{text}\x1b[0m\n\x1b[2m{words} words, {len(text)} characters\x1b[0m")
        self.say("Shown above. Type \"colour\" for a picture.")

    def paint(self):
        # a question: the input box turns into a choice until the user answers (or presses Esc)
        answer = self.ask("Which colour shall I paint?", COLOURS)
        if answer.get("skipped"):
            self.say("No colour, then.")
            return
        rgb = COLOURS[answer["choice"]]
        # a picture: a PNG and the cells it covers (a cell is about twice as tall as wide)
        self.show(answer["choice"], body="#%02x%02x%02x" % rgb, image=(swatch(rgb), 24, 3))
        self.say(f"{answer['choice']}, fading to black.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the example agent against a molterm agent server.")
    parser.add_argument("server", help="the server, as this machine reaches it, e.g. http://molterm-agent.localhost")
    parser.add_argument("--public", help="the server as your browser reaches it, if different")
    parser.add_argument("--tutor", action="store_true", help="tutorial mode: the server's tutor watches the session too")
    arguments = parser.parse_args()
    run(HelloAgent, arguments.server, arguments.public, tutor=arguments.tutor)
