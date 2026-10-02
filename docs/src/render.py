"""Render docs/src/hero.html to docs/hero.png at 2x (1600x800 CSS px).

    pip install playwright && playwright install chromium
    python docs/src/render.py
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path(__file__).resolve().parent
OUT = SRC.parent / "hero.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 800}, device_scale_factor=2)
    page.goto((SRC / "hero.html").as_uri())
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(OUT), clip={"x": 0, "y": 0, "width": 1600, "height": 800})
    browser.close()
print(f"wrote {OUT}")
