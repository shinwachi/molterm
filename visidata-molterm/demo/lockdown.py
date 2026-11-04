"""VisiData for strangers on the web: only commands that look at, sort, select and reshape the sheet in memory.

VisiData can run Python, shell commands and an editor, and open and save files, all of which would hand a visitor the server. So this
keeps an allowlist of commands and removes every other one, from the key bindings, the menus and the command palette alike, and makes
expressions, the editor and the system clipboard fail even if some command not thought of reaches them. The container it runs in is
locked down as well (see docker-compose.yml): this is the first layer, not the only one.

Imported last from demo/live.visidatarc, after the plugin.
"""
import os
import re
import subprocess

from visidata import BaseSheet, Column, Sheet, VisiData, vd

import molterm_visidata

ALLOWED_PREFIXES = (
    "go-", "scroll-", "slide-", "jump-", "sort-", "select-", "unselect-", "stoggle-", "search-", "searchr-", "hide-", "unhide-",
    "resize-", "type-", "key-", "freq-", "describe-", "columns-", "plot-", "zoom", "menu-", "help-", "splitwin-", "cancel-",
    "aggregate-", "dup-", "delete-", "copy-", "cut-", "expand-", "contract-", "rename-", "setcol-precision-", "error",
)
ALLOWED = {
    "addcol-molecule", "addcol-molprops", "open-molecule", "toggle-molterm-mode", "edit-molecule",
    "addcol-new", "addcol-incr", "addcol-incr-step", "addcol-split", "addcol-capture", "addcol-subst", "addcol-regex-subst",
    "addcol-aggregate", "addcol-rank-sheet", "addcol-histogram",
    "setcol-input", "setcol-fill", "setcol-incr", "setcol-subst", "setcol-subst-all", "setcol-regex-subst", "setcol-regex-subst-all",
    "edit-cell", "add-row", "add-rows", "dedupe-rows", "random-rows", "paste-after", "paste-before", "paste-cell",
    "undo-last", "redo-last", "repeat-last", "pivot", "melt", "melt-regex", "transpose", "unfurl-col", "split-col", "freeze-col",
    "open-row", "open-row-basic", "open-rows", "dive-cursor", "dive-selected", "sheets-stack", "threads-sheet",
    "quit-sheet", "quit-sheet-free", "quit-all", "redraw", "reload-sheet", "mouse-enable", "mouse-disable", "no-op",
    "show-command-info", "show-cursor", "show-status", "show-version", "open-statuses", "sidebar-cycle", "sidebar-toggle",
    "toggle-cursor", "toggle-top-cursor", "toggle-multiline", "toggle-scrollfix", "toggle-all",
    "start-cursor", "end-cursor", "start-move", "end-move", "move-cursor", "move-canvas", "check-drag",
    "exec-longname", "exec-longname-simple",  # the command palette, which can only reach what's left
}
# never, whatever the lists above would let through
# sheets-all reaches VisiData's own sheets, options among them; the -ext plots hand off to matplotlib
REFUSED = re.compile(r"expr|python|shell|exec-(?!longname)|sys|save|open-file|option|macro|replay|cmdlog|plugin|suspend|"
                     r"pyobj|envvar|define|import|url|sql|s3-|kill|signal|term-|process|editor|config|memo|repl|palette|"
                     r"sheets-all|-ext$")

REFUSAL = "Not in this online demo, which only looks at the sheet in memory."


def allowed(longname):
    if REFUSED.search(longname) and longname not in {"addcol-molecule", "addcol-molprops", "open-molecule"}:
        return False
    return longname in ALLOWED or longname.startswith(ALLOWED_PREFIXES)


def apply():
    """Remove every command not allowed, for every sheet type. Return how many were removed."""
    removed = 0
    for longname in list(vd.commands):
        if not allowed(longname):
            del vd.commands[longname]
            removed += 1
    return removed


def _refuse(*args, **kwargs):
    vd.fail(REFUSAL)


def _no_processes(*args, **kwargs):
    raise PermissionError(REFUSAL)


# the second line: what the removed commands would have reached. Expressions and the editor fail ...
BaseSheet.evalExpr = Sheet.evalExpr = Column.evalExpr = _refuse
# (not openPath: VisiData opens the library with it, and the commands that open other files are gone)
VisiData.launchEditor = VisiData.launchExternalEditor = VisiData.popen = _refuse
# ... and this process can't start another: no shell, clipboard tool, man page or editor, whatever asks for one
for _name in ("Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
    setattr(subprocess, _name, _no_processes)
for _name in ("system", "popen", "fork", "forkpty", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
              "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "posix_spawn", "posix_spawnp"):
    if hasattr(os, _name):
        setattr(os, _name, _no_processes)
# and the drawings come from the one server the demo was given, whatever a visitor finds to change
_SERVER = os.environ.get("MOLTERM_URL", "http://molterm.localhost")
molterm_visidata.server_url = lambda: _SERVER

REMOVED = apply()
