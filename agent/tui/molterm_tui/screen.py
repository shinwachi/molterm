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


# Overlays: a line taken apart into its cells, changed, and put back together, so that something can be drawn over styled text.

SGR = re.compile(r"\x1b\[[0-9;]*m")


def cells(line):
    """A line as its cells, [style, text]: the style is the SGR escapes in force there (since the last reset), the text a character
    and the combining marks after it, or "" for the second column of a wide character. Escapes other than SGR are dropped."""
    out, state, i = [], "", 0
    while i < len(line):
        match = ANSI.match(line, i)
        if match:
            code = match.group()
            if SGR.fullmatch(code):
                state = "" if code in (RESET, "\x1b[m") else state + code
            i = match.end()
            continue
        char = line[i]
        i += 1
        w = char_width(char)
        if w == 0 and out:
            out[-1][1] += char
            continue
        out.append([state, char])
        if w == 2:
            out.append([state, ""])
    return out


def join(cells_):
    out, state = [], ""
    for cell_style, text in cells_:
        if cell_style != state:
            out.append(RESET + cell_style)
            state = cell_style
        out.append(text)
    return "".join(out) + (RESET if state else "")


def paint(line, start, end, code):
    """The line with columns start to end (not included) given `code` too, such as a background."""
    parts = cells(line)
    parts += [["", " "] for _ in range(end - len(parts))]
    for cell in parts[max(start, 0):end]:
        cell[0] += code
    return join(parts)


def overlay(line, col, text):
    """The line with `text` (escapes allowed) written over it from column `col`."""
    parts, new = cells(line), cells(text)
    parts += [["", " "] for _ in range(col + len(new) - len(parts))]
    parts[col:col + len(new)] = new
    return join(parts)
