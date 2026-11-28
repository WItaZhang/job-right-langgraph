"""A new visitor prepares, revises, exports and reopens a real application packet."""

import argparse
import io
import zipfile
from contextlib import nullcontext
from pathlib import Path

from browser_smoke import workspace
from playwright.sync_api import expect, sync_playwright


def scenario(browser, url, width, height, screenshots=None):
    with browser.new_context(
        viewport={"width": width, "height": height},
        is_mobile=width < 600,
        has_touch=width < 600,
        accept_downloads=True,
    ) as context:
        page = context.new_page()

        def shot(name):
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if screenshots:
                screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshots / f"{width}-{name}.png")

        page.goto(url)
        page.locator(".job").filter(has_text="Fernhill Freight").first.get_by_role(
            "link", name="Tailor documents"
        ).click()
        page.get_by_role("button", name="Use fictional example").click()
        page.get_by_role("button", name="Prepare résumé & letter").click()
        expect(page.locator(".paper")).to_contain_text("Avery Lane")
        shot("resume")
        page.get_by_role("link", name="Cover letter", exact=True).click()
        expect(page.locator(".paper")).to_contain_text("Dear Fernhill Freight hiring team")
        expect(page.locator(".fact").first).to_contain_text("Résumé")
        shot("letter")
        page.reload()
        page.get_by_text("Edit cover letter", exact=True).click()
        page.get_by_label("Document text").fill(
            "Dear Fernhill Freight hiring team,\n\nThank you for your time.\n\nAvery Lane"
        )
        page.get_by_role("button", name="Save letter").click()
        expect(page.locator(".tag")).to_contain_text("v2")
        with page.expect_download() as event:
            page.get_by_role("link", name="Download both").click()
        with zipfile.ZipFile(io.BytesIO(Path(event.value.path()).read_bytes())) as archive:
            assert "Thank you for your time." in archive.read("cover_letter/letter.txt").decode()
        page.get_by_role("link", name="Print / PDF").click()
        assert page.pdf().startswith(b"%PDF")
        page.get_by_role("link", name="← Document").click()
        private_url = page.url
        page.get_by_role("link", name="Bring a job description").click()
        page.get_by_label("Job title").fill("Platform Engineer")
        page.get_by_label("Company", exact=True).fill("Sample Cloud Tools")
        page.get_by_label("Job description", exact=True).fill(
            "Maintain Kubernetes and Terraform developer environments."
        )
        expect(page.locator("#base")).to_contain_text("Avery Lane")
        page.get_by_role("button", name="Prepare résumé & letter").click()
        expect(page.locator(".paper .resume-bullet").first).to_contain_text("Terraform")
        page.get_by_role("link", name="Documents", exact=True).click()
        expect(page.locator(".entry")).to_have_count(2)
        print(f"{width}×{height}: résumé, letter, custom JD, revision, refresh, ZIP and PDF passed.")
        return private_url


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--url")
    args.add_argument("--screenshots", type=Path)
    a = args.parse_args()
    with sync_playwright() as pw, pw.chromium.launch() as browser:
        for w, h in ((1440, 900), (390, 844)):
            with nullcontext(a.url) if a.url else workspace() as url:
                private = scenario(browser, url, w, h, a.screenshots)
                if a.url:
                    with browser.new_context() as stranger:
                        assert stranger.new_page().goto(private).status == 404
