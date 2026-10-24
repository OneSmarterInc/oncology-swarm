"""Browser acceptance flow against the actual API with a deterministic HTTP fixture.

Run: python scripts/ui_test.py [--chromium /path/to/chrome]
Requires playwright install chromium unless an executable is supplied.
"""

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from playwright.sync_api import sync_playwright

from attorneyscraper.fetch import Fetcher
from attorneyscraper.web import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium")
    args = parser.parse_args()

    def factory(**kwargs):
        def handler(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nAllow: /",
                    headers={"content-type": "text/plain"},
                )
            return httpx.Response(
                200,
                text="<main><h1>Jane Smith</h1><h2>Attorney</h2><p>Jane advises hospitals on healthcare regulatory compliance and HIPAA.</p><p>jsmith@firm.com</p></main>",
                headers={"content-type": "text/html"},
            )

        return Fetcher(
            delay=0,
            stop=kwargs.get("stop"),
            transport=httpx.MockTransport(handler),
            validate=lambda u: u,
        )

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Path(tmp) / "test.db", factory)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8802, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        try:
            with sync_playwright() as p:
                options = {
                    "headless": True,
                    "args": ["--no-sandbox", "--disable-dev-shm-usage"],
                }
                if args.chromium:
                    options["executable_path"] = args.chromium
                browser = p.chromium.launch(**options)
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto("http://127.0.0.1:8802")
                page.get_by_text("People discovered", exact=True).wait_for()
                page.locator("#new-run").click()
                page.locator("#sources").fill("Test Law | https://firm.com/jane-smith")
                page.locator("#start-button").click()
                page.locator("#run-summary .badge").filter(
                    has_text="completed"
                ).wait_for(timeout=15000)
                page.get_by_role("button", name="View people", exact=True).click()
                page.get_by_role("button", name="Inspect", exact=True).click()
                page.locator("#person-dialog").wait_for(state="visible")
                assert "jsmith@firm.com" in page.locator("#person-content").inner_text()
                page.locator("#review-status").select_option("needs_review")
                page.locator("#review-note").fill("Human review requested for test")
                page.get_by_role("button", name="Save review", exact=True).click()
                page.locator("#notice").filter(has_text="Review saved").wait_for()
                page.get_by_role("button", name="Review queue", exact=False).click()
                page.get_by_role("button", name="Inspect", exact=True).wait_for()
                with page.expect_download() as d:
                    page.locator("#export-xlsx").click()
                assert d.value.suggested_filename.endswith(".xlsx")
                page.locator("[data-view=dashboard]").click()
                page.get_by_text("People discovered", exact=True).wait_for()
                page.screenshot(path="/tmp/attorney-overview.png", full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.reload()
                page.get_by_text("People discovered", exact=True).wait_for()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                page.screenshot(path="/tmp/attorney-mobile.png", full_page=True)
                assert not errors, errors
                browser.close()
                print(
                    json.dumps(
                        {
                            "passed": True,
                            "checks": [
                                "create run",
                                "crawl completes",
                                "directory",
                                "contact evidence",
                                "review decision",
                                "review queue",
                                "Excel download",
                                "desktop",
                                "mobile overflow",
                                "no JavaScript errors",
                            ],
                        }
                    )
                )
        finally:
            server.should_exit = True
            thread.join(10)


if __name__ == "__main__":
    main()
