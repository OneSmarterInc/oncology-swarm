"""Portal acceptance with the real local API/SQLite and deterministic upstream HTTP.
Run: python scripts/ui_test.py [--chromium /path/to/chrome]
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
from attorneyscraper.models import RunConfig
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
            if req.url.path == "/slow":
                fetcher.wait(1)
            return httpx.Response(
                200,
                text="<main><h1>Jane Smith</h1><h2>Attorney</h2><p>Jane advises hospitals on healthcare regulatory compliance and HIPAA.</p><p>jsmith@firm.com</p><p>Direct: +12125552345</p></main>",
                headers={"content-type": "text/html"},
            )

        fetcher = Fetcher(
            delay=0,
            stop=kwargs.get("stop"),
            transport=httpx.MockTransport(handler),
            validate=lambda url: url,
        )
        return fetcher

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
        checks = []
        try:
            with sync_playwright() as p:
                options = {
                    "headless": True,
                    "args": ["--no-sandbox", "--disable-dev-shm-usage"],
                }
                if args.chromium:
                    options["executable_path"] = args.chromium
                browser = p.chromium.launch(**options)
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1050}, color_scheme="light"
                )
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto("http://127.0.0.1:8802")
                page.get_by_text("People discovered", exact=True).wait_for()
                assert (
                    page.locator("header nav").count() == 1
                    and page.locator(".sidebar").count() == 0
                )
                assert (
                    page.locator("header").evaluate(
                        "(n)=>getComputedStyle(n).backgroundColor"
                    )
                    == "rgb(18, 58, 68)"
                )
                checks.append("Campus/Onco top-navigation and theme")
                page.locator("#new-run").click()
                page.locator("#start-button").click()
                page.locator("#notice").filter(has_text="at least one URL").wait_for()
                page.locator("#sources").fill("invalid")
                page.locator("#start-button").click()
                page.locator("#notice").filter(has_text="complete http").wait_for()
                page.locator("#sources").fill("https://firm.com/jane-smith")
                page.locator("#start-button").click()
                page.locator("#run-summary .badge.completed").wait_for(timeout=15000)
                assert page.locator(".crawl-progress").get_attribute("value") == "100"
                rid = page.evaluate("selectedRun")
                assert (
                    page.evaluate("()=>badge('completed_with_issues').textContent")
                    == "Completed"
                )
                assert (
                    page.evaluate("()=>badge('completed_with_issues').className")
                    == "badge completed"
                )
                checks.append("Legacy completion status uses neutral Completed label")
                checks.append("URL-only creation and completed progress")
                # Synthetic progress snapshot checks the UI, not crawler math.
                page.evaluate(
                    """async()=>{const orig=window.fetch;window.fetch=async(url,opts)=>{const res=await orig(url,opts);if(/\\/api\\/runs\\/[^/?]+$/.test(url)){const run=await res.json();run.status='running';run.progress.percent=35;run.progress.waiting=2;return new Response(JSON.stringify(run));}return res;};await runDetail();window.fetch=orig;}"""
                )
                assert page.locator(".crawl-progress").get_attribute("value") == "35"
                page.evaluate("runDetail()")
                checks.append("determinate active progress")
                page.get_by_role("button", name="View people", exact=True).click()
                page.get_by_role("button", name="Inspect", exact=True).click()
                assert "jsmith@firm.com" in page.locator("#person-content").inner_text()
                assert "Email delivery" in page.locator("#person-content").inner_text()
                assert "+1 212-555-2345" in page.locator("#person-content").inner_text()
                page.locator("#close-dialog").click()
                for kind in ["xlsx", "csv", "json"]:
                    with page.expect_download() as download:
                        page.locator("#export-" + kind).click()
                    assert download.value.suggested_filename.endswith("." + kind)
                page.locator("#search").fill("nobody")
                page.locator("#people-rows").filter(has_text="No people").wait_for()
                page.locator("#clear-filters").click()
                page.get_by_role("button", name="Inspect", exact=True).wait_for()
                page.locator("#phone-only").check()
                page.get_by_role("button", name="Inspect", exact=True).wait_for()
                checks.append("contact evidence, phone filters and filtered exports")
                page.locator("#new-run").click()
                assert page.locator("#sources").input_value() == ""
                page.locator("#source-file").set_input_files(
                    {
                        "name": "valid.csv",
                        "mimeType": "text/csv",
                        "buffer": b"URL\nhttps://firm.com/old\n",
                    }
                )
                page.locator("#upload-status").filter(
                    has_text="1 source rows"
                ).wait_for()
                assert page.locator("#upload-preview").is_visible()
                page.locator("#sources").fill("https://firm.com/old-two")
                page.locator("[data-view=dashboard]").click()
                page.locator("#new-run").click()
                assert page.locator("#sources").input_value() == ""
                assert page.evaluate("uploaded.length") == 0
                assert page.locator("#source-file").input_value() == ""
                page.locator("#source-file").set_input_files(
                    {
                        "name": "invalid.csv",
                        "mimeType": "text/csv",
                        "buffer": b"URL\ninvalid\n",
                    }
                )
                page.locator("#notice").filter(has_text="Row 2").wait_for()
                assert page.evaluate("uploaded.length") == 0
                checks.append(
                    "fresh-run reset, URL-only upload preview and invalid file"
                )
                page.locator("#page-url").fill("https://firm.com/jane-smith")
                page.locator("#page-test-button").click()
                page.locator("#page-test-status").filter(has_text="1 people").wait_for()
                assert len(app.state.store.history()["items"]) == 1
                checks.append("single-page test does not save run")
                # A response from an older upload cannot restore a removed file.
                page.evaluate(
                    """()=>{window.originalFetch=window.fetch;window.pendingUploads=[];window.fetch=(url,options)=>url==='/api/sources/validate'?new Promise(resolve=>pendingUploads.push(resolve)):originalFetch(url,options);}"""
                )
                page.locator("#source-file").set_input_files(
                    {"name": "old.csv", "mimeType": "text/csv", "buffer": b"old"}
                )
                page.wait_for_function("()=>pendingUploads.length===1")
                page.locator("#source-file").set_input_files(
                    {"name": "new.csv", "mimeType": "text/csv", "buffer": b"new"}
                )
                page.wait_for_function("()=>pendingUploads.length===2")
                page.evaluate(
                    """()=>pendingUploads[1](new Response(JSON.stringify({sources:[{organization:'New',url:'https://new.example/'}]})))"""
                )
                page.wait_for_function(
                    "()=>uploaded.length===1&&uploaded[0].organization==='New'"
                )
                page.locator("#new-run").click()
                page.evaluate(
                    """()=>pendingUploads[0](new Response(JSON.stringify({sources:[{organization:'Old',url:'https://old.example/'}]})))"""
                )
                page.wait_for_timeout(50)
                assert page.evaluate("uploaded.length") == 0
                page.evaluate("window.fetch=window.originalFetch")
                checks.append("late upload response cannot restore removed sources")

                page.locator("#new-run").click()
                page.locator("#sources").fill("https://firm.com/slow")
                page.locator("#start-button").click()
                page.get_by_role("button", name="Pause", exact=True).click()
                page.locator("#run-summary .badge.paused").wait_for(timeout=15000)
                paused = float(page.locator(".crawl-progress").get_attribute("value"))
                page.wait_for_timeout(100)
                assert (
                    float(page.locator(".crawl-progress").get_attribute("value"))
                    == paused
                )
                page.get_by_role("button", name="Resume remaining pages").click()
                page.locator("#run-summary .badge.completed").wait_for(timeout=15000)
                checks.append("pause and resume real worker")
                page.get_by_role("button", name="View people", exact=True).click()
                assert (
                    page.locator("#search").input_value() == ""
                    and not page.locator("#phone-only").is_checked()
                )
                checks.append("selected-run directory clears stale filters")
                page.evaluate("(rid)=>openRun(rid)", rid)
                race = page.evaluate("""async()=>{
                    const original=window.fetch,base=await api('/api/runs/'+selectedRun);let pending=[];
                    window.fetch=url=>new Promise(resolve=>pending.push({url,resolve}));
                    selectedRun='old-run';const oldRequest=runDetail();selectedRun='new-run';const newRequest=runDetail();
                    const reply=(id,name)=>{for(const p of pending.filter(p=>p.url.includes(id)))p.resolve(new Response(JSON.stringify(p.url.includes('/pages')?[]:{...base,id,name})));};
                    reply('new-run','New run');await newRequest;reply('old-run','Old run');await oldRequest;window.fetch=original;
                    return {selected:selectedRun,shown:document.querySelector('#run-summary h2').textContent};
                }""")
                assert race == {"selected": "new-run", "shown": "New run"}
                page.evaluate("(id)=>openRun(id)", rid)
                checks.append("stale run responses ignored")
                # XSS stays text when inspecting untrusted extracted fields.
                page.evaluate(
                    """async()=>{const p=(await api('/api/directory')).items[0];p.name='<img src=x onerror="window.pwned=true">';p.reason='<script>window.pwned=true</script>';inspect(p);}"""
                )
                assert page.locator("#person-dialog img").count() == 0
                assert page.evaluate("window.pwned===undefined")
                page.locator("#close-dialog").click()
                checks.append("stored HTML rendered as inert text")
                for i in range(26):
                    fake = app.state.store.create(
                        RunConfig(
                            name=f"History {i}", sources=[{"url": "https://firm.com/"}]
                        )
                    )
                    app.state.store.set_status(fake, "completed")
                page.locator("[data-view=runs]").click()
                page.locator("#runs-next").click()
                page.locator("#runs-page").filter(has_text="Page 2").wait_for()
                page.locator("#runs-prev").click()
                page.on("dialog", lambda d: d.accept())
                page.get_by_role("button", name="Delete", exact=True).first.click()
                page.locator("#runs-page").filter(has_text="27 runs").wait_for()
                checks.append("history pagination and confirmed deletion")
                page.locator("[data-view=dashboard]").click()
                page.screenshot(path="/tmp/attorney-portal-desktop.png", full_page=True)
                page.locator("#new-run").click()
                page.screenshot(path="/tmp/attorney-portal-new.png", full_page=True)
                for size in [
                    {"width": 390, "height": 844},
                    {"width": 768, "height": 1024},
                ]:
                    page.set_viewport_size(size)
                    for view in ["dashboard", "new", "runs", "directory"]:
                        page.locator(f"[data-view={view}]").click()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth<=window.innerWidth"
                        ), (size, view)
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#new-run").click()
                file_box = page.locator(".file-label").bounding_box()
                remove_box = page.locator("#clear-upload").bounding_box()
                assert remove_box["y"] >= file_box["y"] + file_box["height"]
                page.screenshot(path="/tmp/attorney-portal-mobile.png", full_page=True)
                page.emulate_media(color_scheme="dark")
                page.screenshot(path="/tmp/attorney-portal-dark.png", full_page=True)
                checks.append("desktop, tablet, mobile and dark theme")
                assert not errors, errors
                checks.append("no JavaScript errors")
                browser.close()
                print(json.dumps({"passed": True, "checks": checks}, indent=2))
        finally:
            server.should_exit = True
            thread.join(10)


if __name__ == "__main__":
    main()
