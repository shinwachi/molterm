# Tutorial: an agent that talks to users through the TUI

The TUI in the browser is a display that an agent drives. The agent writes text into the transcript, shows panels of text and
pictures, and asks the user to choose between options. The user types back. The agent can run in two places:

- **on the server**, in the Flask process, one thread per session: nothing to deploy beyond this service;
- **anywhere else**, such as your laptop, a notebook or another server. It reaches its session over HTTP with a token, and the user opens
  a link to that session.

Both run the same agent class. This tutorial builds a small one (`examples/hello_agent.py`), runs it both ways, and then puts Claude
behind it (`examples/claude_agent.py`).

```
 user's browser                      molterm agent server                    the agent
 ┌──────────────────────┐   SSE      ┌──────────────────────┐   in-process   ┌──────────────────────────┐
 │ TUI (Python, Pyodide)│ <───────── │ session: event log,  │ <────────────> │ TuiAgent subclass        │
 │ in restty            │ ─────────> │ inbox, tool answers  │  or HTTP+token │ greet(), respond(text)   │
 └──────────────────────┘ messages,  └──────────────────────┘                └──────────────────────────┘
                          answers
```

## 1. Write the agent

Subclass `TuiAgent` (in `sdk/tuiagent`, standard library only) and write two methods. `greet()` is the first turn. `respond(text,
cols)` handles each message the user sends, and `cols` is the transcript's width. Inside them:

| call | what the user sees | returns |
|---|---|---|
| `self.say(text)` | text streamed into the transcript, a word at a time | |
| `self.show(title, body=..., image=..., smiles=...)` | a panel: text (ANSI colours allowed) and/or a picture; with `smiles`, a molecule the user can edit | `{"shown": true, "lines"}`, and `"mol"`, its number, for a molecule |
| `self.mark(mol=N, atoms=[...] or smarts=..., highlight=, note=)` | the molecule's atoms highlighted in its picture, and the note in a speech bubble beside it, with a leader to the first atom | `{"marked": [atoms], "smiles"}` |
| `self.mark(text=..., highlight=, note=)` | those words highlighted where they last appear in the transcript, the note in a bubble under them | `{"ok"}`, or `{"error"}` if they aren't there |
| `self.ask(question, options)` | the input box turns into a choice; ↑↓/enter or a number picks, Esc skips | `{"choice", "index"}` or `{"choice": None, "skipped": True}` |
| `self.use_server_tool(name, input, run, label, summary=)` | a tool line, a spinner while `run()` works, then `summary(output)` under it | `run()`'s output, or `None` if it raised |

The trimmed version of `examples/hello_agent.py` below runs on its own:

```python
from tuiagent import TuiAgent

class HelloAgent(TuiAgent):
    def greet(self):
        self.say("Hi! Type anything and I'll count its words, or type \"colour\".")

    def respond(self, text, cols):
        if "colour" in text.lower():
            answer = self.ask("Which colour shall I paint?", ["Coral", "Teal"])
            if not answer.get("skipped"):
                self.show(answer["choice"], image=(png_bytes(answer["choice"]), 24, 3))  # a PNG, 24 by 3 cells
            return
        words = self.use_server_tool("text.count", {"text": text}, lambda: len(text.split()), "Counting",
                                     summary=lambda n: f"{n} words")
        self.show("Your message", body=f"\x1b[1m{text}\x1b[0m\n{words} words")
```

A picture is a PNG plus the cells it covers, given as `(png_bytes, cols, rows)` or `{"png": base64, "cols", "rows"}`. A cell is about
twice as tall as it is wide. The TUI shows the picture with the Kitty graphics protocol, and the picture scrolls with the transcript.
`png_bytes` above is your own code; the full example builds a gradient with `zlib`.

Give the picture of a molecule its `smiles` and the user can edit and annotate it where it is: a click on it (or `/edit N`) opens
molterm's editor in place, the drawing where the picture was. On Done the edit waits above the input; when the user clicks it into
their message, the message carries `⟦mol N name, edited: <CXSMILES>⟧`, notes and highlights included (the input box shows it short,
as `⟦mol N name, edited #k⟧`). Your
`respond` finds it in `text` (`molterm_tui.app.CONTEXT` is the pattern); the demo agent's `review` in `server/agent.py` shows one way
to answer.

Either of you can point things out. You, with `self.mark`: atoms of a molecule you showed (indices in its SMILES, from 0, or a SMARTS),
highlighted in yellow, green, blue or pink, and a note in a speech bubble beside the picture with a leader to the atom; or words in
the transcript, yours or the user's. `clear=True` removes what you marked first. The user, by dragging across text: it turns blue,
and when they click it into a message, the message carries `⟦highlighted: "the words"⟧` (`molterm_tui.app.QUOTED`). Their notes and highlights on a molecule come
with its edit, in the CXSMILES.

When the user presses Esc, the turn ends: whatever the agent was waiting on returns, and the agent's next `say`, `show` or `ask`
raises `Interrupted`, which `TuiAgent.turn` catches. Clean up in a `finally` if you hold something open. An error you don't catch is
shown to the user, and the session carries on.

## 2a. Run it on the server

Point `AGENT` at the class as `module:Class`. The module is imported with `/app` (this folder) and `sdk/` on the path:

```sh
AGENT=examples.hello_agent:HelloAgent make dev      # or put AGENT=... in env/dev.local.env
```

