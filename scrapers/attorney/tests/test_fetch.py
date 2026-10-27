import socket

import httpx
import pytest

from attorneyscraper.fetch import (
    MAX_BYTES,
    Fetcher,
    FetchError,
    Robots,
    Stopped,
    public_url,
)
from attorneyscraper.models import RunConfig, canonical_url


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "https://user:pass@example.com", "http://example.com:8000"],
)
def test_invalid_url(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_canonical_tracking():
    assert (
        canonical_url("https://EXAMPLE.com/person?utm_source=x&page=2#bio")
        == "https://example.com/person?page=2"
    )


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"]
)
def test_private_dns_blocked(monkeypatch, ip):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443))]
    )
    with pytest.raises(FetchError):
        public_url("https://example.com/")


def test_robots_specific_and_wildcards():
    r = Robots(
        "User-agent: *\nDisallow: /\nUser-agent: AttorneyResearchBot\nDisallow: /private*\nAllow: /private/public$\nCrawl-delay: 3"
    )
    assert r.allowed("https://a.com/team")
    assert not r.allowed("https://a.com/private/file")
    assert r.allowed("https://a.com/private/public")
    assert not r.allowed("https://a.com/private/public/child")
    assert r.delay == 3


def make(handler):
    return Fetcher(
        delay=0, transport=httpx.MockTransport(handler), validate=lambda u: u
    )


def test_robots_block_prevents_request():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(
            200,
            text="User-agent: *\nDisallow: /people",
            headers={"content-type": "text/plain"},
        )

    f = make(handler)
    with pytest.raises(FetchError, match="robots"):
        f.get("https://firm.com/people", "firm.com")
    assert seen == ["/robots.txt"]
    f.close()


def test_external_redirect_is_blocked():
    def handler(req):
        return (
            httpx.Response(404)
            if req.url.path == "/robots.txt"
            else httpx.Response(302, headers={"location": "http://127.0.0.1/"})
        )

    f = make(handler)
    with pytest.raises(FetchError, match="Off-site"):
        f.get("https://firm.com/person", "firm.com")
    f.close()


def test_robots_server_failure_closed():
    f = make(lambda req: httpx.Response(503))
    f.wait = lambda s: None
    with pytest.raises(FetchError, match="Robots unavailable"):
        f.get("https://firm.com/", "firm.com")
    f.close()


def test_size_limit():
    f = make(
        lambda req: (
            httpx.Response(404)
            if req.url.path == "/robots.txt"
            else httpx.Response(
                200,
                content=b"x" * (MAX_BYTES + 1),
                headers={"content-type": "text/html"},
            )
        )
    )
    with pytest.raises(FetchError, match="3 MB"):
        f.get("https://firm.com/", "firm.com")
    f.close()


def test_stop_interrupts_wait():
    f = make(lambda req: httpx.Response(200))
    f.stop.set()
    with pytest.raises(Stopped):
        f.wait(100)
    f.close()


def test_bad_configuration():
    with pytest.raises(ValueError):
        RunConfig(
            sources=[{"organization": "Firm", "url": "https://firm.com"}],
            parallel_sites=99,
        )


def test_robots_wildcards_have_no_exponential_backtracking():
    r = Robots("User-agent: *\nDisallow: /" + "*a" * 30 + "b$")
    assert r.allowed("https://firm.com/" + "a" * 100)


def test_robots_question_and_bracket_are_literal():
    r = Robots("User-agent: *\nDisallow: /find?q=x\nDisallow: /[a]")
    assert not r.allowed("https://firm.com/find?q=x")
    assert r.allowed("https://firm.com/findXq=x")
    assert not r.allowed("https://firm.com/[a]")
    assert r.allowed("https://firm.com/a")


def test_robots_forbidden_means_no_robots_file():
    # hccw.com answers 403 to /robots.txt and 200 to the page: RFC 9309 says crawl.
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(403)
        return httpx.Response(200, text="<main><h1>Our Attorneys</h1></main>", headers={"content-type": "text/html"})

    f = make(handler)
    f.wait = lambda s: None
    assert "Our Attorneys" in f.get("https://firm.com/attorneys/", "firm.com").text
    assert seen == ["/robots.txt", "/attorneys/"]
    f.close()


def test_requests_carry_browser_headers_and_robots_group_stays_named():
    from attorneyscraper.fetch import AGENT, HEADERS, Robots

    assert HEADERS["User-Agent"].startswith("Mozilla/5.0") and "Accept-Language" in HEADERS
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(404)

    f = make(handler)
    f.wait = lambda s: None
    with pytest.raises(FetchError, match="HTTP 404"):
        f.get("https://firm.com/", "firm.com")
    assert seen["user-agent"] == HEADERS["User-Agent"]
    # A site that names the bot in robots.txt is still obeyed.
    policy = Robots("User-agent: *\nAllow: /\nUser-agent: AttorneyResearchBot\nDisallow: /attorneys/")
    assert AGENT.startswith("AttorneyResearchBot") and not policy.allowed("https://firm.com/attorneys/x")
    f.close()


