"""Turn what the terminal sends for keys into named keys and typed text.

restty hands the page each key as the bytes a terminal would send: printable text as itself, and the rest as control characters or
escape sequences. One delivery can hold several keys (a paste, or keys typed faster than they are read), so `parse` returns a list.
With mouse reporting on (SGR, 1006), a click or a wheel step comes as a "mouse" key: `mouse` is (button, col, row, pressed), the
column and row from 0, and `text` the report as it came, for passing on.
"""
from dataclasses import dataclass

_CSI = {
    "A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "shift-tab",
}
_TILDE = {"1": "home", "2": "insert", "3": "delete", "4": "end", "5": "pageup", "6": "pagedown", "7": "home", "8": "end"}
_CONTROL = {
    "\r": "enter", "\n": "enter", "\t": "tab", "\x7f": "backspace", "\x08": "backspace", "\x1b": "escape",
    "\x01": "ctrl-a", "\x03": "ctrl-c", "\x04": "ctrl-d", "\x05": "ctrl-e", "\x0b": "ctrl-k", "\x0c": "ctrl-l", "\x15": "ctrl-u",
    "\x17": "ctrl-w",
}


@dataclass(frozen=True)
class Key:
    name: str  # "text" for typed text, else the key's name, such as "enter" or "up"
    text: str = ""
    alt: bool = False
    mouse: tuple = ()


def parse(data):
    """Return the keys in `data`. Runs of printable characters come back as one "text" key; a bracketed paste as one "paste" key."""
    keys, i, text = [], 0, ""

    def flush():
        nonlocal text
        if text:
            keys.append(Key("text", text))
            text = ""

    while i < len(data):
        char = data[i]
        if data.startswith("\x1b[200~", i):  # bracketed paste: everything up to the end marker, as typed text
            flush()
            end = data.find("\x1b[201~", i)
            end = len(data) if end < 0 else end
            keys.append(Key("paste", data[i + 6:end].replace("\r\n", "\n").replace("\r", "\n")))
            i = end + 6
        elif char == "\x1b" and i + 1 < len(data) and data[i + 1] in "[O":
            flush()
            j = i + 2
            while j < len(data) and not ("@" <= data[j] <= "~"):
                j += 1
            params, final = data[i + 2:j], data[j] if j < len(data) else ""
            parts = params.split(";")
            if params.startswith("<") and final in ("M", "m") and len(parts) == 3:
                try:
                    button, col, row = (int(part) for part in params[1:].split(";"))
                    keys.append(Key("mouse", data[i:j + 1], mouse=(button, col - 1, row - 1, final == "M")))
                except ValueError:
                    pass
                i = j + 1
                continue
            alt = len(parts) > 1 and parts[1] in ("3", "4")
            if final == "~":
                keys.append(Key(_TILDE.get(parts[0], "unknown"), alt=alt))
            else:
                keys.append(Key(_CSI.get(final, "unknown"), alt=alt))
            i = j + 1
        elif char == "\x1b" and i + 1 < len(data) and data[i + 1] not in "\x1b":  # Alt with a key
            flush()
            following = data[i + 1]
            named = _CONTROL.get(following)
            keys.append(Key(named, alt=True) if named else Key("text", following, alt=True))
            i += 2
        elif char in _CONTROL:
            flush()
            keys.append(Key(_CONTROL[char]))
            i += 1
        elif ord(char) < 32:
            flush()
            i += 1  # another control character: nothing here uses it
        else:
            text += char
            i += 1
    flush()
    return keys
