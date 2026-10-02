"""Record docs/demo.gif from a real run of idgate against the real judge.

It runs the commands below for real in a scratch copy of the repo, with the
default judge (the `claude` CLI and your own login) and an empty cache. Their
actual stdout, stderr and exit codes go to demo_session.json. Then each frame
of demo.html is rendered with Playwright and joined with ffmpeg. No output line
is typed in by hand. The terminal window matches voiceprint's and gdoc-surgical's.

    pip install playwright && python -m playwright install chromium
    python docs/src/demo.py                # capture with the real judge, then render
    python docs/src/demo.py --render-only  # render demo_session.json again

The judge is a model, so a rerun can word its issues differently. The script
checks the exit codes it expects and stops if the run does not match.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "docs" / "demo.gif"
SESSION = HERE / "demo_session.json"
FPS = 12

# (command after the prompt, typed?, hold ms, expected exit or None for cat)
STEPS = [
    ("cat examples/draft-bad.md", True, 1400, None),
    ("idgate check --file examples/draft-bad.md", True, 3400, 1),
    ("idgate gate --file examples/draft-bad.md", True, 3000, 0),
    ("idgate check --file examples/draft-good.md", True, 3200, 0),
]


def capture():
    tmp = Path(tempfile.mkdtemp(prefix="idgate_demo_"))
    shutil.copytree(ROOT / "examples", tmp / "examples")
    env = dict(os.environ, IDGATE_DATA_DIR=str(tmp / ".idgate"), PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    session = []
    for cmd, typed, hold, want in STEPS:
        if cmd.startswith("cat "):
            out = (tmp / cmd.split()[1]).read_text(encoding="utf-8").rstrip("\n")
            session.append({"cmd": cmd, "out": out, "typed": typed, "hold": hold})
            continue
        argv = [sys.executable, "-m", "idgate"] + shlex.split(cmd)[1:]
        p = subprocess.run(argv, cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8")
        out = (p.stdout + p.stderr).rstrip("\n")
        print("$ %s\n%s\nexit %s\n" % (cmd, out, p.returncode))
        assert want is None or p.returncode == want, "unexpected exit %s for %r" % (p.returncode, cmd)
        session.append({"cmd": cmd, "out": out + "\nexit %d" % p.returncode, "typed": typed, "hold": hold})
    shutil.rmtree(tmp, ignore_errors=True)
    SESSION.write_text(json.dumps(session, indent=1, ensure_ascii=False), encoding="utf-8")
    return session


def render(session):
    from playwright.sync_api import sync_playwright
    page_html = (HERE / "demo.html").read_text(encoding="utf-8").replace("__SESSION__", json.dumps(session))
    tmp = Path(tempfile.mkdtemp(prefix="idgate_frames_"))
    page_path = tmp / "demo.html"
    page_path.write_text(page_html, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 800, "height": 450})
        page.goto(page_path.as_uri())
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        total = page.evaluate("window.TOTAL_MS")
        n = int(total / 1000 * FPS) + 1
        for i in range(n):
            page.evaluate("renderAt(%d)" % int(i * 1000 / FPS))
            page.screenshot(path=str(tmp / ("f%04d.png" % i)))
        browser.close()
    pattern, palette = str(tmp / "f%04d.png"), str(tmp / "palette.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pattern,
                    "-vf", "palettegen=max_colors=64:stats_mode=diff", palette], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pattern,
                    "-i", palette, "-lavfi", "paletteuse=dither=none:diff_mode=rectangle",
                    "-loop", "0", str(OUT)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {OUT}: {n} frames, {n / FPS:.1f}s, {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    if "--render-only" in sys.argv:
        render(json.loads(SESSION.read_text(encoding="utf-8")))
    else:
        render(capture())
