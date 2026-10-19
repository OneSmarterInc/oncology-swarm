"""Bounded, robots-aware HTTPS fetching with validated, pinned DNS addresses.

The standard-library transport deliberately ignores proxy environment variables.
There are no retries here: callers own durable attempt/budget accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import queue
from pathlib import Path
import re
import socket
import ssl
import threading
import time
import zlib
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit


class FetchError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str
    html: str
    retrieved_at: str
    content_hash: str
    synthetic: bool = False


# Bound unresolved OS DNS work: timed-out lookups cannot accumulate unbounded
# threads or prevent interpreter shutdown. Never use alternate resolvers.
_DNS_SLOTS = threading.BoundedSemaphore(4)

_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_HIDDEN = frozenset({"script", "style", "template", "noscript", "head"})


def hidden_element(tag: str, attrs: dict[str, str | None]) -> bool:
    style = re.sub(r"\s+", "", (attrs.get("style") or "").lower())
    return (tag in _HIDDEN or "hidden" in attrs
            or (attrs.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style or "visibility:hidden" in style)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        hidden = (self.stack[-1][1] if self.stack else False) or hidden_element(tag, dict(attrs))
        if tag not in _VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)


def visible_text(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


@dataclass(frozen=True)
class _Target:
    url: str
    host: str
    path: str


@dataclass(frozen=True)
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes


class _BoundedDecoder:
    """Decode a single HTTP content coding without an unbounded output allocation.

    zlib's max_length limits output *during* inflation, rather than checking a
    fully allocated decompression bomb. Both RFC zlib-wrapped deflate and common
    legacy raw-deflate responses are supported. Brotli is deliberately not
    advertised: its usual Python bindings have no bounded-output process API.
    """
    def __init__(self, encoding: str, limit: int):
        self.encoding = encoding.strip().lower()
        if self.encoding not in {"", "identity", "gzip", "deflate"}:
            raise FetchError("Unsupported response Content-Encoding (supported: gzip, deflate, identity)")
        self.limit = limit
        self.count = 0
        self.pending = b""
        self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if self.encoding == "gzip" else None

    def feed(self, chunk: bytes) -> bytes:
        if self.encoding in {"", "identity"}:
            output = chunk
        else:
            if self.decoder is None:
                self.pending += chunk
                if len(self.pending) < 2:
                    return b""
                chunk, self.pending = self.pending, b""
                cmf, flg = chunk[:2]
                wrapped = cmf & 15 == 8 and cmf >> 4 <= 7 and (cmf * 256 + flg) % 31 == 0
                self.decoder = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
            try:
                output = self.decoder.decompress(chunk, self.limit - self.count + 1)
            except zlib.error as exc:
                raise FetchError("Invalid compressed response") from exc
            if self.decoder.unused_data:
                # Do not silently ignore trailing junk or an uninspected second
                # gzip member. A server can send a single bounded gzip stream.
                raise FetchError("Compressed response contains trailing data or multiple members")
        self.count += len(output)
        if self.count > self.limit:
            raise FetchError("Decoded response exceeds byte limit")
        return output

    def finish(self):
        if self.encoding not in {"", "identity"} and (self.decoder is None or not self.decoder.eof):
            raise FetchError("Truncated compressed response")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect directly to an already-validated sockaddr; TLS verifies the host."""
    def __init__(self, host: str, address: tuple[int, tuple], timeout: float):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        family, sockaddr = self._address
        raw = socket.socket(family, socket.SOCK_STREAM)
        self.sock = raw  # Expose connect I/O to the per-request deadline watchdog.
        try:
            raw.settimeout(self.timeout)
            raw.connect(sockaddr)  # Numeric sockaddr; never resolve the hostname again.
            self.sock = self._context.wrap_socket(
                raw, server_hostname=self.host, do_handshake_on_connect=False)
            self.sock.do_handshake()  # Default context still verifies the certificate and hostname.
        except BaseException:
            if self.sock is not None:
                self.sock.close()
            raw.close()
            raise


