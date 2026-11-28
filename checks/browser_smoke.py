"""Exercise the installed CLI's Web product in Chromium, with a fresh store for each viewport."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


@contextmanager
def workspace():
    with TemporaryDirectory() as temp, socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        url = f"http://127.0.0.1:{port}"
        log_path = Path(temp) / "server.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "job_right", "web", "--port", str(port), "--data-dir", temp],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError(log_path.read_text())
                    try:
                        with urlopen(url, timeout=1):
                            break
                    except URLError:
                        time.sleep(0.1)
                else:
                    raise TimeoutError(log_path.read_text())
                yield url
            finally:
                process.terminate()
                process.wait(timeout=10)


def scenario(browser, width, height, screenshots):
    with workspace() as url, browser.new_context(viewport={"width": width, "height": height}) as context:
        errors = []
        context.on("weberror", lambda exc: errors.append(str(exc.error)))
        page = context.new_page()
        page.goto(url)

        def capture(name):
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
            if screenshots:
                screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshots / f"{width}-{name}.png")

        def example(tab, name):
            tab.get_by_role("link", name=name, exact=True).click()
            tab.get_by_role("button", name="Preview change").click()
            expect(tab.get_by_role("heading", name="Preference review", exact=True)).to_be_visible()

        expect(page.get_by_role("link", name="Matching 9")).to_be_visible()
        capture("matches")
        print(f"{width}px studio begins {round(page.locator('#studio').bounding_box()['y'])}px down the page")
        page.keyboard.press("Tab")
        expect(page.get_by_role("link", name="Skip to content")).to_be_focused()
        page.get_by_role("link", name="Filtered 5").click()
        expect(page.get_by_text("Marigold Gaming")).to_be_visible()
        page.get_by_role("link", name="Preferences", exact=True).click()
        expect(page.get_by_text("Cobalt Orchard Health", exact=False)).to_be_visible()
        page.get_by_role("link", name="Preview a change").click()
        example(page, "Remote only")
        expect(page.get_by_text("Saved jobs newly hidden")).to_be_visible()
        expect(page.locator(".risk")).to_contain_text("Fernhill Freight")
        expect(page.locator(".risk")).to_contain_text("employer exception")
        capture("review")
        first = page.url
        page.reload()
        expect(page.get_by_role("button", name="Reject change")).to_be_visible()
        page.get_by_role("link", name="Matches", exact=True).click()
        page.get_by_role("link", name="Resume latest review").click()
        expect(page).to_have_url(first)
        page.get_by_role("button", name="Reject change").click()
        expect(page.get_by_text("Review rejected.", exact=True)).to_be_visible()

        page.get_by_role("link", name="Matches", exact=True).click()
        example(page, "Salary floor")
        old_review = page.url
        other = context.new_page()
        other.goto(url)
        example(other, "Prefer Rust")
        other.get_by_role("button", name="Approve change").click()
        expect(other.get_by_text("Saved as profile v2.")).to_be_visible()
        page.get_by_role("button", name="Approve change").click()
        expect(page).to_have_url(old_review)
        expect(page.get_by_text("This review is out of date.")).to_be_visible()
        capture("conflict")
        page.get_by_role("link", name="Use this feedback").click()
        expect(page.get_by_label("Your feedback")).to_have_value("Raise my salary floor to $160k.")
        page.get_by_role("button", name="Preview change").click()
        expect(page.locator(".decision")).to_contain_text("v2")
        page.get_by_role("button", name="Approve change").click()
        expect(page.get_by_text("Saved as profile v3.")).to_be_visible()
        page.reload()
        page.get_by_role("link", name="Preferences", exact=True).click()
        expect(page.get_by_text("$160,000", exact=True)).to_be_visible()
        expect(page.locator(".prefs")).to_contain_text("Rust")
        page.get_by_role("link", name="Activity", exact=True).click()
        expect(page.locator(".entry")).to_have_count(4)
        expect(page.locator(".revision")).to_have_count(3)
        expect(page.locator(".entry .stale")).to_have_count(1)
        expect(page.locator(".entry .rejected")).to_have_count(1)
        assert not errors, errors
        print(f"{width}×{height}: preview, resume, reload, decisions, conflict recovery and history passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    with sync_playwright() as pw, pw.chromium.launch() as browser:
        for width, height in [(1440, 900), (390, 844)]:
            scenario(browser, width, height, args.screenshots)
