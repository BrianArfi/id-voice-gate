"""Record docs/demo-site.gif from a real run of `idgate site` over examples/site, lint only.

No model is called, so this run is offline and the same every time. The command runs for
real in a scratch copy of examples/, its actual output goes to demo_site_session.json, and
the frames are drawn with the same terminal page as demo.gif (demo.html), at a larger size.

    pip install playwright && python -m playwright install chromium
    python docs/src/demo_site.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "docs" / "demo-site.gif"
SESSION = HERE / "demo_site_session.json"
FPS = 12
W, H = 1100, 700
CMD = "idgate site examples/site --lint-only --include-ts"


def capture():
    tmp = Path(tempfile.mkdtemp(prefix="idgate_site_"))
    shutil.copytree(ROOT / "examples", tmp / "examples")
    env = dict(os.environ, IDGATE_DATA_DIR=str(tmp / ".idgate"), PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    argv = [sys.executable, "-m", "idgate"] + CMD.split()[1:]
    p = subprocess.run(argv, cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    out = (p.stdout + p.stderr).rstrip("\n")
    print("$ %s\n%s\nexit %s" % (CMD, out, p.returncode))
    assert p.returncode == 1, "expected exit 1 (blocks found), got %s" % p.returncode
    session = [{"cmd": CMD, "out": out + "\nexit %d" % p.returncode, "typed": True, "hold": 4500}]
    SESSION.write_text(json.dumps(session, indent=1, ensure_ascii=False), encoding="utf-8")
    return session


def page(session):
    html = (HERE / "demo.html").read_text(encoding="utf-8")
    swaps = [
        ("width: 800px; height: 450px", "width: %dpx; height: %dpx" % (W, H)),
        (".win { height: 416px", ".win { height: %dpx" % (H - 34)),
        ("font-size: 13.5px", "font-size: 15px"),
        ("idgate, judge: claude CLI (sonnet)", "idgate site, lint only (no model, offline)"),
        ("function deco(line) {\n  const h = esc(line);",
         "function deco(line) {\n  const h = esc(line);\n"
         "  if (line.startsWith('BLOCK')) return '<span class=\"fail\">BLOCK</span>' + h.slice(5);\n"
         "  if (line.startsWith('WARN')) return '<span class=\"hit\">WARN</span>' + h.slice(4);\n"
         "  if (line.startsWith('[idgate]')) return '<span class=\"info\">' + h + '</span>';"),
    ]
    for a, b in swaps:
        assert a in html, "demo.html changed, cannot find %r" % a
        html = html.replace(a, b)
    return html.replace("__SESSION__", json.dumps(session))


def render(session):
    from playwright.sync_api import sync_playwright
    tmp = Path(tempfile.mkdtemp(prefix="idgate_site_frames_"))
    (tmp / "demo.html").write_text(page(session), encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": W, "height": H})
        pg.goto((tmp / "demo.html").as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready")
        total = pg.evaluate("window.TOTAL_MS")
        n = int(total / 1000 * FPS) + 1
        for i in range(n):
            pg.evaluate("renderAt(%d)" % int(i * 1000 / FPS))
            pg.screenshot(path=str(tmp / ("f%04d.png" % i)))
        browser.close()
    pattern, palette = str(tmp / "f%04d.png"), str(tmp / "palette.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pattern,
                    "-vf", "palettegen=max_colors=128:stats_mode=full", palette], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", pattern,
                    "-i", palette, "-lavfi", "paletteuse=dither=none:diff_mode=rectangle",
                    "-loop", "0", str(OUT)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {OUT}: {n} frames, {n / FPS:.1f}s, {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    render(capture())
