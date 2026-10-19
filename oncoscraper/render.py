"""Optional headless-browser rendering for JavaScript-built pages.

Install once:  pip install playwright  &&  python -m playwright install chromium
Without it the scraper still works on server-rendered HTML and embedded JSON.
"""
from __future__ import annotations

from .config import ScrapeConfig


class BrowserRenderer:
    def __init__(self, cfg: ScrapeConfig):
        from playwright.sync_api import sync_playwright  # imported lazily

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=cfg.user_agent, locale="en-IN")
        self._timeout = int(cfg.timeout_seconds * 1000)

    def render(self, url: str) -> str:
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            # Click common "load more" buttons a few times to expose more doctors.
            for _ in range(5):
                button = page.locator("text=/^(load|view|show) more/i").first
                if button.count() == 0 or not button.is_visible():
                    break
                button.click()
                page.wait_for_timeout(1500)
            return page.content()
        finally:
            page.close()

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()
