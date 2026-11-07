# molterm agent

A Claude Code-like terminal interface for an agent that runs on the server, one agent per session. The interface is Python
(`tui/molterm_tui`), run in the browser by [Pyodide](https://pyodide.org/) and drawn in [restty](https://github.com/wiedymi/restty); the
server is Flask. The agent uses the interface as one of its tools: it can put a panel in the user's transcript, and ask the user a
question and wait for the answer. For now the agent is an emulation, a script that behaves like a model would, to show the pieces
working: it draws molecules with the molterm server and shows them as pictures (the Kitty graphics protocol, which restty draws), and
asks before showing their properties.

![Asked for a drug, the agent offers a choice, draws imatinib and shows its properties](../docs/images/molterm-agent.gif)

**To write an agent of your own** and run it on the server or anywhere else, see [TUTORIAL.md](TUTORIAL.md): a base class (`sdk/tuiagent`,
the standard library only), an example agent, and one with Claude behind it.

## Run it

Docker only, from this folder, with the molterm dev server running (the agent draws with it):

```sh
make dev                 # build and start -> http://molterm-agent-dev.localhost, against the dev molterm server
make test                # the tests, in the container
make build up ENV=prod   # production -> http://molterm-agent.localhost, against the production molterm server
```

A real domain goes in a git-ignored `env/<env>.local.env`, as for the other services (see the main README). The image fetches Pyodide
0.28.3 (Python 3.13, as the server), restty and the DejaVu fonts from npm at build time, each checked against its sha256.

## How it fits together

```
 browser                                                      server (Flask, one process)
 ┌─────────────────────────────────────────────┐              ┌────────────────────────────────────────────┐
 │ restty ── keys ──> molterm_tui.App (Pyodide)│ ── POST ───> │ session ── inbox ──> agent thread          │
 │        <── screen ──┘   │   ▲               │   messages,  │   │                     │  server tools:     │
 │                         │   └── events ─────│ <─ SSE ───── │ event log <── emits ────┤  molterm.render,   │
 │          tui.show, tui.ask answers ─────────│ ── POST ───> │ tool results ──> wakes ─┘  molterm.properties│
 └─────────────────────────────────────────────┘              └────────────────────────────────────────────┘
```

- `POST /api/v1/sessions` makes a session and starts its agent in a thread. Sessions live in memory (gunicorn runs one worker with
  threads), end after 30 minutes unused, and at most 200 are open.
- `GET /api/v1/sessions/<id>/events` streams the agent's events as Server-Sent Events: `session`, `turn_start`, `status`, `text`
  (streamed a word at a time), `text_end`, `tool_use`, `tool_result`, `error`, `turn_end`. Each has a sequence number, so a page that
  loses the stream picks up where it left off.
- `POST .../messages` (`{"text", "cols"}`) queues what the user typed; `POST .../interrupt` stops the agent's turn (Esc).
- `POST .../tool_results` (`{"id", "result"}`) answers a TUI tool. The agent waits for it, up to 10 minutes.

The TUI tools, which run in the browser:

| tool | input | result |
|---|---|---|
| `tui.show` | `title`, and `image` (`png` as base64, `cols`, `rows`) and/or `body` (text, ANSI colours allowed); `smiles` makes the picture that molecule, which the user can edit | `{"shown": true, "lines"}`, with `"mol"`, its number, for a molecule |
| `tui.ask` | `question`, `options` | `{"choice", "index"}`, or `{"choice": null, "skipped": true}` on Esc |
| `tui.mark` | `mol` and `atoms` (indices in its SMILES) or `smarts`; or `text`. Optional `highlight` (yellow, green, blue, pink), `note`, `clear` | for a molecule `{"marked", "smiles"}`; for text `{"ok"}`; `{"error"}` if there is no such molecule or text |

Tools whose names don't start with `tui.` are the agent's own, and the page only shows their result. `GET /api/v1/tools` lists the
demo agent's.

The server runs `EmulatedAgent` for each session, or the class `AGENT` names (`module:Class`). An agent that runs elsewhere asks for a
session of its own (`POST /api/v1/agents/sessions`, which returns a token) and reaches it through `/api/v1/sessions/<id>/agent/...`;
the user opens `/?session=<id>`. The tutorial lists those routes.

A picture is sent to the terminal once, as a Kitty virtual placement, and its cells are printed as Unicode placeholders (U+10EEEE with
combining marks for row and column, in a colour that names the picture). They are ordinary text in the TUI's lines, so the picture
scrolls, crops and redraws with the transcript although the TUI repaints the whole screen each frame. `molterm.render` draws with
`/api/v1/render.png`, fitted to the terminal's colours (`Molterm.render` still draws coloured Braille, for a terminal without Kitty graphics).

| path | job |
|---|---|
| `tui/molterm_tui/app.py` | the interface: transcript, tool calls, spinner, input box, questions, molecules and their editing, marks and bubbles; keys and events in, frames out |
| `tui/molterm_tui/keys.py`, `screen.py` | the terminal's key sequences and mouse reports; measuring, wrapping and styling text, and drawing over styled text (bubbles, highlights) |
| `tui/molterm_tui/kitty.py` | Kitty pictures: sending one, and its placeholder lines |
| `server/app.py` | Flask: the page, the TUI's files for Pyodide, sessions and their streams, and the way through to molterm's editor |
| `server/sessions.py` | a session: event log, inbox, tool results, interrupts |
| `server/agent.py` | the emulated agent and its molterm tools |
| `server/static/page.js` | connects restty, Pyodide, the TUI and the server |
| `sdk/tuiagent/` | `TuiAgent`, the base for agents, and `RemoteSession`, a session reached over HTTP |
| `examples/` | `hello_agent.py` (no dependencies) and `claude_agent.py` (Claude chooses the TUI tools) |

### Molecules, edited where they are shown

A picture shown with its `smiles` is a molecule the TUI keeps: numbered (`mol 1` beside its title), and placed, as the TUI knows which
rows and columns each one covers on the screen. Clicking one (the TUI turns on the terminal's mouse reporting), or `/edit N` (`/edit`
alone takes the last), opens molterm's editor on it, inline: the drawing takes the picture's place in the transcript, at its scale, and a strip of
tools (selection, eraser, text, bonds, charges, rings) floats over the text above it, with one line for the status and the Done,
Cancel and tools buttons. F2 or the tools button puts the tools away, for someone who knows the keys, and the TUI remembers that for
the next edit. The line under the drawing resizes it: drag it (down and across), or Alt with the arrows. The transcript makes room,
what is below moving down, and the molecule stays where it is. The ChemDraw keys work on the atom or bond under the pointer, and the right button's menus have the rest, annotations
included: highlights and notes. `/edit N box`, or `/editor box` for every click, opens the
compact editor in a box over the picture instead.

**Done** (Ctrl-Q) redraws the molecule as edited (`mol 1 · edited`), in the place of the drawing, and the edit waits above the
input as a context (see below); put into a message, it carries `⟦mol 1 Imatinib, edited: <CXSMILES>⟧`, where the CXSMILES has the
annotations. The demo agent finds it (`server/agent.py`, `_EDITED`), says what was noted, and computes the edited molecule's
properties. **Cancel** (Ctrl-C) leaves everything as it was.

The editor runs on the molterm server (`POST /api/v1/edit` with `inline` and `at`, or `compact=1` and a `box`), as it does for
`molterm tui`. Inline, the screen beyond the drawing and the strip stays the TUI's: the editor's output asks for it to be drawn again
where a menu closed over it, or the drawing changed size (`ESC ] 5523;repaint`), which the TUI does from its transcript as it is
then, laid out around the drawing; the TUI's own frames wait meanwhile. Resizing sends the editor a step with the new `inline`
place and no input, and molterm keeps what was at the drawing's top left corner there. The page can't
reach molterm, so the agent server passes the steps on: `POST /api/v1/sessions/<id>/edit` (the editor's fields, as JSON; its answer as
text). The TUI sends one step at a time, holding what is typed or clicked meanwhile for the next. For the terminal to take the TUI's
mouse modes, the page is restty's PTY: what the TUI writes comes out of a transport the page makes (`pty` in `page.js`), and the
terminal's input, keys and mouse reports alike, goes back into the TUI.

### Pointing things out

A molecule's annotations, the user's from the editor or the agent's from `tui.mark`, are drawn by molterm (`POST /api/v1/callouts`,
through `POST /api/v1/sessions/<id>/picture`): the highlights in the picture, and, for each note, a leader from its atom to the
picture's right edge, ending in the middle of a row. The TUI carries the leader on in text to a speech bubble with the note in it,
beside the picture, turning down or up when bubbles would overlap. Bubbles and highlights are drawn over the transcript each frame, so
they scroll with it. A bubble can be moved out of the way: drag it (its leader follows, turning as it must), fold it with its `–`
to a ◆ at the leader's end (a click opens it again), or hide it with its `×`. `/notes` shows them all again, `/notes hide` folds them
all, and `/notes reset` puts them back where they were.

