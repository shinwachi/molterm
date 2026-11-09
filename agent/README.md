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

`make agent-gif` in the main folder records the demos above from the running dev agent (`docs/screenshots/agent_gif.py` and
`agent_features.py`, in headless Chromium).

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

![Imatinib edited where it is shown: the drawing in the picture's place, a floating strip of tools, and the notes in bubbles beside it](../docs/images/molterm-agent-inline.png)

![A note added to the carbonyl oxygen appears in a bubble at once; Alt+↓ makes the drawing larger, the transcript making room; Done, and the edit clicked into a message brings the agent's review](../docs/images/molterm-agent-annotate.gif)

A picture shown with its `smiles` is a molecule the TUI keeps: numbered (`mol 1` beside its title), and placed, as the TUI knows which
rows and columns each one covers on the screen. Clicking one (the TUI turns on the terminal's mouse reporting), or `/edit N` (`/edit`
alone takes the last), opens molterm's editor on it, inline: the drawing takes the picture's place in the transcript, at its scale, and a strip of
tools (selection, eraser, text, bonds, charges, rings) floats over the text above it, with one line for the status and the Done,
Cancel and tools buttons. F2 or the tools button puts the tools away, for someone who knows the keys, and the TUI remembers that for
the next edit. The line under the drawing resizes it: drag it (down and across), or Alt with the arrows. The transcript makes room,
what is below moving down, and the molecule stays where it is. The ChemDraw keys work on the atom or bond under the pointer, and the right button's menus have the rest, annotations
included: highlights and notes. A note made there shows at once in a bubble beside the drawing, at the end of a leader from its atom
(drawn in the drawing by molterm, which leaves the note's text to the TUI), and moves with the drawing. On an atom or bond in a
highlighted group, the menu asks which the note is about: Note This Atom (or Bond)... or Note Highlighted Group..., whose leader
points at the middle of the group. `/edit N box`, or `/editor box` for every click, opens the
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

![The agent's marks: the N-methylpiperazine highlighted, its note in a bubble at the end of a leader, the agent's words highlighted with a note, and the user's highlight waiting above the input](../docs/images/molterm-agent-bubbles.png)

![A bubble dragged aside, its leader following; "N-methylpiperazine" dragged across, clicked into a message, and marked as seen by the agent](../docs/images/molterm-agent-point.gif)

A molecule's annotations, the user's from the editor or the agent's from `tui.mark`, are drawn by molterm (`POST /api/v1/callouts`,
through `POST /api/v1/sessions/<id>/picture`): the highlights in the picture, and, for each note, a leader from its atom to the
picture's right edge, ending in the middle of a row. The TUI carries the leader on in text to a speech bubble with the note in it,
beside the picture, turning down or up when bubbles would overlap. Whose a note is shows in its colour: the user's own (written
in the editor, and kept as theirs in the CXSMILES) in blue, the blue of the text they highlight, its leader, bubble and text alike;
the agent's in amber. Bubbles and highlights are drawn over the transcript each frame, so
they scroll with it. A bubble goes where it covers the least text near what it points at (beside a picture, in a blank line, beside a short line),
and where it can't help covering some, that text shows faintly in the bubble's margins and empty room, so the user knows there is
more under it. The line from its atom or text to the bubble is the cheapest path over the screen's cells: blank
cells cost least, then punctuation and lines, then letters and digits, with a little more for each turn, and it goes around other
bubbles and, if it can, around pictures and the drawing being edited; so it slips through the blank column beside a reply rather
than over its words. A bubble can be moved out of the way: drag it, and its line follows it anywhere; fold it with its `–`
to a ◆ at the leader's end (a click opens it again), or hide it with its `×`. `/notes` shows them all again, `/notes hide` folds them
all, and `/notes reset` puts them back where they were.

Text can be pointed at too. The agent's `tui.mark` with `text` highlights those words where they last appear (the user's own words
or the agent's), with a note in a bubble hanging under them. The user can select any of the transcript, dragging across it: the
agent's and their own words, a tool's line and its result, a panel's text, the banner (not its box). The selection shows as it is
dragged; let go, and it has handles at once, as a highlight's (see below), a knob above its first letter and one below its last,
and a menu under it asks what it is for (dragging a knob puts the menu away until it is let go): **Copy** puts it on the clipboard (a reply's
wrapped lines joined again, a tool's lines kept as lines, pictures left out), and the line under the input says how much went;
**Add a note…**
highlights it and asks for the note, which hangs in a bubble under it; **Highlight** highlights it in blue, its tint and its letters
both, as the user's notes are, and puts it above the input as a context. Any of the transcript can be highlighted this way, a
tool's lines and the banner too; the highlight's menu takes it away. Esc, or a press elsewhere, lets the selection go. Copying goes through the page (`bridge.copy`): the Clipboard API where the page is secure (https or localhost),
and otherwise, as over plain http, a hidden text area copied by the menu's click; a TUI with no `copy` in its bridge sends OSC 52
for the terminal instead. A drag takes exactly the letters it crosses; a double click takes the word under it, and a double
click held and dragged takes whole words (the selection darkens, and the line under the input says so). A drag may run over
several lines, paragraphs and replies: each reply gets its part of the highlight, and they go into the message, and away, as one.
(Before, a drag highlighted at once and only the agent's and the user's words could be selected; the rest of the transcript, most of
it, couldn't.)
A highlight is found again by its words, whatever the line breaks, blank lines and indents between them.

The right button (or Ctrl+click, a Mac's right click) opens a highlight's menu: change the selection, add (or edit) a note, which
hangs in a bubble under it, put it into the message, remove it, or remove all the user's highlights (with more than one, a question in the input's place
asks first: "Remove all N of your highlights?", keeping them the default). Changing the selection, for
the user's own, gives it handles, as iOS does: a knob above its first letter and one below its last, dragged to change what it
covers, neither going past the other, and snapping back to the first and last letters when let go; Enter or a click elsewhere keeps the change and Esc undoes it. A right click on
a bubble opens its menu: edit its note, or put it into the message as a context. A molecule's note, edited, goes to molterm
(`mark` with `edit`, which sets one atom's or bond's note) and becomes the user's. A note is written in a box in the input's place,
which, like a question, pulses if something else is clicked. The user's bubbles are blue with white letters and controls.

The input's cursor is the terminal's own, where typing goes: restty draws it solid while the terminal has the keyboard, the cue
that the user can type, and hollow when it hasn't. It is hidden while a question or an edit has the keys.

The keys are the terminal's only while it has the focus, and a click on the top bar, on the note under it, or in the frame's margin
takes them away. The TUI can't say so: it is drawing its own screen, and the one cue there is, that hollow cursor, is hidden exactly
then, because a TUI waiting for an answer hides the cursor. What is left is a page that has quietly stopped listening, which reads as
a lost session. So the page gives them back: every click on it is a click on the terminal, since nothing else there wants the
keyboard (a click that ends a selection is the user taking a copy, and is left alone). A host whose terminal shares the page with
other things that do want the keyboard cannot do that, and has to say where the keys went instead.

A question (`tui.ask`) takes the input's place and holds the keys and the mouse until it is answered: a click on an option answers
it, and a click anywhere else, or typing it has no use for, makes its box pulse twice (its border brightening, a warm tint inside),
and the line in the transcript where it was asked with it, to show where the answer goes; from the third click elsewhere on,
every other one also sends two large chevrons, solid blocks, sliding down the middle of the screen towards it as they fade in
and out; the wheel still scrolls. A tool that is running, the question's among them, has a dot that fades
out and in rather than blinking.

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

Verified (2026-11-07): `make test`, 51 tests (the TUI driven by keys and events, its input kept at the bottom of the screen; a molecule
kept, placed, clicked, edited inline or in a box, and put into the input on Done, or left on Cancel; the inline editor's repaints and
its tools put away and remembered; the agent's marks on a molecule, with their bubbles beside it, and on text, with a bubble under it;
a drag across any text, the agent's words, a tool's lines or the banner,
then copied (through the page or OSC 52) or added as a context; a drag that highlights text and leaves it as a context; contexts put into the text, taken out, dropped and sent in full;
bubbles folded, opened, dragged, hidden and shown again, placed clear of text, their lines routed to any side, and text under
them shown faintly, their lines routed through blank cells; notes made while editing inline, in bubbles at once; the inline drawing resized by keys and by its line, the transcript making
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
and the molecule staying put, and Done put the picture back in place with the transcript closed up. Then: the cursor shows as an outline
with the letter under it readable; the agent's note on its own words sits beside the short lines below them, covering nothing; a
molecule's bubble dragged below and left of the picture, and up over its title, kept its line (beside the picture, not through it),
and the text it covered showed faintly in its margins. Then: a note added to imatinib's carbonyl oxygen while editing inline
(right button, Note..., typed, Enter) appeared at once in a bubble beside the drawing, at the end of a leader from the oxygen, beside
the agent's note on the N-methylpiperazine; the agent's note on its own words hangs from a line that runs down the blank column
beside the tool lines below, not over them; the cursor in the input is solid while it has the keyboard. Then (2026-11-09), in
headless Chromium over plain http: a drag from the banner's session line to the tips under it showed the selection and, let go, the
menu; Copy fired the page's copy with "session: …\n Tips: type a message …" (no box sides or bottom), the keys went back to the
terminal, and the line under the input said how much was copied; Add it as context put the same text above the input as a chip.
Then: words on the tips line selected had handles at once and the menu (Copy, Add a note…, Highlight) under the lower knob; dragging
that knob on put the menu away and letting go brought it back under the new end; Add a note… highlighted them in blue, the note in a
blue bubble under them, and Highlight did the same without the note, the chip above the input both times.

## Open issues

- The demo agent is a script, not a model. `examples/claude_agent.py` is tested only with a stand-in for the API client: it has not
  run against the API here (there is no key in this environment).
- Sessions are in one process's memory: a restart ends them, and the service can't run more than one worker.
- A session link is its only key on the user's side: anyone with `/?session=<id>` can read and type in it. An agent elsewhere that
  keeps polling keeps its session open past the 30 minutes.
- While the mouse is reported to the TUI, the terminal doesn't select text with it: the TUI selects and copies itself. A highlight
  on a tool's line is found by its words in what the line shows, so it moves with them if the line is redrawn differently. Only what is
  in the transcript can be selected (not the input, a question or a bubble), and a selection can't run past the screen's edge.
- Inline, the drawing gets at least 8 rows and 40 columns, so around a small molecule it hides some of the transcript until Done;
  the box editor covers more. The drawing matches the picture's place and scale closely, not exactly (the editor lays atoms out
  itself, and draws them its own way). Resizing the window while editing cancels the edit.
- While an inline edit is open the TUI draws nothing of its own except when the editor asks: the agent's events show at the next
  repaint, or on Done or Cancel.
- A note's leader is a straight line to the picture's right edge, and may cross other bonds. A bubble dragged to the left of the
  picture, level with it, is joined by a line across the picture, which hides the cells it crosses. Bubbles need about 37 columns beside
  the picture, which is drawn narrower to leave them; in a narrower window the notes aren't shown (the highlights still are).
- A text highlight is found by its words, so the same words elsewhere in that block are highlighted too. Highlights are of the
  user's, the agent's and notes' text, not of tool lines or panels.
- An inline drawing's size is the user's for that edit only; the next edit starts from the picture's size again.
- A bubble's place is kept relative to where it would be, so it moves with its molecule or text; it isn't saved anywhere, and a
  /clear or a reload forgets it, as it forgets the contexts.
- A molecule's tag (`mol N`) is the TUI's own: a model sees it only in the context of a message, not in its own tool calls.
- DejaVu Sans Mono has no ⏺ or ⎿, so the transcript uses ● and └ for Claude Code's marks.
- Pyodide takes a few seconds to load on the first visit (about 12 MB, cached after).
