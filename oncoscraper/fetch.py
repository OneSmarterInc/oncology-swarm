"""Polite HTTP fetcher: robots.txt, per-host pacing, retries, size cap, optional browser render."""
from __future__ import annotations

import ipaddress
import logging
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


def _ssl_verify():
    """Use the operating system's certificate store when possible.

    Windows then also fetches missing intermediate certificates (many hospital
    sites send an incomplete chain) and trusts antivirus / office proxy roots,
    the same way Chrome does. Falls back to httpx's default bundle.
    """
    try:
        import ssl

        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # truststore missing or unsupported
        return True

HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml")


CHALLENGE_RE = re.compile(r"cf-chl|challenge-platform|Just a moment\.\.\.|Checking your browser|"
                          r"_Incapsula_|ddos-guard|perimeterx|px-captcha", re.I)


BLOCK_CODES = (401, 403, 429, 503)


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


def _is_ssl_or_proxy(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(k in text for k in ("certificate", "ssl", "proxy", "tls", "handshake"))


def _is_public(host: str) -> bool:
    """Refuse localhost and literal private IPs only.

    Host names are not checked by their DNS answer: VPNs, antivirus and office
    DNS often map real hospital sites to private or reserved addresses
    (e.g. 198.18.x.x), which made every site look "local".
    """
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True  # a normal host name
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast)