Text can be pointed at too. The agent's `tui.mark` with `text` highlights those words where they last appear (the user's own words
or the agent's), with a note in a bubble hanging under them. The user drags across text: the selection shows as it is dragged, then
turns blue, and waits above the input as a context; a click on the highlight takes it away.

### Contexts, above the input

What the user edited or highlighted waits as a chip at the top of the input box, put away (◇): it isn't sent until the user
clicks it, which puts its tag into the text at the cursor (◆, and the tag tinted in the text). Another click takes it out again,
and `×` drops it. Backspace removes a tag whole. In the text an edit's tag is short, `⟦mol 1 Imatinib, edited #1⟧`, and the message
carries it in full; a highlight's is `⟦highlighted: "the words"⟧`. Once sent, a context leaves the chips; a newer edit of the same
molecule replaces the older one. The demo agent shows
both: it marks a part of the drug it draws, and highlights its own words about dragging, and when the user sends a highlight it says
what they pointed at and marks it as seen.

To plug in a real model, replace `EmulatedAgent` with one that sends the transcript and `TOOLS` to the model, and turns its tool calls
into the same `use_tui` and `use_server_tool` calls: the events, the page and the TUI stay as they are.

## Status

Verified (2026-11-07): `make test`, 48 tests (the TUI driven by keys and events, its input kept at the bottom of the screen; a molecule
kept, placed, clicked, edited inline or in a box, and put into the input on Done, or left on Cancel; the inline editor's repaints and
its tools put away and remembered; the agent's marks on a molecule, with their bubbles beside it, and on text, with a bubble under it;
a drag that highlights text and leaves it as a context; contexts put into the text, taken out, dropped and sent in full;
bubbles folded, opened, dragged, hidden and shown again; the inline drawing resized by keys and by its line, the transcript making
room and closing up after; the server's sessions, stream, tool answers,
interrupts, the editor's way through to molterm, and the agent's script, its review of an edited molecule included, with molterm stubbed; an agent elsewhere driving a session over a real HTTP port, the token checks, `AGENT`, and
the Claude example's tool loop and interrupt handling with a stand-in client). In Chrome: the page starts Pyodide and a session, the greeting streams in, "show me a
drug" brings up the agent's question in place of the input, the chosen drug is drawn by molterm on the server and shown in the
transcript as a Kitty picture (it stays put as later frames redraw, and a second one beside it), and the properties come as a second
panel after a yes. The terminal refits its grid when its box or the zoom changes: a narrower box, and a zoom simulated by overriding
`devicePixelRatio` (1.19 to 2.6 and back), each fill the box afterwards. `examples/hello_agent.py`, run in a container of its own and
joined at `/?session=<id>`, counted words, showed a text panel, asked for a colour and showed its swatch as a picture; the same class
run by the server (`AGENT=examples.hello_agent:HelloAgent`) did the same. In headless Chromium against the dev services: clicking a
drawn molecule opens the compact editor in place; the hexagon from its strip and a click add a ring; `n` over the oxygen makes it a
nitrogen; the right button's menu opens inside the box; Done redraws the molecule and puts `⟦mol 1 CCO, edited: CCN⟧` into the input, and
sending it brings the edited molecule's properties. Then (2026-11-07), with imatinib: the agent's mark highlights the
N-methylpiperazine in the picture, with its note in a bubble at the end of a leader, and its words about dragging get a highlight and
a bubble under them; a click on the picture edits it inline, the drawing over the picture at about its place and size, the strip
above it over the transcript; F2 leaves only its line and brings the transcript back under it; the right button's menu spreads over
the transcript and Escape restores it; `n` over the carbonyl oxygen and Done redraw imatinib as edited, its highlight and bubble kept,
and put its tag into the input. A drag across "methylpiperazine" showed the selection, turned it blue and put it into the input, and
sending it made the agent mark it as seen. Then (2026-11-07, later): a bubble folded to ◆ and back with `/notes`, and dragged down and
right with its leader turning to follow; a highlight dragged across text waited as a chip above the input, and a click put its tag
into the text; while editing inline, Alt+↓ twice and a drag of the line under the drawing made it larger, the text below moving down
and the molecule staying put, and Done put the picture back in place with the transcript closed up.

## Open issues

- The demo agent is a script, not a model. `examples/claude_agent.py` is tested only with a stand-in for the API client: it has not
  run against the API here (there is no key in this environment).
- Sessions are in one process's memory: a restart ends them, and the service can't run more than one worker.
- A session link is its only key on the user's side: anyone with `/?session=<id>` can read and type in it. An agent elsewhere that
  keeps polling keeps its session open past the 30 minutes.
- While the mouse is reported to the TUI, the terminal doesn't select text with it; a drag highlights instead, and there is no copy.
- Inline, the drawing gets at least 8 rows and 40 columns, so around a small molecule it hides some of the transcript until Done;
  the box editor covers more. The drawing matches the picture's place and scale closely, not exactly (the editor lays atoms out
  itself, and draws them its own way). Resizing the window while editing cancels the edit.
- While an inline edit is open the TUI draws nothing of its own except when the editor asks: the agent's events show at the next
  repaint, or on Done or Cancel.
- A note's leader is a straight line to the picture's right edge, and may cross other bonds. Bubbles need about 37 columns beside
  the picture, which is drawn narrower to leave them; in a narrower window the notes aren't shown (the highlights still are).
- A text highlight is found by its words, so the same words elsewhere in that block are highlighted too. Highlights are of the
  user's, the agent's and notes' text, not of tool lines or panels.
- An inline drawing's size is the user's for that edit only; the next edit starts from the picture's size again.
- A bubble's place is kept relative to where it would be, so it moves with its molecule or text; it isn't saved anywhere, and a
  /clear or a reload forgets it, as it forgets the contexts.
- A molecule's tag (`mol N`) is the TUI's own: a model sees it only in the context of a message, not in its own tool calls.
- DejaVu Sans Mono has no ⏺ or ⎿, so the transcript uses ● and └ for Claude Code's marks.
- Pyodide takes a few seconds to load on the first visit (about 12 MB, cached after).
