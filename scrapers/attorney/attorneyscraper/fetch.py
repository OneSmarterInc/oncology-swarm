"""Bounded public HTTP fetching, origin pacing and conservative robots enforcement."""

from __future__ import annotations

import fnmatch
import ipaddress
import math
import re
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .models import canonical_url, site_key
from .transport import PublicTransport

# The robots.txt group this crawler answers to ("User-agent: AttorneyResearchBot").
AGENT = "AttorneyResearchBot/0.1"
# Request headers: the same browser identity the onco and campus scrapers send. Firm sites
# behind a WAF answer 403 to a bare bot token while serving the identical public page to a browser.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_BYTES = 3_000_000


class FetchError(Exception):
    pass


class Stopped(FetchError):
    pass


_dns_cache: dict[tuple[str, int], tuple[float, list[str]]] = {}
_dns_lock = threading.Lock()
DNS_TTL = 900.0  # seconds a good answer is trusted after a later lookup fails


def resolve(host: str, port: int = 443, attempts: int = 3, pause: float = 0.5) -> list[str]:
    """Addresses for ``host``.

    Consumer and office resolvers miss intermittently: www.bricker.com answered
    NXDOMAIN once in the middle of a crawl that had already fetched a hundred of
    its pages. A miss is retried, and an answer that worked within DNS_TTL is
    reused rather than letting one bad reply end the site.
    """
    key = (host, port)
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(a[4][0] for a in answers))
            if addresses:
                with _dns_lock:
                    _dns_cache[key] = (time.monotonic(), addresses)
                return addresses
        except OSError as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(pause * (attempt + 1))
    with _dns_lock:
        cached = _dns_cache.get(key)
    if cached and time.monotonic() - cached[0] < DNS_TTL:
        return cached[1]
    raise FetchError(f"DNS resolution failed ({getattr(last, 'errno', None) or last})")


def public_url(url: str):
    url = canonical_url(url)
    host = urlsplit(url).hostname
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise FetchError("Private/local hosts are not allowed")
    addresses = set(resolve(host))
    if not addresses or any(
        (not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast)
        for a in addresses
    ):
        raise FetchError("Host resolves to a non-public address")
    return url


class Robots:
    def __init__(self, text: str):
        groups, agents, rules, delays = [], [], [], []
        self.sitemaps = []
        started = False
        for line in text.splitlines()[:20000]:
            line = line.split("#", 1)[0].strip().lstrip("\ufeff")
            if ":" not in line:
                continue
            key, value = [x.strip() for x in line.split(":", 1)]
            key = key.lower()
            if key == "user-agent":
                if started:
                    groups.append((agents, rules, delays))
                    agents, rules, delays, started = [], [], [], False
                agents.append(value.lower())
            elif key in {"allow", "disallow"}:
                started = True
                if value:
                    rules.append((value, key == "allow"))
            elif key == "crawl-delay":
                started = True
                try:
                    d = float(value)
                    if math.isfinite(d) and d >= 0:
                        delays.append(d)
                except ValueError:
                    pass
            elif key == "sitemap":
                self.sitemaps.append(value)
        groups.append((agents, rules, delays))
        scored = [
            (
                max(
                    (
                        0 if a == "*" else len(a)
                        for a in aa
                        if a == "*" or a in AGENT.lower()
                    ),
                    default=-1,
                ),
                rr,
                dd,
            )
            for aa, rr, dd in groups
        ]
        best = max((x[0] for x in scored), default=-1)
        self.rules = [r for s, rr, _ in scored if s == best and s >= 0 for r in rr]
        self.matchers = []
        for rule, allow in self.rules:
            # fnmatch uses bounded/atomic wildcard matching, avoiding repeated .* backtracking.
            literal = rule[:-1] if rule.endswith("$") else rule + "*"
            literal = literal.replace("[", "[[]").replace("?", "[?]")
            self.matchers.append(
                (
                    re.compile(fnmatch.translate(literal)),
                    len(rule.replace("*", "")),
                    allow,
                )
            )
        self.delay = max(
            (d for s, _, dd in scored if s == best and s >= 0 for d in dd), default=0
        )

    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        path = p.path + ("?" + p.query if p.query else "")
        matches = []
        for pattern, weight, allow in self.matchers:
            if pattern.match(path) or pattern.match(unquote(path)):
                matches.append((weight, allow))
        return max(matches)[1] if matches else True


@dataclass
class Page:
    url: str
    status: int
    text: str
    content_type: str


BLOCK_CODES = {401, 403, 429, 503}


