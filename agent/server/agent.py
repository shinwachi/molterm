"""An emulated agent: a script that behaves like a language-model agent, to show the pieces working before a real model is plugged in.

It streams its replies word by word, uses a server-side tool (the molterm server, which draws molecules), and uses the user's TUI as
tools: `tui.show` puts a drawing in the transcript, and `tui.ask` asks the user a question and waits for the answer. A real agent would
make the same calls with the same events; only the deciding would differ.
"""
import base64
import math
import re

import requests
from tuiagent import TuiAgent

SAMPLES = {
    "Aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "Caffeine": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "Imatinib": "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1",
    "Penicillin G": "CC1(C)S[C@@H]2[C@H](NC(=O)Cc3ccccc3)C(=O)N2[C@H]1C(=O)O",
}
_SMILES = re.compile(r"^[A-Za-z0-9@+\-\[\]()=#$%/\\.:*]+$")
_ELEMENTS = re.compile(r"Cl|Br|[BCNOSPFI]|[cnosp]")
# the page's terminal colours, for molterm to fit its picture to, and the pixels a picture gets per column (a cell is twice as tall)
TERMINAL = {"background": "161615", "foreground": "f0efec"}
CELL_PIXELS = 16
PICTURE_ROWS = 16
# the tools this agent can call, as a real agent's tool list would describe them
TOOLS = [
    {"name": "molterm.render", "where": "server",
     "description": "Draw a molecule (SMILES): as a picture for the Kitty graphics protocol (mode kitty, the default), or as coloured "
                    "Braille text (mode braille)."},
    {"name": "molterm.properties", "where": "server", "description": "Compute a molecule's MW, cLogP, TPSA, H-bond donors and acceptors, QED."},
    *TuiAgent.TUI_TOOLS,
]


def looks_like_smiles(text):
    return " " not in text and bool(_SMILES.match(text)) and bool(_ELEMENTS.search(text)) and len(text) <= 500


class Molterm:
    """The molterm server, as the agent's server-side tools."""

    def __init__(self, url, timeout=20):
        self.url, self.timeout = url.rstrip("/"), timeout

    def picture(self, smiles, cols, rows=PICTURE_ROWS):
        """The drawing as a PNG fitted to the page's terminal: {"png": base64, "cols", "rows"}, the cells it takes."""
        response = requests.get(f"{self.url}/api/v1/render.png", params={"input": smiles, "cols": max(min(cols, 100), 20), "rows": rows,
                                                                          "cell_width": CELL_PIXELS, **TERMINAL}, timeout=self.timeout)
        if response.status_code != 200:
            raise ValueError(response.text.strip() or f"molterm answered {response.status_code}")
        png = response.content
        height = int.from_bytes(png[20:24], "big")  # the PNG's IHDR: width, then height
        return {"png": base64.b64encode(png).decode("ascii"), "cols": int(response.headers.get("X-Molterm-Cols", cols)),
                "rows": max(math.ceil(height / (2 * CELL_PIXELS)), 1)}

    def render(self, smiles, cols):
        """The drawing as coloured Braille text."""
        response = requests.get(f"{self.url}/api/v1/render.txt", params={"input": smiles, "color": "1", "mode": "braille",
                                                                          "cols": max(min(cols, 120), 20), "rows": 24}, timeout=self.timeout)
        if response.status_code != 200:
            raise ValueError(response.text.strip() or f"molterm answered {response.status_code}")
        return response.text.rstrip("\n")

    def properties(self, smiles):
        response = requests.post(f"{self.url}/api/v1/render", json={"input": smiles, "properties": True, "cols": 20, "rows": 8},
                                 timeout=self.timeout)
        body = response.json()
        if response.status_code != 200:
            raise ValueError(body.get("error", {}).get("message", f"molterm answered {response.status_code}"))
        return {"formula": body.get("formula"), **body["properties"]}


