"""Polite HTTP fetcher: robots.txt, per-host pacing, retries, size cap, optional browser render."""
from __future__ import annotations

import functools
import ipaddress
import logging
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import hashlib
import re

import httpx

from .config import ScrapeConfig
from .models import Page
from .robots import AllowAll, RobotsPolicy

log = logging.getLogger(__name__)

HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml")


CHALLENGE_RE = re.compile(r"cf-chl|challenge-platform|Just a moment\.\.\.|Attention Required|captcha|ddos-guard|"
                          r"Checking your browser|perimeterx|_Incapsula_", re.I)


class FetchBlocked(Exception):
    """Raised when robots.txt or the host check forbids a URL."""


def normalize_url(url: str) -> str:
    p = urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower().rstrip(".")
    port = f":{p.port}" if p.port and not ((scheme == "https" and p.port == 443) or (scheme == "http" and p.port == 80)) else ""
    path = re.sub(r"/{2,}", "/", p.path or "/")
    # Drop common tracking params, keep the rest (pagination lives in queries).
    query = "&".join(q for q in p.query.split("&") if q and not re.match(r"(utm_|fbclid|gclid|ref=|source=)", q))
    return urlunsplit((scheme, host + port, path, query, ""))


@functools.lru_cache(maxsize=512)
def _is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # let the HTTP request surface the DNS error
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
            return False
    return True


class Fetcher:
    def __init__(self, config: ScrapeConfig):
        self.cfg = config
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(config.timeout_seconds, connect=15.0),
            headers={
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            max_redirects=5,
        )
        self._robots: dict[str, RobotsPolicy] = {}
        self._next_time: dict[str, float] = {}
        self._lock = threading.Lock()
        self._renderer = None
        self.requests_made = 0

    def close(self) -> None:
        self.client.close()
        if self._renderer:
            self._renderer.close()

    # -- policy ------------------------------------------------------------
    def _origin(self, url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def robots_for(self, url: str) -> RobotsPolicy:
        origin = self._origin(url)
        if not self.cfg.respect_robots:
            return AllowAll()
        if origin in self._robots:
            return self._robots[origin]
        policy: RobotsPolicy = AllowAll()
        try:
            resp = self._raw_get(origin + "/robots.txt")
            if resp.status_code == 200 and "<html" not in resp.text[:500].lower():
                policy = RobotsPolicy(resp.text, self.cfg.user_agent)
            elif resp.status_code in (401, 403):
                # Site forbids robots.txt itself: treat as full disallow (RFC 9309).
                policy = RobotsPolicy("User-agent: *\nDisallow: /", self.cfg.user_agent)
        except httpx.HTTPError as exc:
            log.warning("robots.txt unavailable for %s: %s", origin, exc)
        self._robots[origin] = policy
        return policy

    def check_allowed(self, url: str) -> None:
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchBlocked(f"unsupported URL: {url}")
        if not self.cfg.allow_private_hosts and not _is_public(p.hostname):
            raise FetchBlocked(f"private or local address refused: {p.hostname}")
        if not self.robots_for(url).allowed(url):
            raise FetchBlocked("robots.txt disallows this URL")

    # -- transport ---------------------------------------------------------
    def _pace(self, url: str) -> None:
        host = urlsplit(url).netloc
        delay = max(self.cfg.delay_seconds, self._robots.get(self._origin(url), AllowAll()).crawl_delay)
        delay = min(delay, 30.0)
        with self._lock:
            now = time.monotonic()
            due = max(now, self._next_time.get(host, now))
            self._next_time[host] = due + delay
        if due > now:
            time.sleep(due - now)

    def _raw_get(self, url: str) -> httpx.Response:
        self._pace(url)
        self.requests_made += 1
        with self.client.stream("GET", url) as resp:
            body = bytearray()
            for chunk in resp.iter_bytes():
                body.extend(chunk)
                if len(body) > self.cfg.max_bytes:
                    break
            resp._content = bytes(body)  # noqa: SLF001 - httpx has no public setter
            return resp

    def get(self, url: str) -> Page:
        """Fetch one page. Never raises for HTTP errors; returns Page.error instead."""
        url = normalize_url(url)
        try:
            self.check_allowed(url)
        except FetchBlocked as exc:
            return Page(url, url, 0, "", error=str(exc))
        last_error = None
        for attempt in range(self.cfg.retries + 1):
            try:
                resp = self._raw_get(url)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(min(10, 2 ** attempt))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.cfg.retries:
                retry_after = resp.headers.get("retry-after", "")
                time.sleep(min(30, float(retry_after)) if retry_after.isdigit() else 2 ** (attempt + 1))
                continue
            final = normalize_url(str(resp.url))
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code in (403, 429, 503) and CHALLENGE_RE.search(resp.text[:20000]) and self.cfg.render != "never":
                # Bot-check interstitial: a real browser often passes it. Robots rules were already checked.
                rendered = self._render(Page(url, final, resp.status_code, resp.text, ctype or "text/html"))
                if rendered and not CHALLENGE_RE.search(rendered.html[:20000]):
                    rendered.status = 200
                    self._save(rendered)
                    return rendered
                return Page(url, final, resp.status_code, "", ctype, error=f"HTTP {resp.status_code} (bot check page)")
            if resp.status_code >= 400:
                return Page(url, final, resp.status_code, "", ctype, error=f"HTTP {resp.status_code}")
            if ctype and not ctype.startswith(HTML_TYPES):
                return Page(url, final, resp.status_code, "", ctype, error=f"skipped content-type {ctype}")
            if final != url and urlsplit(final).netloc != urlsplit(url).netloc:
                try:
                    self.check_allowed(final)
                except FetchBlocked as exc:
                    return Page(url, final, resp.status_code, "", ctype, error=f"redirect refused: {exc}")
            html = resp.text
            page = Page(url, final, resp.status_code, html, ctype or "text/html")
            if self._should_render(page):
                page = self._render(page) or page
            self._save(page)
            return page
        return Page(url, url, 0, "", error=last_error or "failed")

    # -- optional JavaScript rendering --------------------------------------
    def _should_render(self, page: Page) -> bool:
        if self.cfg.render == "never" or not page.content_type.startswith("text/html"):
            return False
        if self.cfg.render == "always":
            return True
        # auto: render when the HTML is an empty app shell.
        text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", page.html, flags=re.S | re.I)
        words = len(text.split())
        shell = re.search(r'id=["\'](?:root|app|__next|__nuxt)["\']', page.html) is not None
        return words < 80 or (shell and words < 250)

    def _render(self, page: Page) -> Page | None:
        if self._renderer is None:
            try:
                from .render import BrowserRenderer
                self._renderer = BrowserRenderer(self.cfg)
            except Exception as exc:  # Playwright missing or browser not installed
                log.info("JS rendering unavailable (%s); using raw HTML", exc)
                self.cfg.render = "never"
                return None
        try:
            self._pace(page.final_url)
            html = self._renderer.render(page.final_url)
            return Page(page.url, page.final_url, page.status, html, page.content_type, rendered=True)
        except Exception as exc:
            log.warning("render failed for %s: %s", page.final_url, exc)
            return None

    def _save(self, page: Page) -> None:
        if not self.cfg.save_html_dir or not page.html:
            return
        folder = Path(self.cfg.save_html_dir)
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", urlsplit(page.final_url).path).strip("-")[:80] or "index"
        digest = hashlib.sha1(page.final_url.encode()).hexdigest()[:8]
        (folder / f"{slug}-{digest}.html").write_text(
            f"<!-- source: {page.final_url} -->\n" + page.html, encoding="utf-8")
