"""An agent backed by Claude, which gets the TUI as tools: the model decides when to show a panel and when to ask the user something.

Needs the `anthropic` package and a key (ANTHROPIC_API_KEY, or an `ant auth login` profile).
On the server:  add anthropic to requirements.txt, the key to credentials_norepo.env, then AGENT=examples.claude_agent:ClaudeAgent
Elsewhere:      pip install anthropic; python examples/claude_agent.py http://molterm-agent.localhost

See TUTORIAL.md.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk"))  # for running it as a script; harmless otherwise

from tuiagent import TuiAgent, run  # noqa: E402

MODEL = "claude-opus-5-5"
SYSTEM = ("You are a helpful assistant that talks to the user in a terminal interface. Keep replies short. When something reads "
          "better as a block of its own (a table, a list, code, a summary), put it in a panel with show_panel. When you need the "
          "user to choose between a few options, use ask_user rather than asking in prose. To draw the user's eye to words "
          "already in the conversation, theirs or yours, highlight them with point_out.")
# The TUI's tools, as the model sees them. Tool names can't contain dots, so the model's show_panel is the TUI's tui.show.
TOOLS = [
    {"name": "show_panel", "description": "Show a panel in the user's terminal, under the conversation: a title and plain text.",
     "strict": True,
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["title", "body"],
                      "properties": {"title": {"type": "string"}, "body": {"type": "string"}}}},
    {"name": "ask_user", "description": "Ask the user to pick one of 2 to 6 options, and wait for their choice. They may skip it.",
     "strict": True,
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["question", "options"],
                      "properties": {"question": {"type": "string"},
                                     "options": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "point_out", "description": "Highlight words already in the conversation, where they last appear, with a short note in a "
                                         "speech bubble under them (an empty note for none).",
     "strict": True,
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["text", "note"],
                      "properties": {"text": {"type": "string"}, "note": {"type": "string"}}}},
]


class ClaudeAgent(TuiAgent):
    def __init__(self, session, client=None, pace=1.0):
        super().__init__(session, pace)
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.messages = []  # the whole conversation, sent each turn

    def greet(self):
        self.say("Hello! I'm Claude, talking to you through this terminal. Ask me anything.")

    def respond(self, text, cols):
        self.messages.append({"role": "user", "content": self.unanswered() + [{"type": "text", "text": text}]})
        while True:
            self.session.emit("status", label="Thinking")
            response = self.client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=self.messages,
                # if a safety classifier declines, the API retries on a fallback model in the same call
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            self.messages.append({"role": "assistant", "content": response.content})  # all of it, thinking blocks included
            if response.stop_reason == "refusal":
                self.say("I can't help with that one.")
                return
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    self.say(block.text)
            calls = [block for block in response.content if block.type == "tool_use"]
            if not calls:
                return
            # every call's result goes back together, in one user message
            self.messages.append({"role": "user", "content": [self.run_tool(call) for call in calls]})

    def unanswered(self):
        """Results for tool calls the last turn left open, because the user interrupted it (Esc): every tool_use needs its result."""
        if not self.messages or self.messages[-1]["role"] != "assistant":
            return []
        return [{"type": "tool_result", "tool_use_id": block.id, "content": "The user interrupted this.", "is_error": True}
                for block in self.messages[-1]["content"] if block.type == "tool_use"]

    def run_tool(self, call):
        if call.name == "show_panel":
            result = self.show(call.input["title"], body=call.input["body"])
        elif call.name == "ask_user":
            result = self.ask(call.input["question"], call.input["options"])
        elif call.name == "point_out":
            result = self.mark(text=call.input["text"], note=call.input["note"] or None)
        else:
            return {"type": "tool_result", "tool_use_id": call.id, "content": f"No such tool: {call.name}", "is_error": True}
        return {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Claude-backed agent against a molterm agent server.")
    parser.add_argument("server", help="the server, as this machine reaches it, e.g. http://molterm-agent.localhost")
    parser.add_argument("--public", help="the server as your browser reaches it, if different")
    parser.add_argument("--tutor", nargs="?", const=True, default=False, metavar="NAME",
                        help="tutorial mode: the server's tutor watches the session too, or the one of its TUTORS called NAME")
    arguments = parser.parse_args()
    run(ClaudeAgent, arguments.server, arguments.public, tutor=arguments.tutor)