_FIXTURES = {
    "https://demo.example.invalid/doctors/asha-rao": "asha-rao.html",
    "https://demo.example.invalid/doctors/vikram-sen": "vikram-sen.html",
}
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_TRANSITION_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32", "::ffff:0:0/96",
))


def _robots_path(value: str) -> str:
    # RFC 9309 comparison: decode percent-encoded unreserved ASCII only.
    value = quote(value, safe="/%*?$:@!&'()+,;=-._~")
    def normalize(match):
        number = int(match.group(0)[1:], 16)
        character = chr(number)
        if character.isascii() and (character.isalnum() or character in "-._~"):
            return character
        return f"%{number:02X}"
    return re.sub(r"%[0-9a-fA-F]{2}", normalize, value)


def _robots_match(pattern: str, path: str, anchored: bool) -> bool:
    # Literal segments with '*' only: no regex engine or backtracking DoS.
    segments = pattern.split("*")
    if len(segments) == 1:
        return path == pattern if anchored else path.startswith(pattern)
    if not path.startswith(segments[0]):
        return False
    position = len(segments[0])
    for segment in segments[1:-1]:
        found = path.find(segment, position)
        if found < 0:
            return False
        position = found + len(segment)
    last = segments[-1]
    if anchored:
        return path.endswith(last) and len(path) - len(last) >= position
    return path.find(last, position) >= 0


class _RobotsPolicy:
    """RFC-style group selection, wildcard/end-anchor and longest-match handling."""
    def __init__(self, content: str, user_agent: str):
        if len(content.splitlines()) > 10000:
            raise FetchError("robots.txt exceeds the directive limit")
        groups = []
        agents, rules, delays = [], [], []
        has_directive = False
        meaningful = False
        for raw_line in content.splitlines():
            line = raw_line.split("#", 1)[0].strip().lstrip("\ufeff")
            if not line:
                continue
            key, separator, value = line.partition(":")
            if not separator:
                raise FetchError("Malformed robots.txt directive")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not value:
                    raise FetchError("Empty robots.txt user agent")
                if has_directive:
                    groups.append((agents, rules, delays))
                    agents, rules, delays, has_directive = [], [], [], False
                agents.append(value.lower())
                meaningful = True
            elif key in {"allow", "disallow", "crawl-delay", "request-rate"}:
                if not agents:
                    raise FetchError("robots.txt rules precede a user-agent group")
                has_directive = True
                if key in {"allow", "disallow"}:
                    if value:
                        rules.append((key == "allow", _robots_path(value)))
                elif key == "crawl-delay":
                    try:
                        delay = float(value)
                        if delay < 0 or not delay < float("inf"):
                            raise ValueError()
                        delays.append(delay)
                    except ValueError as exc:
                        raise FetchError("Invalid robots.txt crawl delay") from exc
                elif key == "request-rate":
                    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", value)
                    if not match or int(match[1]) <= 0:
                        raise FetchError("Unsupported robots.txt request rate")
                    delays.append(int(match[2]) / int(match[1]))
            # Other records such as Sitemap are ignored, not fetched.
        if agents:
            groups.append((agents, rules, delays))
        if content.strip() and not meaningful:
            # Comment-only and Sitemap-only files contain no restrictions.
            noncomments = [line.split("#", 1)[0].strip() for line in content.splitlines()]
            if any(line and not line.lower().startswith("sitemap:") for line in noncomments):
                raise FetchError("robots.txt has no parseable groups")
        product = user_agent.split("/", 1)[0].lower()
        selected = []
        best = -1
        for group_agents, group_rules, group_delays in groups:
            specificity = max((0 if agent == "*" else len(agent)
                               for agent in group_agents if agent == "*" or agent in product), default=-1)
            if specificity > best:
                best, selected = specificity, [(group_rules, group_delays)]
            elif specificity == best and specificity >= 0:
                selected.append((group_rules, group_delays))
        self.rules = [rule for rules, _delays in selected for rule in rules] if best >= 0 else []
        self.delay = max((delay for _rules, delays in selected for delay in delays), default=0.0)

    def can_fetch(self, _user_agent: str, url: str) -> bool:
        parts = urlsplit(url)
        path = _robots_path(parts.path or "/")
        if parts.query:
            path += "?" + _robots_path(parts.query)
        matches = []
        for allow, rule in self.rules:
            anchored = rule.endswith("$")
            pattern = rule[:-1] if anchored else rule
            if _robots_match(pattern, path, anchored):
                # Count octets rather than percent-encoding spelling in specificity.
                length = len(re.sub(r"%[0-9A-F]{2}", "x", pattern.replace("*", "")))
                matches.append((length, allow))
        return max(matches, default=(0, True))[1]  # Allow wins an equal-specificity tie.


