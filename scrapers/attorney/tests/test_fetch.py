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
