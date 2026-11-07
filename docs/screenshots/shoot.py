"""Take the README's hero screenshot, and web/README.md's, from the running dev services, in headless Chromium: `make screenshots`
(Playwright's image, on the traefik network, so the pages are reached by container name and no hostname appears in them).

The showcase is a recording that plays by itself, so the shot waits for a caption, lets the frame settle, and photographs the
terminal alone.
"""
import os
import sys

from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "images")
MOLTERM = os.environ.get("MOLTERM", "http://molterm-dev-molterm-1:8910")
WEB = os.environ.get("MOLTERM_WEB", "http://molterm-web-dev-molterm-web-1:8940")

# (page, [(words in the caption, milliseconds to let the frame settle, file)]), in the order the recording plays them
RECORDED = [
    (f"{MOLTERM}/showcase", [
        ("A text interface with structures in it", 1800, "molterm-tui.png"),
    ]),
]
LONGEST = 8 * 60 * 1000  # a recording can take minutes to reach its last shot


def shoot_recording(page, url, shots, only):
    page.goto(url)
    for words, settle, name in shots:
        if only and name not in only:
            continue
        print(f"{url}: waiting for '{words}'", flush=True)
        page.wait_for_function("words => document.getElementById('caption').textContent.includes(words)", arg=words,
                               timeout=LONGEST, polling=100)
        page.wait_for_timeout(settle)
        page.locator("#frame").screenshot(path=os.path.join(OUT, name))
        print(f"  -> {name}", flush=True)


def shoot_web(page, only):
    """The browser page: one drug as RDKit's picture and in Braille, one after the other, as a visitor would draw them."""
    if only and "web.png" not in only:
        return
    page.goto(f"{WEB}/#" + "smiles=Cn1cnc2c1c(%3DO)n(C)c(%3DO)n2C&mode=picture")
    page.wait_for_function("document.getElementById('status').textContent.startsWith('Drawn')", timeout=60_000)
    page.click("#clear")
    imatinib = "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1"
    for smiles, mode in ((imatinib, "picture"), (imatinib, "braille")):
        page.fill("#input", smiles)
        page.click(f"#modes button[data-mode={mode}]")
        page.wait_for_timeout(1200)
    page.wait_for_timeout(1000)
    page.locator("main .layout").screenshot(path=os.path.join(OUT, "web.png"))
    print("  -> web.png", flush=True)


def main(only):
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as playwright:
        # restty draws with WebGL2, which headless Chromium has from SwiftShader
        browser = playwright.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        context = browser.new_context(viewport={"width": 1280, "height": 1050}, device_scale_factor=2, color_scheme="light")
        shoot_web(context.new_page(), only)
        for url, shots in RECORDED:
            if only and not any(name in only for *_, name in shots):
                continue
            shoot_recording(context.new_page(), url, shots, only)
        browser.close()


if __name__ == "__main__":
    main(set(sys.argv[1:]))