def test_dns_miss_is_retried_before_failing(monkeypatch):
    import socket as _socket

    from attorneyscraper.fetch import resolve

    calls = []

    def flaky(host, port, type=None):
        calls.append(host)
        if len(calls) < 3:
            raise _socket.gaierror(11001, "getaddrinfo failed")
        return [(None, None, None, None, ("162.159.134.42", port))]

    monkeypatch.setattr(_socket, "getaddrinfo", flaky)
    assert resolve("www.hccw.com", pause=0) == ["162.159.134.42"]
    assert len(calls) == 3
    calls.clear()

    def dead(host, port, type=None):
        calls.append(host)
        raise _socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(_socket, "getaddrinfo", dead)
    with pytest.raises(FetchError, match="DNS resolution failed"):
        resolve("nowhere.invalid", pause=0)
    assert len(calls) == 3


def test_dns_answer_is_reused_when_a_later_lookup_misses(monkeypatch):
    import socket as _socket

    from attorneyscraper import fetch as fetch_mod

    fetch_mod._dns_cache.clear()
    state = {"fail": False}

    def resolver(host, port, type=None):
        if state["fail"]:
            raise _socket.gaierror(11001, "getaddrinfo failed")
        return [(None, None, None, None, ("216.157.108.97", port))]

    monkeypatch.setattr(_socket, "getaddrinfo", resolver)
    assert fetch_mod.resolve("www.bricker.com", pause=0) == ["216.157.108.97"]
    state["fail"] = True  # the resolver starts missing mid-crawl
    assert fetch_mod.resolve("www.bricker.com", pause=0) == ["216.157.108.97"]
    # A host never resolved still fails, and a stale answer is not trusted forever.
    with pytest.raises(FetchError, match="DNS resolution failed"):
        fetch_mod.resolve("never.seen.invalid", pause=0)
    monkeypatch.setattr(fetch_mod.time, "monotonic", lambda: 10**6)
    with pytest.raises(FetchError, match="DNS resolution failed"):
        fetch_mod.resolve("www.bricker.com", pause=0)
    fetch_mod._dns_cache.clear()


class FakeRenderer:
    """Stands in for Chromium: the firewall lets a browser through."""

    def __init__(self, status=200, url="https://firm.com/attorneys/", html=None, ctype="text/html"):
        from attorneyscraper.render import BrowserResponse

        self.calls = []
        self._response = BrowserResponse(status, url, html if html is not None else
                                         "<main><h1>Our Attorneys</h1><p>Jane Smith, Attorney</p></main>", ctype)

    def fetch(self, url):
        self.calls.append(url)
        return self._response

    def close(self):
        pass


def test_firewall_403_falls_back_to_the_browser():
    # hallrender.com and hccw.com answer 403 to every non-browser client.
    f = make(lambda req: httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(403))
    f.wait = lambda s: None
    f.render = "auto"
    f._renderer = FakeRenderer()
    page = f.get("https://firm.com/attorneys/", "firm.com")
    assert page.status == 200 and "Our Attorneys" in page.text
    assert f._renderer.calls == ["https://firm.com/attorneys/"]
    f.close()


def test_browser_fallback_is_off_by_default_and_respects_robots():
    f = make(lambda req: httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(403))
    f.wait = lambda s: None
    f._renderer = FakeRenderer()
    with pytest.raises(FetchError, match="HTTP 403"):  # render="never"
        f.get("https://firm.com/attorneys/", "firm.com")
    assert f._renderer.calls == []
    f.close()

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /attorneys", headers={"content-type": "text/plain"})
        return httpx.Response(403)

    g = make(handler)
    g.wait = lambda s: None
    g.render = "auto"
    g._renderer = FakeRenderer()
    with pytest.raises(FetchError, match="Blocked by robots.txt"):
        g.get("https://firm.com/attorneys/", "firm.com")
    assert g._renderer.calls == []  # robots still decides, browser or not
    g.close()


def test_browser_off_site_redirect_is_blocked():
    f = make(lambda req: httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(403))
    f.wait = lambda s: None
    f.render = "auto"
    f._renderer = FakeRenderer(url="https://elsewhere.example/attorneys/")
    with pytest.raises(FetchError, match="Off-site redirect blocked"):
        f.get("https://firm.com/attorneys/", "firm.com")
    f.close()


def test_a_blocked_host_skips_the_doomed_plain_request_next_time():
    plain = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        plain.append(req.url.path)
        return httpx.Response(403)

    f = make(handler)
    f.wait = lambda s: None
    f.render = "auto"
    f._renderer = FakeRenderer()
    assert f.get("https://firm.com/attorneys/", "firm.com").status == 200
    assert plain == ["/attorneys/"]
    f._renderer._response.url = "https://firm.com/people/jane"
    assert f.get("https://firm.com/people/jane", "firm.com").status == 200
    assert plain == ["/attorneys/"]  # second page went straight to the browser
    assert f._renderer.calls == ["https://firm.com/attorneys/", "https://firm.com/people/jane"]
    f.close()