class Fetcher:
    def __init__(self, delay=1.5, stop=None, transport=None, validate=public_url, render="never"):
        self.delay = delay
        self.stop = stop or threading.Event()
        self.validate = validate
        # "never" (the default, and what the tests use), "auto" = browser only after a
        # firewall block, "always" = every page through the browser.
        self.render = render
        self._renderer = None
        self._renderer_error: str | None = None
        self.client = httpx.Client(
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=False,
            headers=HEADERS,
            transport=transport if transport is not None else PublicTransport(),
            trust_env=False,
        )
        self.policies, self.last = {}, {}

    def close(self):
        self.client.close()
        if self._renderer is not None:
            try:
                self._renderer.close()
            finally:
                self._renderer = None

    def browser_ready(self) -> bool:
        if self.render == "never":
            return False
        if self._renderer is None and self._renderer_error is None:
            try:
                from .render import BrowserRenderer

                self._renderer = BrowserRenderer()
            except Exception as exc:  # BrowserUnavailable, or no display
                self._renderer_error = str(exc)
        return self._renderer is not None

    def browser_get(self, url, allowed_site) -> Page | None:
        """Fetch one URL through Chromium. The same URL and redirect rules apply."""
        if not self.browser_ready():
            return None
        self.validate(url)
        try:
            result = self._renderer.fetch(url)
        except Exception as exc:
            raise FetchError(f"Browser fetch failed: {type(exc).__name__}") from exc
        if site_key(result.url) != allowed_site:
            raise FetchError("Off-site redirect blocked")
        return Page(result.url, result.status, result.text, result.content_type)

    def wait(self, seconds):
        if self.stop.wait(max(0, seconds)):
            raise Stopped("Run stopped")

    def _raw(self, url, allowed_site, robots=False):
        for _ in range(6):
            self.validate(url)
            if site_key(url) != allowed_site:
                raise FetchError("Off-site redirect blocked")
            origin = urlsplit(url).netloc
            delay = max(self.delay, self.policies.get(origin, Robots("")).delay)
            self.wait(self.last.get(origin, 0) + delay - time.monotonic())
            self.last[origin] = time.monotonic()
            for attempt in range(3):
                try:
                    with self.client.stream("GET", url) as response:
                        status = response.status_code
                        if status in {429, 500, 502, 503, 504} and attempt < 2:
                            retry = response.headers.get("retry-after", "")
                            # Long Retry-After is surfaced rather than shortened and violated.
                            if retry.isdigit() and int(retry) > 60:
                                raise FetchError(
                                    "Server requested a long retry delay; retry this run later"
                                )
                            self.wait(
                                max(
                                    delay, int(retry) if retry.isdigit() else 2**attempt
                                )
                            )
                            self.last[origin] = time.monotonic()
                            continue
                        if status in {301, 302, 303, 307, 308}:
                            target = canonical_url(
                                urljoin(url, response.headers.get("location", ""))
                            )
                            if not robots and not self.policy(
                                target, allowed_site
                            ).allowed(target):
                                raise FetchError(
                                    "Redirect target blocked by robots.txt"
                                )
                            url = target
                            break
                        data = bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if self.stop.is_set():
                                raise Stopped("Run stopped")
                            data.extend(chunk)
                            if len(data) > MAX_BYTES:
                                raise FetchError("Page exceeds 3 MB limit")
                        encoding = response.encoding or "utf-8"
                        return Page(
                            url,
                            status,
                            data.decode(encoding, errors="replace"),
                            response.headers.get("content-type", ""),
                        )
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    if attempt == 2:
                        raise FetchError(type(e).__name__) from e
                    self.wait(max(delay, 2**attempt))
            else:
                raise FetchError("Retry limit reached")
        raise FetchError("Too many redirects")

    def policy(self, url, allowed_site):
        p = urlsplit(url)
        if site_key(url) != allowed_site:
            raise FetchError("Off-site request blocked")
        if p.netloc not in self.policies:
            robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
            response = self._raw(robots_url, allowed_site, robots=True)
            if response.status in BLOCK_CODES and self.render != "never":
                response = self.browser_get(robots_url, allowed_site) or response
            if 400 <= response.status < 500 and response.status != 429:
                # RFC 9309 2.3.1.3: a 4xx robots.txt means no robots file; crawling is permitted.
                policy = Robots("")
            elif response.status == 200 and "<html" not in response.text[:200].lower():
                policy = Robots(response.text)
            else:
                raise FetchError(
                    f"Robots unavailable (HTTP {response.status}); crawl deferred"
                )
            self.policies[p.netloc] = policy
        return self.policies[p.netloc]

    def get(self, url, allowed_site):
        policy = self.policy(url, allowed_site)
        if not policy.allowed(url):
            raise FetchError("Blocked by robots.txt")
        if self.render == "always" and self.browser_ready():
            page = self.browser_get(url, allowed_site) or self._raw(url, allowed_site)
        else:
            page = self._raw(url, allowed_site)
            if page.status in BLOCK_CODES and self.render != "never":
                # hallrender.com and hccw.com refuse any non-browser client; the same
                # public page loads in Chromium. robots.txt was already honoured above.
                page = self.browser_get(url, allowed_site) or page
        if page.status != 200:
            raise FetchError(f"HTTP {page.status}")
        if not any(
            x in page.content_type
            for x in ("text/html", "application/xhtml", "xml", "text/plain")
        ):
            raise FetchError("Unsupported content type: " + page.content_type[:80])
        return page