class EmulatedAgent(TuiAgent):
    """The demo agent: the TuiAgent base (sdk/tuiagent) does the talking; this decides what to say, as a model would."""

    def __init__(self, session, molterm, pace=1.0):
        super().__init__(session, pace)
        self.molterm = molterm

    def use_server_tool(self, name, input_, run, label="Working", summary=None, errors=(requests.RequestException, ValueError, KeyError)):
        return super().use_server_tool(name, input_, run, label, summary or (lambda output: self.summary(name, output)), errors)

    @staticmethod
    def summary(name, output):
        if name == "molterm.render" and isinstance(output, dict):
            return f"Drew it as a picture, {output['cols']} by {output['rows']} cells"
        if name == "molterm.render":
            lines = output.split("\n")
            return f"Drew it in {len(lines)} lines of Braille"
        if name == "molterm.properties":
            return f"{output['formula']}, MW {output['mw']:.1f}"
        return str(output)[:200]

    # -- the script -------------------------------------------------------------------------------------------------------------------

    def greet(self):
        self.say("Hello! I'm an emulated agent running on the server, in a session of my own. I can draw molecules with molterm "
                 "and show them to you here, and ask you things through this interface. Type a SMILES, or ask me to show you a drug.")

    def respond(self, text, cols):
        words = text.lower()
        if looks_like_smiles(text):
            self.draw(text, None, cols)
        elif any(w in words for w in ("drug", "molecule", "show", "draw", "example", "pick")):
            self.offer(cols)
        elif re.search(r"\b(hi|hello|hey)\b", words):
            self.say("Hello again! Type a SMILES, such as CCO for ethanol, or ask me to show you a drug.")
        else:
            self.say("I'm only an emulation, so I don't really understand that. What I can do is draw molecules.")
            answer = self.use_tui("tui.ask", {"question": "Shall I draw one for you?", "options": ["Yes, pick a drug", "No, thanks"]},
                                  "Waiting for you")
            if answer.get("index") == 0:
                self.offer(cols)
            else:
                self.say("All right. Type a SMILES whenever you like.")

    def offer(self, cols):
        self.say("Which one would you like to see?")
        answer = self.use_tui("tui.ask", {"question": "Which drug shall I draw?", "options": list(SAMPLES)}, "Waiting for you")
        if answer.get("choice") in SAMPLES:
            self.draw(SAMPLES[answer["choice"]], answer["choice"], cols)
        else:
            self.say("No drug, then. Type a SMILES whenever you like.")

    def draw(self, smiles, name, cols):
        self.say(f"I'll draw {name or 'that'} with molterm.")
        picture = self.use_server_tool("molterm.render", {"smiles": smiles, "mode": "kitty"}, lambda: self.molterm.picture(smiles, cols),
                                       "Drawing")
        if picture is None:
            self.say("molterm couldn't draw it. If that was a SMILES, check it for typos; otherwise the molterm server may be down.")
            return
        self.use_tui("tui.show", {"title": name or smiles, "image": picture}, "Showing")
        self.say("There it is, drawn by RDKit and shown with the Kitty graphics protocol: oxygen red, nitrogen blue.")
        answer = self.use_tui("tui.ask", {"question": f"Do you want the properties of {name or 'this molecule'} too?",
                                          "options": ["Yes", "No"]}, "Waiting for you")
        if answer.get("index") != 0:
            self.say("Fine. Type another SMILES, or ask me for a drug.")
            return
        values = self.use_server_tool("molterm.properties", {"smiles": smiles}, lambda: self.molterm.properties(smiles), "Computing")
        if values is None:
            self.say("molterm couldn't compute them, sorry.")
            return
        rows = [("Formula", values["formula"]), ("MW", f"{values['mw']:.1f}"), ("cLogP", f"{values['logp']:.2f}"),
                ("TPSA", f"{values['tpsa']:.1f}"), ("H-bond donors", values["hbd"]), ("H-bond acceptors", values["hba"]),
                ("QED", f"{values['qed']:.2f}"), ("Lipinski violations", values["ro5"])]
        table = "\n".join(f"\x1b[2m{label:<20}\x1b[0m {value}" for label, value in rows)
        self.use_tui("tui.show", {"title": f"Properties of {name or smiles}", "body": table}, "Showing")
        verdict = "drug-like by Lipinski's rule of five" if values["ro5"] == 0 else f"breaks {values['ro5']} of Lipinski's rules"
        self.say(f"It's {verdict}, with a QED of {values['qed']:.2f}.")