class SafeFetcher:
    def __init__(self, settings, reserve_request: Callable[[str], object], *,
                 pace_host: Callable[[str, float], object] | None = None):
        self.settings = settings
        self.reserve_request = reserve_request
        self.pace_host = pace_host
        self._allowed = frozenset(self._domain(d) for d in settings.allowed_domains)
        self._lock = threading.Lock()
        self._next_request: dict[str, float] = {}
        self._crawl_delay: dict[str, float] = {}
        self._robots: dict[str, tuple[float, _RobotsPolicy]] = {}
        self._origin_locks: dict[str, threading.RLock] = {}
        if settings.requests_per_minute <= 0 or settings.request_timeout_seconds <= 0:
            raise ValueError("Request rate and timeout must be positive")
        if settings.max_response_bytes <= 0 or settings.max_redirects < 0:
            raise ValueError("Response size must be positive and redirects nonnegative")
        if not settings.user_agent or any(ord(c) < 32 or ord(c) > 126 for c in settings.user_agent):
            raise ValueError("A valid identifying User-Agent is required")

    @staticmethod
    def _domain(domain: str) -> str:
        if not isinstance(domain, str) or not domain or domain != domain.strip():
            raise ValueError("Invalid allowlist domain")
        try:
            result = domain.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Invalid allowlist domain") from exc
        if len(result) > 253 or result.endswith(".") or not re.fullmatch(r"[a-z0-9.-]+", result):
            raise ValueError("Allowlist must contain exact domain names, without ports or wildcards")
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in result.split(".")):
            raise ValueError("Invalid allowlist domain")
        return result

    def _validate_url(self, url: str) -> _Target:
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise FetchError("Invalid or excessively long URL")
        if url != url.strip() or "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise FetchError("URL contains whitespace, control characters, or backslashes")
        try:
            parts = urlsplit(url)
            if parts.scheme.lower() != "https" or not parts.hostname:
                raise FetchError("Only HTTPS URLs are permitted")
            if parts.username is not None or parts.password is not None or parts.port not in (None, 443):
                raise FetchError("URL credentials and non-443 ports are forbidden")
            host = parts.hostname
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise FetchError("IP-literal URLs are forbidden")
            host = self._domain(host)
            if host not in self._allowed:
                raise FetchError("Domain is not in the exact allowlist")
            path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            path.encode("ascii")
        except (ValueError, UnicodeError) as exc:
            raise FetchError("Malformed URL") from exc
        return _Target(urlunsplit(("https", host, parts.path or "/", parts.query, "")), host, path)

    def normalize_url(self, url: str, *, same_host: str | None = None) -> str:
        """Validate a candidate without I/O; DNS is revalidated on every request."""
        target = self._validate_url(url)
        if same_host is not None and target.host != self._domain(same_host):
            raise FetchError("Cross-origin discovery URLs and redirects are forbidden")
        return target.url

    def _resolve(self, host: str) -> tuple[int, tuple]:
        slots = _DNS_SLOTS
        if not slots.acquire(blocking=False):
            raise FetchError('DNS resolver capacity busy', retryable=True)
        result = queue.Queue(maxsize=1)
        def lookup():
            try:
                result.put((True, socket.getaddrinfo(host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)))
            except Exception as exc:
                result.put((False, exc))
            finally:
                slots.release()
        try:
            threading.Thread(target=lookup, daemon=True, name='bounded-dns').start()
        except RuntimeError as exc:
            slots.release()
            raise FetchError('DNS resolver worker unavailable', retryable=True) from exc
        try:
            success, answers = result.get(timeout=self.settings.request_timeout_seconds)
        except queue.Empty as exc:
            raise FetchError('DNS resolution timed out', retryable=True) from exc
        if not success:
            if isinstance(answers, socket.gaierror):
                raise FetchError('DNS resolution failed', retryable=True) from answers
            raise answers
        if not answers:
            raise FetchError("DNS returned no addresses", retryable=True)
        validated = []
        for family, _kind, _protocol, _canonical, sockaddr in answers:
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise FetchError("DNS returned an unsupported address family")
            try:
                address = ipaddress.ip_address(sockaddr[0])
            except ValueError as exc:
                raise FetchError("DNS returned an invalid address") from exc
            if (not address.is_global or address.is_multicast or address.is_reserved
                    or address.is_private or address.is_loopback or address.is_link_local
                    or address.is_unspecified
                    or (address.version == 6 and (
                        address not in ipaddress.ip_network("2000::/3")
                        or any(address in net for net in _TRANSITION_NETWORKS)))):
                raise FetchError("DNS returned a non-public or transition address")
            # All A/AAAA answers must pass, even addresses we will not connect to.
            if family == socket.AF_INET6 and (len(sockaddr) < 4 or sockaddr[3] != 0):
                raise FetchError("Scoped IPv6 addresses are forbidden")
            validated.append((family, sockaddr))
        return validated[0]

    def _rate_limit(self, host: str):
        interval = max(60.0 / self.settings.requests_per_minute, self._crawl_delay.get(host, 0.0))
        if self.pace_host is not None:
            # Shared coordinator pacing must use the current interval after a
            # newly discovered robots delay, not an older next-request slot.
            self.pace_host(host, interval)
            return
        with self._lock:
            now = time.monotonic()
            due = max(now, self._next_request.get(host, now))
            self._next_request[host] = due + interval
        if due > now:
            time.sleep(due - now)

    def _request_once(self, target: _Target) -> _Response:
        address = self._resolve(target.host)
        self._rate_limit(target.host)
        # This durable reservation happens before EVERY request, including robots and hops.
        # Do not catch budget exceptions: the caller owns their terminal handling.
        self.reserve_request(target.url)
        connection = _PinnedHTTPSConnection(target.host, address, self.settings.request_timeout_seconds)
        deadline = time.monotonic() + self.settings.request_timeout_seconds
        timed_out = threading.Event()
        active = [None]

        def abort_at_deadline():
            timed_out.set()
            current_socket = active[0] if active[0] is not None else connection.sock
            if current_socket is not None:
                try:
                    current_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    current_socket.close()
                except OSError:
                    pass

        # Socket timeouts alone reset on reads: a slow header sender could otherwise
        # keep getresponse() alive forever. Closing its socket bounds wall-clock I/O.
        timer = threading.Timer(self.settings.request_timeout_seconds, abort_at_deadline)
        timer.daemon = True
        timer.start()
        try:
            connection.request("GET", target.path, headers={
                "Host": target.host, "User-Agent": getattr(self.settings, "identified_user_agent", self.settings.user_agent),
                "Accept": "text/html, application/xhtml+xml, application/xml;q=0.9, text/plain;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "close",
            })
            active_socket = connection.sock
            active[0] = active_socket
            if active_socket is not None:
                active_socket.settimeout(max(0.001, deadline - time.monotonic()))
            response = connection.getresponse()
            headers = {key.lower(): value.strip() for key, value in response.getheaders()}
            if not 200 <= response.status < 300:
                # Redirect/error bodies are never used. In particular a missing
                # robots file stays a 404 even if its error page has an unknown
                # content coding; close without reading or decompressing it.
                return _Response(response.status, headers, b"")
            encoding = headers.get("content-encoding", "identity").lower()
            decoder = _BoundedDecoder(encoding, self.settings.max_response_bytes)
            length = headers.get("content-length")
            if length is not None:
                try:
                    if int(length) < 0 or int(length) > self.settings.max_response_bytes:
                        raise FetchError("Response exceeds byte limit")
                except ValueError as exc:
                    raise FetchError("Invalid response Content-Length") from exc
            chunks = []
            count = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("Request timed out", retryable=True)
                if active_socket is not None:
                    active_socket.settimeout(remaining)
                chunk = response.read1(min(65536, self.settings.max_response_bytes - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                if count > self.settings.max_response_bytes:
                    raise FetchError("Response exceeds byte limit")
                chunks.append(decoder.feed(chunk))
            decoder.finish()
            if length is not None and count != int(length):
                raise FetchError("Incomplete response body", retryable=True)
            return _Response(response.status, headers, b"".join(chunks))
        except FetchError:
            raise
        except ssl.SSLCertVerificationError as exc:
            raise FetchError("TLS certificate verification failed") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise FetchError("Request timed out", retryable=True) from exc
        except (OSError, http.client.HTTPException) as exc:
            message = "Request timed out" if timed_out.is_set() else "HTTPS request failed"
            raise FetchError(message, retryable=True) from exc
        finally:
            timer.cancel()
            connection.close()

    @staticmethod
    def _decode(response: _Response, *, robots: bool = False, sitemap: bool = False) -> str:
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        allowed = ({"text/plain"} if robots else {"application/xml", "text/xml", "text/plain"}
                   if sitemap else {"text/html", "text/plain", "application/xhtml+xml"})
        if media not in allowed:
            raise FetchError("Unsupported or missing response Content-Type")
        charset_match = re.search(r"charset\s*=\s*[\"']?([^\s;\"']+)", response.headers.get("content-type", ""), re.I)
        charset = charset_match.group(1) if charset_match else "utf-8"
        try:
            return response.body.decode(charset, errors="replace")
        except (LookupError, UnicodeError) as exc:
            raise FetchError("Unsupported response character encoding") from exc

    @staticmethod
    def _check_status(response: _Response, context: str):
        if not 200 <= response.status < 300:
            raise FetchError(f"{context} returned HTTP {response.status}",
                             retryable=response.status == 429 or 500 <= response.status <= 599)

    def _robots_for(self, target: _Target) -> _RobotsPolicy:
        cached = self._robots.get(target.host)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        original_host = target.host
        current = self._validate_url(f"https://{target.host}/robots.txt")
        visited = set()
        for hop in range(self.settings.max_redirects + 1):
            if current.url in visited:
                raise FetchError("robots.txt redirect loop")
            visited.add(current.url)
            response = self._request_once(current)
            if response.status in _REDIRECTS:
                location = response.headers.get("location")
                if not location or hop >= self.settings.max_redirects:
                    raise FetchError("robots.txt redirect limit or missing Location")
                current = self._validate_url(urljoin(current.url, location))
                if current.host != original_host:
                    raise FetchError("Cross-origin robots.txt redirects are forbidden")
                continue
            # A missing robots file has no restrictions. Refuse 403, 429, 5xx,
            # malformed content, and all other unsuccessful responses.
            if response.status == 404:
                content = ""
            else:
                self._check_status(response, "robots.txt")
                content = self._decode(response, robots=True)
            if "\x00" in content or "<html" in content.lower():
                raise FetchError("Invalid robots.txt response")
            parser = _RobotsPolicy(content, self.settings.user_agent)
            delay = parser.delay
            if delay > 300:
                raise FetchError("robots.txt pacing exceeds the supported 300-second wait; fetch refused")
            self._crawl_delay[target.host] = float(delay)
            # Respect the newly discovered delay after robots.txt itself as well.
            with self._lock:
                self._next_request[target.host] = max(
                    self._next_request.get(target.host, 0), time.monotonic() + float(delay))
            self._robots[target.host] = (time.monotonic(), parser)
            return parser
        raise FetchError("robots.txt redirect limit")

    def _allowed_response(self, target: _Target) -> _Response:
        # Serialize each origin across policy lookup and request. A new crawl-delay
        # cannot be bypassed by another thread already waiting on an older rate slot.
        with self._lock:
            lock = self._origin_locks.setdefault(target.host, threading.RLock())
        with lock:
            robots = self._robots_for(target)
            if not robots.can_fetch(self.settings.user_agent, target.url):
                raise FetchError("robots.txt disallows this URL")
            return self._request_once(target)

    def fetch(self, url: str, *, same_host: str | None = None) -> FetchResult:
        if self.settings.mode == "fixture":
            filename = _FIXTURES.get(url)
            if filename is None:
                raise FetchError("Fixture mode accepts only the two bundled synthetic URLs")
            raw = (Path(__file__).resolve().parent.parent / "fixtures" / filename).read_bytes()
            if len(raw) > self.settings.max_response_bytes:
                raise FetchError("Fixture exceeds byte limit")
            html = raw.decode("utf-8")
            text = visible_text(html)
            if "SYNTHETIC" not in text:
                raise FetchError("Fixture is missing its SYNTHETIC marker")
            return FetchResult(url, text, html, datetime.now(timezone.utc).isoformat(),
                               hashlib.sha256(raw).hexdigest(), True)
        return self._fetch_live(url, same_host=same_host)

    def fetch_sitemap(self, url: str, *, same_host: str | None = None) -> FetchResult:
        """Fetch XML using exactly the page transport, robots, budget and pacing.

        XML is returned in ``text``. Parsing belongs to discovery. A gzip file
        envelope is supported in addition to HTTP Content-Encoding, with the
        same output cap at both layers; no unbounded gzip.decompress is used.
        """
        return self._fetch_live(url, same_host=same_host, sitemap=True)

    def _fetch_live(self, url: str, *, same_host: str | None = None, sitemap: bool = False) -> FetchResult:
        if self.settings.mode != "live":
            raise FetchError("This document requires live fetch mode")
        current = self._validate_url(self.normalize_url(url, same_host=same_host))
        visited = set()
        for hop in range(self.settings.max_redirects + 1):
            if current.url in visited:
                raise FetchError("Page redirect loop")
            visited.add(current.url)
            response = self._allowed_response(current)
            if response.status in _REDIRECTS:
                location = response.headers.get("location")
                if not location or hop >= self.settings.max_redirects:
                    raise FetchError("Page redirect limit or missing Location")
                current = self._validate_url(self.normalize_url(urljoin(current.url, location), same_host=same_host))
                continue
            self._check_status(response, "Page")
            if sitemap and response.body.startswith(b"\x1f\x8b"):
                decoder = _BoundedDecoder("gzip", self.settings.max_response_bytes)
                chunks = [decoder.feed(response.body[index:index + 65536])
                          for index in range(0, len(response.body), 65536)]
                decoder.finish()
                headers = dict(response.headers)
                headers["content-type"] = "application/xml"
                response = _Response(response.status, headers, b"".join(chunks))
            document = self._decode(response, sitemap=sitemap)
            is_html = (not sitemap and response.headers["content-type"].split(";", 1)[0].strip().lower()
                       in {"text/html", "application/xhtml+xml"})
            return FetchResult(current.url, visible_text(document) if is_html else document,
                               document if is_html else "", datetime.now(timezone.utc).isoformat(),
                               hashlib.sha256(response.body).hexdigest(), False)
        raise FetchError("Page redirect limit")
