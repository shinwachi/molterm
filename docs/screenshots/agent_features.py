"""Record the agent's pointing and editing features, from the running dev agent: `make agent-gif` (with agent_gif.py).

    docs/images/molterm-agent-annotate.gif  a molecule edited where it is shown: a note typed in its bubble on two lines, the atom
                                            highlighted and painted in the SMILES under the drawing, the SMILES hidden and shown again
                                            with its checkbox, the drawing made larger, Done, and the edit clicked into a message
    docs/images/molterm-agent-point.gif     pointing things out: a bubble dragged aside, text dragged across and highlighted from
                                            its menu, its context clicked into a message, and the agent marking it as seen
    docs/images/molterm-agent-inline.png    the inline editor, with its notes in bubbles beside the drawing
    docs/images/molterm-agent-bubbles.png   the agent's marks: a highlighted part of the molecule, painted in its SMILES too, notes in
                                            bubbles, and a context

Each run starts a session, asks for imatinib and declines its properties, then records. The pointer positions are those of a
1000 by 900 window, as agent_gif.py uses.
"""
import os
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_gif import AGENT, VIEW, record, save  # noqa: E402

IMAGES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "images")


def session(p):
    browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport=VIEW, device_scale_factor=1, color_scheme="dark")
    page.goto(AGENT)
    page.wait_for_function("document.getElementById('state').textContent.startsWith('session')", timeout=120_000, polling=100)
    page.locator("#terminal").click()
    page.wait_for_timeout(6000)
    page.keyboard.type("show me a drug")
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")  # imatinib, marked by the agent
    page.wait_for_timeout(14000)
    page.keyboard.press("Escape")  # no properties
    page.wait_for_timeout(3000)
    box = page.locator("#frame").bounding_box()
    return browser, page, box


def helpers(page, box):
    def at(x, y):
        return box["x"] + x, box["y"] + y

    def pause(seconds):
        page.wait_for_timeout(seconds * 1000)

    def click(x, y, button="left"):
        page.mouse.move(*at(x, y), steps=8)
        pause(0.3)
        page.mouse.click(*at(x, y), button=button)

    def drag(x0, y0, x1, y1):
        page.mouse.move(*at(x0, y0), steps=8)
        page.mouse.down()
        page.mouse.move(*at(x1, y1), steps=20)
        pause(0.3)
        page.mouse.up()
    return at, pause, click, drag


def shot(page, name):
    page.locator("#frame").screenshot(path=os.path.join(IMAGES, name))
    print(f"-> {name}", flush=True)


def annotate(p):
    browser, page, box = session(p)
    at, pause, click, drag = helpers(page, box)

    def script():
        pause(1.5)
        click(150, 385)                          # the picture: the editor, in place
        pause(3)
        page.mouse.move(*at(217, 432), steps=10)  # the carbonyl oxygen
        pause(1)
        click(217, 432, "right")
        pause(1.5)
        click(247, 667)                          # Note...
        pause(1)
        page.keyboard.type("carbonyl O", delay=70)  # typed in its bubble, at the end of its leader
        pause(0.4)
        page.keyboard.press("Shift+Enter")       # a second line
        page.keyboard.type("H-bond acceptor", delay=70)
        pause(0.8)
        page.keyboard.press("Enter")             # kept
        pause(3)
        shot(page, "molterm-agent-inline.png")
        click(217, 432, "right")
        pause(1.5)
        click(247, 653)                          # Highlight: its O painted in the SMILES under the drawing
        pause(2.5)
        click(505, 272)                          # ☑ SMILES: hidden
        pause(2)
        click(505, 272)                          # and shown again
        pause(2)
        page.keyboard.press("Alt+ArrowDown")     # a larger drawing, the transcript making room
        pause(2.5)
        page.keyboard.press("Alt+ArrowDown")
        pause(2.5)
        page.mouse.move(*at(500, 880), steps=5)
        page.keyboard.press("Control+q")         # Done: the edit waits above the input
        pause(4)
        click(100, 737)                          # into the message
        pause(1)
        page.keyboard.type("is this better?", delay=70)
        pause(0.5)
        page.keyboard.press("Enter")
        pause(9)

    save(record(page, script), box, os.path.join(IMAGES, "molterm-agent-annotate.gif"))
    browser.close()


def point(p):
    browser, page, box = session(p)
    at, pause, click, drag = helpers(page, box)

    def script():
        pause(1.5)
        drag(430, 342, 600, 400)                 # the agent's bubble, out of the way
        pause(1.5)
        drag(290, 582, 412, 582)                 # "N-methylpiperazine": handles, and a menu under them
        pause(2)
        page.keyboard.press("ArrowUp")           # its last item: Highlight
        pause(0.6)
        page.keyboard.press("Enter")             # highlighted, waiting above the input
        pause(2)
        shot(page, "molterm-agent-bubbles.png")
        click(100, 737)                          # into the message
        pause(1)
        page.keyboard.type("why this part?", delay=70)
        pause(0.5)
        page.keyboard.press("Enter")             # the agent marks it as seen
        pause(9)

    save(record(page, script), box, os.path.join(IMAGES, "molterm-agent-point.gif"))
    browser.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        for name in sys.argv[1:] or ("annotate", "point"):
            {"annotate": annotate, "point": point}[name](p)
