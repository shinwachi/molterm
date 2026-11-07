"""Record the agent's TUI as an animated GIF, docs/images/molterm-agent.gif: `make agent-gif` (Playwright's image, on the traefik
network, against the running dev agent).

A user asks for a drug, picks one, sees its picture and asks for its properties. Chromium's screencast hands over a lossless frame
each time the page changes, with its time; the frames are cut to the terminal, put on one palette (a terminal has few colours, so
nothing needs dithering and nothing shimmers), and each lasts until the next, with long pauses shortened and the end held.
"""
import base64
import io
import os

from PIL import Image
from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "images", "molterm-agent.gif")
AGENT = os.environ.get("MOLTERM_AGENT", "http://molterm-agent-dev-molterm-agent-1:8950")
VIEW = {"width": 1000, "height": 900}
WIDTH = 760           # the GIF's width in pixels
SHORTEST, LONGEST = 40, 2000  # milliseconds a frame may last
END = 4000            # the finished screen, before the GIF starts again


def record(page, script):
    """Run `script()` while the screencast runs: [(seconds, PNG bytes)], one per change."""
    cdp, frames = page.context.new_cdp_session(page), []

    def frame(event):
        frames.append((event["metadata"]["timestamp"], base64.b64decode(event["data"])))
        cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    cdp.on("Page.screencastFrame", frame)
    cdp.send("Page.startScreencast", {"format": "png", "maxWidth": VIEW["width"], "maxHeight": VIEW["height"]})
    script()
    cdp.send("Page.stopScreencast")
    return frames


def save(frames, box):
    crop = tuple(round(v) for v in (box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]))
    images, times = [], []
    for t, png in frames:
        image = Image.open(io.BytesIO(png)).convert("RGB").crop(crop)
        image = image.resize((WIDTH, round(image.height * WIDTH / image.width)), Image.LANCZOS)
        if images and image.tobytes() == images[-1].tobytes():
            continue
        images.append(image)
        times.append(t)
    durations = [min(max(round((b - a) * 1000), SHORTEST), LONGEST) for a, b in zip(times, times[1:])] + [END]
    # one palette for every frame, from a strip of the first, the middle and the last
    strip = Image.new("RGB", (WIDTH, images[0].height * 3))
    for i, image in enumerate((images[0], images[len(images) // 2], images[-1])):
        strip.paste(image, (0, i * image.height))
    palette = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=False)
    print(f"-> {OUT}: {len(frames)} frames, {sum(durations) / 1000:.1f} s, {os.path.getsize(OUT) // 1024} KB", flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport=VIEW, device_scale_factor=1, color_scheme="dark")
        page.goto(AGENT)
        page.wait_for_function("document.getElementById('state').textContent.startsWith('session')", timeout=120_000, polling=100)
        page.wait_for_timeout(1500)  # the banner has its session
        box = page.locator("#frame").bounding_box()
        page.locator("#terminal").click()

        def pause(seconds):
            page.wait_for_timeout(seconds * 1000)

        def script():
            pause(6)                                  # the greeting streams in
            page.keyboard.type("show me a drug", delay=90)
            pause(0.5)
            page.keyboard.press("Enter")
            pause(2.5)                                # the agent asks which
            for key in ("ArrowDown", "ArrowDown", "ArrowDown", "ArrowUp"):
                page.keyboard.press(key)
                pause(0.6)
            page.keyboard.press("Enter")              # Imatinib
            pause(8)                                  # drawn by molterm, shown, then a question
            page.keyboard.press("Enter")              # yes, the properties
            pause(6)

        save(record(page, script), box)
        browser.close()


if __name__ == "__main__":
    main()
