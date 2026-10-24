"""Bounded public HTTP fetching, origin pacing and conservative robots enforcement."""

from __future__ import annotations

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

AGENT = "AttorneyResearchBot/0.1"
MAX_BYTES = 3_000_000


class FetchError(Exception):
    pass


class Stopped(FetchError):
    pass


def public_url(url: str):
    url = canonical_url(url)
    host = urlsplit(url).hostname
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise FetchError("Private/local hosts are not allowed")
    try:
        addresses = {
            a[4][0] for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except OSError as e:
        raise FetchError("DNS resolution failed") from e
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
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
        self.delay = max(
            (d for s, _, dd in scored if s == best and s >= 0 for d in dd), default=0
        )

    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        path = p.path + ("?" + p.query if p.query else "")
        matches = []
        for rule, allow in self.rules:
            pattern = (
                "^"
                + re.escape(rule.rstrip("$")).replace(r"\*", ".*")
                + ("$" if rule.endswith("$") else "")
            )
            if re.search(pattern, path) or re.search(pattern, unquote(path)):
                matches.append((len(rule.replace("*", "")), allow))
        return max(matches)[1] if matches else True


@dataclass
class Page:
    url: str
    status: int
    text: str
    content_type: str


class Fetcher:
    def __init__(self, delay=1.5, stop=None, transport=None, validate=public_url):
        self.delay = delay
        self.stop = stop or threading.Event()
        self.validate = validate
        self.client = httpx.Client(
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=False,
            headers={"User-Agent": AGENT},
            transport=transport,
            trust_env=transport is None,
        )
        self.policies, self.last = {}, {}

    def close(self):
        self.client.close()

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
                        for chunk in response.iter_bytes():
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
            response = self._raw(
                f"{p.scheme}://{p.netloc}/robots.txt", allowed_site, robots=True
            )
            if response.status in {404, 410}:
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
        page = self._raw(url, allowed_site)
        if page.status != 200:
            raise FetchError(f"HTTP {page.status}")
        if not any(
            x in page.content_type
            for x in ("text/html", "application/xhtml", "xml", "text/plain")
        ):
            raise FetchError("Unsupported content type: " + page.content_type[:80])
        return page