class Fetcher:
    def __init__(self, config: ScrapeConfig):
        self.cfg = config
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(config.timeout_seconds, connect=15.0),
            headers={
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
            max_redirects=5,
            verify=_ssl_verify(),
        )
        self._robots: dict[str, RobotsPolicy] = {}
        self.robots_status: dict[str, str] = {}
        self._next_time: dict[str, float] = {}
        self._lock = threading.Lock()
        self._renderer = None
        self._renderer_error: str | None = None
        self.browser_hosts: set[str] = set()   # sites that only answer real browsers
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
        """robots.txt per RFC 9309: 2xx = obey rules, 4xx = no rules, 5xx/network = treat as disallow-all."""
        origin = self._origin(url)
        if not self.cfg.respect_robots:
            return AllowAll()
        if origin in self._robots:
            return self._robots[origin]
        policy: RobotsPolicy = AllowAll()
        try:
            resp = self._raw_get(origin + "/robots.txt")
            body = resp.text[:500].lower()
            if resp.status_code == 200 and "<html" not in body:
                policy = RobotsPolicy(resp.text, self.cfg.user_agent)
                self.robots_status[origin] = "200 (rules applied)"
            elif resp.status_code in BLOCK_CODES and self._browser_ok():
                # Firewall blocks plain HTTP clients; read robots.txt the way a browser would.
                b = self._browser_get(origin + "/robots.txt", expand=False)
                if b and b.status == 200 and "<html" not in b.text[:500].lower():
                    policy = RobotsPolicy(b.text, self.cfg.user_agent)
                    self.robots_status[origin] = f"{resp.status_code} to plain HTTP, 200 in browser (rules applied)"
                    self.browser_hosts.add(urlsplit(origin).netloc)
                elif b and b.status >= 500:
                    policy = RobotsPolicy("User-agent: *\nDisallow: /", self.cfg.user_agent)
                    self.robots_status[origin] = f"{b.status} in browser (server error, site treated as closed)"
                else:
                    self.robots_status[origin] = f"{resp.status_code} (browser got {b.status if b else 'nothing'}; no rules)"
            elif resp.status_code >= 500:
                policy = RobotsPolicy("User-agent: *\nDisallow: /", self.cfg.user_agent)
                self.robots_status[origin] = f"{resp.status_code} (server error, site treated as closed)"
            else:
                self.robots_status[origin] = f"{resp.status_code} (no usable robots.txt, no rules)"
        except httpx.HTTPError as exc:
            log.warning("robots.txt unavailable to plain HTTP for %s: %s", origin, exc)
            self.robots_status[origin] = f"unreachable to plain HTTP ({type(exc).__name__}), no rules"
            if self._browser_ok():
                # Plain HTTP cannot connect (certificate, proxy or firewall); read it the way Chrome does.
                b = self._browser_get(origin + "/robots.txt", expand=False)
                if b is not None:
                    self.browser_hosts.add(urlsplit(origin).netloc)
                    if b.status == 200 and "<html" not in b.text[:500].lower():
                        policy = RobotsPolicy(b.text, self.cfg.user_agent)
                        self.robots_status[origin] = "plain HTTP failed, 200 in browser (rules applied)"
                    elif b.status >= 500:
                        policy = RobotsPolicy("User-agent: *\nDisallow: /", self.cfg.user_agent)
                        self.robots_status[origin] = f"{b.status} in browser (server error, site treated as closed)"
                    else:
                        self.robots_status[origin] = f"plain HTTP failed, browser got {b.status} (no rules)"
        self._robots[origin] = policy
        return policy

    def check_allowed(self, url: str) -> None:
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchBlocked(f"unsupported URL: {url}")
        if not self.cfg.allow_private_hosts and not _is_public(p.hostname):
            raise FetchBlocked(f"private or local address refused: {p.hostname}")
        if not self.robots_for(url).allowed(url):
            status = self.robots_status.get(self._origin(url), "")
            raise FetchBlocked(f"robots.txt disallows this URL [robots.txt HTTP {status}]")

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
        if urlsplit(url).netloc in self.browser_hosts:
            return self._via_browser(url, url, 0, "")
        last_error = None
        for attempt in range(self.cfg.retries + 1):
            try:
                resp = self._raw_get(url)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                if _is_ssl_or_proxy(exc):
                    break  # retrying the same client will not help
                time.sleep(min(10, 2 ** attempt))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.cfg.retries:
                retry_after = resp.headers.get("retry-after", "")
                time.sleep(min(30, float(retry_after)) if retry_after.isdigit() else 2 ** (attempt + 1))
                continue
            final = normalize_url(str(resp.url))
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code in BLOCK_CODES or (400 <= resp.status_code < 500 and resp.status_code not in (404, 410)):
                # Firewall refuses non-browser clients (common on large hospital sites).
                return self._via_browser(url, final, resp.status_code, ctype)
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
        # Plain HTTP could not connect at all (certificate, proxy, firewall). Chrome often can.
        if self._browser_ok():
            page = self._via_browser(url, url, 0, "", reason=last_error)
            if not page.error:
                return page
            return Page(url, url, 0, "", error=f"plain HTTP: {last_error}; browser: {page.error}")
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

    def _browser_ok(self) -> bool:
        if self.cfg.render == "never":
            return False
        if self._renderer is None and self._renderer_error is None:
            try:
                from .render import BrowserRenderer
                self._renderer = BrowserRenderer(self.cfg)
            except Exception as exc:  # Playwright missing or browser not installed
                self._renderer_error = str(exc)
                log.warning("Browser unavailable: %s", exc)
        return self._renderer is not None

    def _browser_get(self, url: str, expand: bool = True):
        if not self._browser_ok():
            return None
        try:
            self._pace(url)
            self.requests_made += 1
            return self._renderer.fetch(url, expand=expand)
        except Exception as exc:
            log.warning("browser fetch failed for %s: %s", url, exc)
            return None

    def _via_browser(self, url: str, final: str, http_status: int, ctype: str, reason: str | None = None) -> Page:
        if not self._browser_ok():
            hint = (f"site blocks non-browser clients; install Playwright to fetch it: "
                    f"pip install -r requirements.txt && python -m playwright install chromium"
                    if self.cfg.render != "never" else "site blocks non-browser clients (browser disabled)")
            return Page(url, final, http_status, "", ctype, error=f"HTTP {http_status}: {hint}")
        b = self._browser_get(url)
        if b is None:
            return Page(url, final, http_status, "", ctype, error=f"HTTP {http_status}; browser fetch failed")
        if b.status >= 400 or CHALLENGE_RE.search(b.html[:20000]):
            return Page(url, normalize_url(b.url), b.status, "", b.content_type,
                        error=f"HTTP {b.status} even in a real browser (site blocks automated access)")
        if http_status or reason:
            self.browser_hosts.add(urlsplit(url).netloc)
        page = Page(url, normalize_url(b.url), b.status or 200, b.html, b.content_type or "text/html", rendered=True)
        self._save(page)
        return page

    def _render(self, page: Page) -> Page | None:
        b = self._browser_get(page.final_url)
        if b is None or b.status >= 400:
            return None
        return Page(page.url, page.final_url, page.status, b.html, page.content_type, rendered=True)

    def _save(self, page: Page) -> None:
        if not self.cfg.save_html_dir or not page.html:
            return
        folder = Path(self.cfg.save_html_dir)
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", urlsplit(page.final_url).path).strip("-")[:80] or "index"
        digest = hashlib.sha1(page.final_url.encode()).hexdigest()[:8]
        (folder / f"{slug}-{digest}.html").write_text(
            f"<!-- source: {page.final_url} -->\n" + page.html, encoding="utf-8")
