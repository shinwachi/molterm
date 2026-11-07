"""Record the agent's TUI as an animated GIF, docs/images/molterm-agent.gif: `make agent-gif` (Playwright's image, on the traefik
network, against the running dev agent).

A user asks for a drug, picks one, sees its picture and asks for its properties. Playwright records the page as a video; ffmpeg cuts
it to the terminal, from the moment the session starts, drops repeated frames and makes the GIF.
"""
import glob
import os
import subprocess
import tempfile
import time

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "images", "molterm-agent.gif")
AGENT = os.environ.get("MOLTERM_AGENT", "http://molterm-agent-dev-molterm-agent-1:8950")
VIEW = {"width": 1000, "height": 900}
WIDTH = 760  # the GIF's width in pixels
FPS = 12


def main():
    videos = tempfile.mkdtemp()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport=VIEW, device_scale_factor=1, color_scheme="dark", record_video_dir=videos,
                                      record_video_size=VIEW)
        page = context.new_page()
        began = time.monotonic()
        page.goto(AGENT)
        page.wait_for_function("document.getElementById('state').textContent.startsWith('session')", timeout=120_000, polling=100)
        page.wait_for_timeout(1000)  # the banner is drawn
        start = time.monotonic() - began
        box = page.locator("#frame").bounding_box()
        page.locator("#terminal").click()

        def pause(seconds):
            page.wait_for_timeout(seconds * 1000)

        pause(6)                                  # the greeting streams in
        page.keyboard.type("show me a drug", delay=90)
        pause(0.4)
        page.keyboard.press("Enter")
        pause(2.5)                                # the agent asks which
        for key in ("ArrowDown", "ArrowDown", "ArrowDown", "ArrowUp"):
            page.keyboard.press(key)
            pause(0.5)
        page.keyboard.press("Enter")              # Imatinib
        pause(8)                                  # drawn by molterm, shown, then a question
        page.keyboard.press("Enter")              # yes, the properties
        pause(6)
        length = time.monotonic() - began - start
        context.close()
        browser.close()

    video = glob.glob(os.path.join(videos, "*.webm"))[0]
    x, y, w, h = (int(round(v)) // 2 * 2 for v in (box["x"], box["y"], box["width"], box["height"]))
    graph = (f"crop={w}:{h}:{x}:{y},fps={FPS},scale={WIDTH}:-1:flags=lanczos,mpdecimate,"
             "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
                    "-i", video, "-filter_complex", graph, "-fps_mode", "vfr", "-loop", "0", OUT], check=True)
    print(f"-> {OUT}: {length:.1f} s, {os.path.getsize(OUT) // 1024} KB", flush=True)


if __name__ == "__main__":
    main()
