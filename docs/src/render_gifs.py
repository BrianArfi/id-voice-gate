"""Render the README's explainer GIFs (illustrations) from their HTML sources.

    pip install playwright && python -m playwright install chromium   # plus ffmpeg on PATH
    python docs/src/render_gifs.py

hero-anim.html    -> docs/hero.gif          the hero still (hero.html) in motion
before-after.html -> docs/before-after.gif  four rule examples, flagged, then wiped to the fix
how-it-works.html -> docs/how-it-works.gif  lint, judge, rewrite, re-check, rewrite guard, exit codes

The real recordings have their own scripts: demo.py (docs/demo.gif, real judge) and
demo_site.py (docs/demo-site.gif, lint only). render.py renders docs/hero.png.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent

JOBS = [
    # source, output, width, height, seconds
    ("hero-anim.html", "hero.gif", 1600, 800, 10),
    ("before-after.html", "before-after.gif", 1200, 700, 10),
    ("how-it-works.html", "how-it-works.gif", 1200, 700, 12.2),
]

for src, out, w, h, dur in JOBS:
    subprocess.run([sys.executable, str(HERE / "record_html.py"), str(HERE / src), str(DOCS / out),
                    "--w", str(w), "--h", str(h), "--dur", str(dur), "--fps", "12", "--colors", "96"], check=True)
