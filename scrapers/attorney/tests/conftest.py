import httpx
import pytest

from attorneyscraper.fetch import Fetcher

SITE = {
    "/robots.txt": (
        "text/plain",
        "User-agent: *\nDisallow: /private\nSitemap: https://firm.com/sitemap.xml",
    ),
    "/": (
        "text/html",
        '<main><h1>Our Attorneys</h1><a href="/people/jane">Jane Smith</a><a href="/people/john">John Jones</a><a href="/private">Private</a></main>',
    ),
    "/people/jane": (
        "text/html",
        '<main><h1>Jane Smith</h1><h2>Attorney</h2><p>Jane advises hospitals on healthcare regulatory compliance and HIPAA.</p><p>Email <a href="mailto:jsmith@firm.com">Jane</a></p><p>Direct: 212-555-2345</p></main>',
    ),
    "/people/john": (
        "text/html",
        '<main><h1>John Jones</h1><h2>Attorney</h2><p>John defends hospitals in medical malpractice claims.</p><p>Email <a href="mailto:jjones@firm.com">John</a></p></main>',
    ),
    "/people/nurse": (
        "text/html",
        "<main><h1>Mary Brown</h1><h2>Attorney</h2><p>Mary is a registered nurse and attorney who handles healthcare transactions.</p><p>mbrown@firm.com</p></main>",
    ),
    "/sitemap.xml": (
        "application/xml",
        '<?xml version="1.0"?><urlset><url><loc>https://firm.com/people/nurse</loc></url></urlset>',
    ),
}


@pytest.fixture
def factory():
    def make(**kwargs):
        def handler(req):
            if req.url.path not in SITE:
                return httpx.Response(404)
            ct, text = SITE[req.url.path]
            return httpx.Response(200, text=text, headers={"content-type": ct})

        f = Fetcher(
            delay=0,
            stop=kwargs.get("stop"),
            transport=httpx.MockTransport(handler),
            validate=lambda u: u,
        )
        return f

    return make
