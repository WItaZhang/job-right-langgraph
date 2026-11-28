"""Record the product itself against a disposable, seeded SQLite workspace."""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from browser_smoke import workspace  # noqa: E402

POINTER = """
addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
  dot.style.cssText=[
    'position:fixed;z-index:9999;pointer-events:none;width:16px;height:16px;border-radius:50%',
    'background:#b64e2d;border:3px solid #fff;box-shadow:0 1px 7px #46382877',
    'left:-30px;top:-30px;transform:translate(-50%,-50%)'
  ].join(';');
  document.body.appendChild(dot);
  addEventListener('mousemove', e => {dot.style.left=e.clientX+'px';dot.style.top=e.clientY+'px';});
  addEventListener('mousedown', () => dot.animate([{scale:'1'},{scale:'1.9'},{scale:'1'}],350));
});
"""


def record(browser, url, out, vertical):
    out.mkdir(parents=True, exist_ok=True)
    size = dict(width=390 if vertical else 1080, height=740 if vertical else 740)
    with browser.new_context(
        viewport=size,
        record_video_dir=out / "raw",
        record_video_size=size,
        reduced_motion="reduce",
        accept_downloads=True,
    ) as ctx:
        ctx.add_init_script(POINTER)

        def start(address):
            p = ctx.new_page()
            p.goto(address, wait_until="networkidle")
            return p

        def tap(p, item):
            item.scroll_into_view_if_needed()
            box = item.bounding_box()
            p.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.5, steps=8)
            p.wait_for_timeout(200)
            item.click()

        def finish(p, n):
            p.wait_for_timeout(700)
            video = p.video
            p.close()
            video.save_as(out / f"scene-{n}.webm")

        page = start(url)
        job = (
            page.locator(".job")
            .filter(has_text="Senior Backend Engineer")
            .filter(has_text="Fernhill Freight")
        )
        job.screenshot(path=out / "saved.png")
        page.close()
        p = start(url + "/#studio")
        tap(p, p.get_by_role("link", name="Remote only", exact=True))
        p.wait_for_timeout(450)
        tap(p, p.get_by_role("button", name="Preview change"))
        review = p.url
        p.locator(".risk").scroll_into_view_if_needed()
        finish(p, 1)
        p = start(review)
        tap(p, p.get_by_role("link", name="Try a Fernhill Freight exception"))
        p.wait_for_timeout(350)
        tap(p, p.get_by_role("button", name="Preview change"))
        p.wait_for_timeout(520)
        tap(p, p.get_by_role("button", name="Approve change"))
        p.wait_for_timeout(350)
        tap(p, p.get_by_role("link", name="View job matches"))
        card = (
            p.locator(".job").filter(has_text="Senior Backend Engineer").filter(has_text="Fernhill Freight")
        )
        card.scroll_into_view_if_needed()
        finish(p, 2)
        p = start(url)
        tap(
            p,
            p.locator(".job")
            .filter(has_text="Senior Backend Engineer")
            .filter(has_text="Fernhill Freight")
            .get_by_role("link", name="Tailor documents"),
        )
        tap(p, p.get_by_role("button", name="Use fictional example"))
        p.wait_for_timeout(700)
        tap(p, p.get_by_role("button", name="Prepare résumé & letter"))
        packet = p.url
        p.locator(".paper").scroll_into_view_if_needed()
        finish(p, 3)
        p = start(packet)
        tap(p, p.get_by_role("link", name="Cover letter", exact=True))
        p.wait_for_timeout(600)
        p.locator(".fact").first.scroll_into_view_if_needed()
        p.wait_for_timeout(850)
        tap(p, p.get_by_role("link", name="Download both"))
        finish(p, 4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--vertical", action="store_true")
    args = parser.parse_args()
    with workspace() as url, sync_playwright() as pw, pw.chromium.launch() as chrome:
        record(chrome, url, args.output, args.vertical)
