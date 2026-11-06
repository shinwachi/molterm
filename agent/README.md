# molterm agent

A Claude Code-like terminal interface for an agent that runs on the server, one agent per session. The interface is Python
(`tui/molterm_tui`), run in the browser by [Pyodide](https://pyodide.org/) and drawn in [restty](https://github.com/wiedymi/restty); the
server is Flask. The agent uses the interface as one of its tools: it can put a panel in the user's transcript, and ask the user a
question and wait for the answer. For now the agent is an emulation, a script that behaves like a model would, to show the pieces
working: it draws molecules with the molterm server and shows them as pictures (the Kitty graphics protocol, which restty draws), and
asks before showing their properties.

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
| `tui.show` | `title`, and `image` (`png` as base64, `cols`, `rows`) and/or `body` (text, ANSI colours allowed) | `{"shown": true, "lines"}` |
| `tui.ask` | `question`, `options` | `{"choice", "index"}`, or `{"choice": null, "skipped": true}` on Esc |

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
| `tui/molterm_tui/app.py` | the interface: transcript, tool calls, spinner, input box, questions; keys and events in, frames out |
| `tui/molterm_tui/keys.py`, `screen.py` | the terminal's key sequences; measuring, wrapping and styling text |
| `tui/molterm_tui/kitty.py` | Kitty pictures: sending one, and its placeholder lines |
| `server/app.py` | Flask: the page, the TUI's files for Pyodide, sessions and their streams |
| `server/sessions.py` | a session: event log, inbox, tool results, interrupts |
| `server/agent.py` | the emulated agent and its molterm tools |
| `server/static/page.js` | connects restty, Pyodide, the TUI and the server |
| `sdk/tuiagent/` | `TuiAgent`, the base for agents, and `RemoteSession`, a session reached over HTTP |
| `examples/` | `hello_agent.py` (no dependencies) and `claude_agent.py` (Claude chooses the TUI tools) |

To plug in a real model, replace `EmulatedAgent` with one that sends the transcript and `TOOLS` to the model, and turns its tool calls
into the same `use_tui` and `use_server_tool` calls: the events, the page and the TUI stay as they are.

## Status

Verified (2026-11-06): `make test`, 32 tests (the TUI driven by keys and events; the server's sessions, stream, tool answers, interrupts
and the agent's script, with molterm stubbed; an agent elsewhere driving a session over a real HTTP port, the token checks, `AGENT`, and
the Claude example's tool loop and interrupt handling with a stand-in client). In Chrome: the page starts Pyodide and a session, the greeting streams in, "show me a
drug" brings up the agent's question in place of the input, the chosen drug is drawn by molterm on the server and shown in the
transcript as a Kitty picture (it stays put as later frames redraw, and a second one beside it), and the properties come as a second
panel after a yes. The terminal refits its grid when its box or the zoom changes: a narrower box, and a zoom simulated by overriding
`devicePixelRatio` (1.19 to 2.6 and back), each fill the box afterwards. `examples/hello_agent.py`, run in a container of its own and
joined at `/?session=<id>`, counted words, showed a text panel, asked for a colour and showed its swatch as a picture; the same class
run by the server (`AGENT=examples.hello_agent:HelloAgent`) did the same.

## Open issues

- The demo agent is a script, not a model. `examples/claude_agent.py` is tested only with a stand-in for the API client: it has not
  run against the API here (there is no key in this environment).
- Sessions are in one process's memory: a restart ends them, and the service can't run more than one worker.
- A session link is its only key on the user's side: anyone with `/?session=<id>` can read and type in it. An agent elsewhere that
  keeps polling keeps its session open past the 30 minutes.
- DejaVu Sans Mono has no ⏺ or ⎿, so the transcript uses ● and └ for Claude Code's marks.
- Pyodide takes a few seconds to load on the first visit (about 12 MB, cached after).
