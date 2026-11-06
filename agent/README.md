# molterm agent

A Claude Code-like terminal interface for an agent that runs on the server, one agent per session. The interface is Python
(`tui/molterm_tui`), run in the browser by [Pyodide](https://pyodide.org/) and drawn in [restty](https://github.com/wiedymi/restty); the
server is Flask. The agent uses the interface as one of its tools: it can put a panel in the user's transcript, and ask the user a
question and wait for the answer. For now the agent is an emulation, a script that behaves like a model would, to show the pieces
working: it draws molecules with the molterm server and shows them, and asks before showing their properties.

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
| `tui.show` | `title`, `body` (text, ANSI colours allowed) | `{"shown": true, "lines"}` |
| `tui.ask` | `question`, `options` | `{"choice", "index"}`, or `{"choice": null, "skipped": true}` on Esc |

Tools whose names don't start with `tui.` run on the server, and the page only shows their result. `GET /api/v1/tools` lists them all.

| path | job |
|---|---|
| `tui/molterm_tui/app.py` | the interface: transcript, tool calls, spinner, input box, questions; keys and events in, frames out |
| `tui/molterm_tui/keys.py`, `screen.py` | the terminal's key sequences; measuring, wrapping and styling text |
| `server/app.py` | Flask: the page, the TUI's files for Pyodide, sessions and their streams |
| `server/sessions.py` | a session: event log, inbox, tool results, interrupts |
| `server/agent.py` | the emulated agent and its molterm tools |
| `server/static/page.js` | connects restty, Pyodide, the TUI and the server |

To plug in a real model, replace `EmulatedAgent` with one that sends the transcript and `TOOLS` to the model, and turns its tool calls
into the same `use_tui` and `use_server_tool` calls: the events, the page and the TUI stay as they are.

## Status

Verified (2026-11-05): `make test`, 24 tests (the TUI driven by keys and events; the server's sessions, stream, tool answers, interrupts
and the agent's script, with molterm stubbed). In Chrome: the page starts Pyodide and a session, the greeting streams in, "show me a
drug" brings up the agent's question in place of the input, the chosen drug is drawn by molterm on the server and shown in the
transcript in coloured Braille, and the properties come as a second panel after a yes.

## Open issues

- The agent is a script, not a model.
- Sessions are in one process's memory: a restart ends them, and the service can't run more than one worker.
- DejaVu Sans Mono has no ⏺ or ⎿, so the transcript uses ● and └ for Claude Code's marks.
- Pyodide takes a few seconds to load on the first visit (about 12 MB, cached after).
