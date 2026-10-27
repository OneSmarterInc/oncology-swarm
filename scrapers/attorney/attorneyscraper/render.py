"""Real-browser fetching with Playwright (Chromium), for sites whose firewall
answers 401/403/429/503 to any non-browser client.

hallrender.com and hccw.com serve their public attorney directories to a browser
and refuse the same URL over plain HTTP, whatever User-Agent is sent, because the
block is on the TLS/HTTP2 fingerprint rather than the header. One browser context
is kept for the whole run so a cookie the firewall sets is reused.

Install once:  pip install -r requirements.txt  &&  python -m playwright install chromium
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

CHALLENGE = re.compile(
    r"cf-chl|challenge-platform|Just a moment\.\.\.|Checking your browser|"
    r"_Incapsula_|ddos-guard|perimeterx|px-captcha",
    re.I,
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BLOCK_CODES = {401, 403, 429, 503}
MAX_CHARS = 3_000_000


class BrowserUnavailable(RuntimeError):
    pass


@dataclass
class BrowserResponse:
    status: int
    url: str
    text: str
    content_type: str


def has_content(html: str) -> bool:
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S | re.I)
    return len(text.split()) > 60


class BrowserRenderer:
    """Chromium, started once per run and only when a site actually blocks us."""

    def __init__(self, timeout_seconds: float = 20.0, headless: bool = True):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - import guard
            raise BrowserUnavailable(
                "Playwright is not installed. Run: pip install -r requirements.txt "
                "and then: python -m playwright install chromium"
            ) from exc
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        try:
            self._browser = self._pw.chromium.launch(headless=headless, channel="chromium", args=args)
        except Exception:
            try:
                self._browser = self._pw.chromium.launch(headless=headless, args=args)
            except Exception as exc:
                self._pw.stop()
                raise BrowserUnavailable(
                    f"Chromium could not start ({exc}). Run: python -m playwright install chromium"
                ) from exc
        self._context = self._browser.new_context(
            user_agent=BROWSER_UA,
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        # Page text is all that is needed; skip images, fonts and media.
        self._context.route(
            re.compile(r"\.(?:png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf|mp4|webm|mp3)(?:\?|$)", re.I),
            lambda route: route.abort(),
        )
        self._timeout = int(timeout_seconds * 1000)

    def fetch(self, url: str) -> BrowserResponse:
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
            status = response.status if response else 0
            ctype = (response.headers.get("content-type", "") if response else "").split(";")[0].strip().lower()
            if ctype.startswith("text/plain") or url.endswith("robots.txt"):
                body = response.text() if response else ""
                return BrowserResponse(status, page.url, body[:MAX_CHARS], ctype or "text/plain")
            # A firewall interstitial usually clears itself within a few seconds.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and CHALLENGE.search(page.content()[:30000]):
                page.wait_for_timeout(1000)
                status = 200
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            html = page.content()
            if status in BLOCK_CODES and not CHALLENGE.search(html[:30000]) and has_content(html):
                status = 200  # the interstitial passed; this is the real document
            return BrowserResponse(status, page.url, html[:MAX_CHARS], ctype or "text/html")
        finally:
            page.close()

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._pw.stop()
