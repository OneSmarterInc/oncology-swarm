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
                    has_text="Completed"
                ).wait_for(timeout=15000)
                assert (
                    "Extraction completed" in page.locator("#run-summary").inner_text()
                )
                assert page.locator("progress").get_attribute("value") == "1"
                assert page.locator("[data-view=review]").count() == 0
                # Starting URLs can be done while discovered pages are still running.
                page.evaluate(r"""async () => {
                    const original = window.fetch;
                    window.fetch = async (url, options) => {
                        const response = await original(url, options);
                        if (/\/api\/runs\/[^/]+$/.test(url)) {
                            const run = await response.json();
                            run.status='running';run.progress.waiting=2;
                            return new Response(JSON.stringify(run), {status:200});
                        }
                        return response;
                    };
                    await runDetail();
                    window.fetch=original;
                }""")
                assert page.locator("progress").get_attribute("value") is None
                assert (
                    "Extraction in progress"
                    in page.locator("#run-summary").inner_text()
                )
                page.get_by_role("button", name="View people", exact=True).click()
                page.get_by_role("button", name="Inspect", exact=True).click()
                page.locator("#person-dialog").wait_for(state="visible")
                assert "jsmith@firm.com" in page.locator("#person-content").inner_text()
                assert "Email delivery" in page.locator("#person-content").inner_text()
                assert (
                    "Attorney match supported"
                    in page.locator("#people-rows").inner_text()
                )
                assert page.locator("#review-form").count() == 0
                page.locator("#close-dialog").click()
                with page.expect_download() as d:
                    page.locator("#export-xlsx").click()
                assert d.value.suggested_filename.endswith(".xlsx")
                page.locator("#new-run").click()
                assert page.locator("#discover").count() == 0
                page.locator("#source-file").set_input_files(
                    {
                        "name": "valid.csv",
                        "mimeType": "text/csv",
                        "buffer": b"Organization,URL\nOld Firm,https://old.example/\n",
                    }
                )
                page.locator("#upload-status").filter(
                    has_text="1 source rows"
                ).wait_for()
                page.locator("#source-file").set_input_files(
                    {
                        "name": "invalid.csv",
                        "mimeType": "text/csv",
                        "buffer": b"Organization,URL\nBroken,invalid\n",
                    }
                )
                page.locator("#notice").filter(has_text="Row 2").wait_for()
                assert page.evaluate("uploaded.length") == 0
                page.evaluate("""() => {
                    window.originalFetch = window.fetch;
                    window.pendingUploads = [];
                    window.fetch = (url, options) => url === '/api/sources/validate'
                        ? new Promise(resolve => pendingUploads.push(resolve))
                        : originalFetch(url, options);
                }""")
                page.locator("#source-file").set_input_files(
                    {"name": "first.csv", "mimeType": "text/csv", "buffer": b"first"}
                )
                page.wait_for_function("pendingUploads.length === 1")
                page.locator("#source-file").set_input_files(
                    {"name": "second.csv", "mimeType": "text/csv", "buffer": b"second"}
                )
                page.wait_for_function("pendingUploads.length === 2")
                page.evaluate(
                    """() => pendingUploads[1](new Response(JSON.stringify({sources:[{organization:'Second',url:'https://second.example/'}]}),{status:200}))"""
                )
                page.wait_for_function("uploaded.length === 1")
                page.evaluate(
                    """() => pendingUploads[0](new Response(JSON.stringify({sources:[{organization:'First',url:'https://first.example/'}]}),{status:200}))"""
                )
                page.wait_for_function(
                    "uploaded[0].organization === 'Second' && !uploadPending"
                )
                assert page.evaluate("uploaded[0].organization") == "Second"
                page.evaluate("window.fetch = window.originalFetch")

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
                                "no review controls",
                                "active progress never full",
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
