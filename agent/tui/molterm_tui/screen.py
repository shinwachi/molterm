"""Measuring, wrapping and styling text for a terminal: the pieces the TUI draws its frames from."""
import re
import unicodedata

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

RESET = "\x1b[0m"
BOLD, DIM, ITALIC, INVERSE = "\x1b[1m", "\x1b[2m", "\x1b[3m", "\x1b[7m"
GREY = "\x1b[38;5;245m"
ACCENT = "\x1b[38;2;215;119;87m"  # Claude Code's warm orange, for the mark and the spinner
GREEN, RED, BLUE, CYAN = "\x1b[32m", "\x1b[31m", "\x1b[34m", "\x1b[36m"
USER_BG = "\x1b[48;5;236m"


def strip(text):
    return ANSI.sub("", text)


def char_width(char):
    if unicodedata.combining(char) or char in "​‍":
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def width(text):
    """Columns the text takes on screen, escape sequences not counted."""
    return sum(char_width(c) for c in strip(text))


def style(text, *codes):
    return "".join(codes) + text + RESET if codes else text


def wrap(text, cols):
    """Plain text (no escapes) into lines of at most `cols` columns, breaking at spaces where it can, and at newlines always."""
    cols = max(cols, 1)
    lines = []
    for paragraph in text.split("\n"):
        line, line_width = "", 0
        for word in re.split(r"(\s+)", paragraph):
            if not word:
                continue
            word_width = width(word)
            if line_width + word_width <= cols:
                line, line_width = line + word, line_width + word_width
                continue
            if word.isspace():  # a space at the end of a line is dropped
                lines.append(line.rstrip())
                line, line_width = "", 0
                continue
            if line:
                lines.append(line.rstrip())
                line, line_width = "", 0
            while width(word) > cols:  # a word longer than the line is cut
                head, head_width = "", 0
                for char in word:
                    if head_width + char_width(char) > cols:
                        break
                    head, head_width = head + char, head_width + char_width(char)
                lines.append(head)
                word = word[len(head):]
            line, line_width = word, width(word)
        lines.append(line.rstrip())
    return lines


def truncate(text, cols):
    """A line with escapes cut to `cols` columns, its styling closed if cut."""
    out, used, i = "", 0, 0
    while i < len(text):
        match = ANSI.match(text, i)
        if match:
            out += match.group()
            i = match.end()
            continue
        w = char_width(text[i])
        if used + w > cols:
            return out + RESET
        out, used, i = out + text[i], used + w, i + 1
    return out


def pad(text, cols):
    """A line padded with spaces to exactly `cols` columns (cut if longer)."""
    text = truncate(text, cols)
    return text + " " * max(cols - width(text), 0)