Open the service (http://molterm-agent-dev.localhost). Each visit gets a session, with an agent made as `HelloAgent(session)`.
`/health` reports which agent is running. With `AGENT` unset, the server runs the molterm demo agent (`server/agent.py`).

This suits agents that belong with the service: they share its process, so they need no deployment of their own. But a crash or a
slow call in the agent counts against the server's 64 threads.

## 2b. Run it anywhere else

```sh
python examples/hello_agent.py http://molterm-agent-dev.localhost
# Open http://molterm-agent-dev.localhost/?session=61eb9b694eeebac4 to talk to the agent. Ctrl+C here ends it.
```

Here's what happens when you run it:
1. `RemoteSession.open` asks the server for a session with no agent of its own: `POST /api/v1/agents/sessions` returns an id and a token.
2. The script prints a link; the page at `/?session=<id>` joins that session instead of starting one.
3. The agent runs in your process, and each `say`, `show` and `ask` becomes an HTTP call carrying the token. The user's messages and
   answers come back by long polling.

To write your own runner, use the pieces directly:

```python
from tuiagent import RemoteSession
session = RemoteSession.open("http://molterm-agent-dev.localhost")
print(session.url)          # send this to the user
HelloAgent(session)()        # runs until the session ends or you stop it
```

If your process reaches the server by another name than the browser does, pass both. In this repo, that means a container on the
`traefik` network, since nothing runs on the host:

```sh
docker run --rm -it --network traefik -v "$PWD":/w:ro python:3.13-slim \
    python /w/examples/hello_agent.py http://molterm-agent-dev-molterm-agent-1:8950 --public http://molterm-agent-dev.localhost
```

Anyone with the link can use the session, so share it like a meeting link. Only the token can speak as the agent. The session ends
after 30 minutes with neither side active, or when the server restarts.

### From another language

The client is a thin layer over six routes, with the token sent as `Authorization: Bearer <token>`:

| route | |
|---|---|
| `POST /api/v1/agents/sessions` | a session for your agent → `{"id", "token", "url"}` |
| `POST /api/v1/sessions/<id>/agent/events` | `{"type", ...}`: `turn_start`, `text` (`delta`), `text_end`, `tool_use` (`id`, `name`, `input`), `tool_result` (`id`, `output`, `is_error`), `status` (`label`), `error` (`message`), `turn_end` |
| `GET /api/v1/sessions/<id>/agent/messages?timeout=25` | the user's next message → `{"message": {"text", "cols"} or null, "interrupted", "closed"}` |
| `GET /api/v1/sessions/<id>/agent/tool_results/<tool id>?timeout=25` | the TUI's answer to a `tui.*` tool → `{"result" or null, "interrupted", "closed"}` |
| `GET /api/v1/sessions/<id>/agent` | `{"interrupted", "closed"}` |

A turn is `turn_start`, then your events, then `turn_end`. To call a TUI tool, send a `tool_use` event named `tui.show`, `tui.ask` or `tui.mark`
with an `id` of your choosing, then poll for that id's result. A tool with any other name is yours to run: send `tool_use` when it
starts and `tool_result` when it ends, and the user sees it run.

## 3. Put a model behind it

`examples/claude_agent.py` is the same class shape, but Claude decides what to do. The TUI's tools are offered to the model under
names it accepts (`show_panel`, `ask_user` and `point_out`, since tool names can't contain dots). Each `respond` runs the usual tool-use loop:

```python
response = self.client.beta.messages.create(model="claude-opus-5-5", max_tokens=16000, system=SYSTEM, tools=TOOLS,
                                            messages=self.messages, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
self.messages.append({"role": "assistant", "content": response.content})
for block in response.content:            # text goes to the transcript
    if block.type == "text": self.say(block.text)
calls = [b for b in response.content if b.type == "tool_use"]
# each call runs in the TUI (self.show / self.ask), and the answers go back together as tool_result blocks
```

Two details matter here:
- **Esc mid-tool:** if the user presses Esc while a tool waits, the history ends with a `tool_use` that has no result. The next turn
  starts by answering it as interrupted (`unanswered()`), because the API rejects a tool call left open.
- **Refusals:** `fallbacks="default"` lets the API retry a request a safety classifier declined on a fallback model, inside the same
  call. A `refusal` stop reason means the whole chain declined, and the agent says so.

To run it:

```sh
# elsewhere: your machine, with a key (ANTHROPIC_API_KEY, or an `ant auth login` profile)
pip install anthropic
python examples/claude_agent.py http://molterm-agent-dev.localhost

# on the server: add `anthropic` to requirements.txt, ANTHROPIC_API_KEY to ../credentials_norepo.env, then
AGENT=examples.claude_agent:ClaudeAgent make dev
```

From here, add your own tools alongside the TUI's: give the model a `draw_molecule` tool that calls molterm, as `server/agent.py`
does, and show its picture with `self.show(..., image=...)`.

## Where the code is

| path | |
|---|---|
| `sdk/tuiagent/base.py` | `TuiAgent`: the turn loop, `say`, `show`, `ask`, `mark`, `use_server_tool`, interrupts |
| `sdk/tuiagent/remote.py` | `RemoteSession` and `run`: a session over HTTP |
| `examples/hello_agent.py` | the example agent, without dependencies |
| `examples/claude_agent.py` | the same shape, with Claude choosing the tools |
| `server/sessions.py`, `server/app.py` | sessions, and the routes above |
| `tests/test_sdk.py` | both ways of running, over a real HTTP port; the Claude example with a stand-in client |
