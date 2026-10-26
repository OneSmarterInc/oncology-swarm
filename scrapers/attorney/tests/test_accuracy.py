"""Accuracy regressions from the user's three-firm export and contact markup."""

import pytest
from bs4 import BeautifulSoup

from attorneyscraper.extract import contacts, extract, link_priority


@pytest.mark.parametrize(
    "name,path",
    [
        ("California Privacy Notice", "/california-privacy-notice/"),
        ("Korea Practice", "/services/global-capabilities/korea-practice/"),
        (
            "Russia-Ukraine War Resource Center",
            "/services/russia-ukraine-war-resource-center/",
        ),
        ("Service Terms", "/service-terms/"),
        ("Tariff Resource Center", "/services/tariff-resource-center/"),
        ("Los Angeles", "/en/locations/united-states/los-angeles"),
        ("San Diego", "/en/locations/united-states/san-diego"),
        ("Woodland Hills", "/en/locations/united-states/woodland-hills"),
        ("Legal Notices", "/en/legal-notices"),
        ("External Publication", "/insights?type=External+Publication"),
        ("Global Guide Quarterly", "/insights?type=Global+Guide+Quarterly"),
        ("Press Release", "/insights?type=Press+Release"),
        ("Who We Are", "/about/who-we-are"),
        ("Workplace Training", "/products-services/workplace-training"),
        ("Request Information", "/information-request"),
        (
            "Ten Employment Issues This Labor Day",
            "/news-analysis/asap/ten-employment-issues-labor-day",
        ),
    ],
)
def test_non_person_pages_not_profiles(name, path):
    html = f'<h1>{name}</h1><p>Partner, attorney and counsel.</p><a href="mailto:jane@firm.com">Contact</a>'
    assert extract(html, "https://firm.com" + path)["records"] == []


def test_service_page_still_extracts_real_person_cards():
    html = '<h1>Korea Practice</h1><article><h2><a href="/professionals/jane-smith">Jane Smith</a></h2><p>Partner</p><a href="mailto:jsmith@firm.com">Email</a></article>'
    records = extract(html, "https://firm.com/services/korea-practice")["records"]
    assert [p["name"] for p in records] == ["Jane Smith"]
    assert records[0]["contacts"][0]["owner"] == "individual"


def test_profile_header_not_organization_and_phone_attribution():
    html = '<main><header><h1>Barbara D. Linney</h1><p>Partner</p><p>T +1.202.861.1611</p><a href="mailto:blinney@firm.com">Email</a></header><p>International trade practice.</p></main>'
    record = extract(html, "https://firm.com/professionals/barbara-d-linney/")[
        "records"
    ][0]
    assert {(c["kind"], c["owner"]) for c in record["contacts"]} == {
        ("email", "individual"),
        ("phone", "individual"),
    }


def test_mailto_cc_and_body_not_extracted_as_person_emails():
    node = BeautifulSoup(
        '<a href="mailto:jsmith@firm.com?cc=other@firm.com&amp;body=third@firm.com">Email</a>',
        "html.parser",
    )
    assert [
        c["value"] for c in contacts(node, "Jane Smith", "https://firm.com/jane-smith")
    ] == ["jsmith@firm.com"]


def test_discovery_prefers_directory_and_profiles_over_articles():
    assert link_priority("https://firm.com/people") > link_priority(
        "https://firm.com/people/jane-smith"
    )
    assert link_priority("https://firm.com/people/jane-smith") > link_priority(
        "https://firm.com/services/korea-practice", "Korea Practice"
    )
    assert (
        link_priority(
            "https://firm.com/california-privacy-notice", "California Privacy Notice"
        )
        == 0
    )


def test_unknown_capitalized_heading_without_person_evidence_rejected():
    assert not extract(
        "<h1>Innovation Center</h1><p>Welcome to our website.</p>", "https://firm.com/"
    )["records"]


def test_named_international_call_control_keeps_person_ownership():
    html = '<h1>Nicola James</h1><p>Partner</p><div><a href="mailto:njames@firm.com">njames@firm.com</a><div>Call Nicola<ul><li><a href="tel:+44 20 4570 8613">+44 20 4570 8613</a></li><li><a href="tel:+44 7917 957986">+44 7917 957986</a></li></ul></div></div>'
    record = extract(html, "https://firm.com/people/nicola-james")["records"][0]
    assert {(c["value"], c["owner"]) for c in record["contacts"]} == {
        ("njames@firm.com", "individual"),
        ("+442045708613", "individual"),
        ("+447917957986", "individual"),
    }


def test_support_phone_not_promoted_by_nearby_named_email():
    html = '<h1>Jane Smith</h1><p>Partner</p><div><a href="mailto:jsmith@firm.com">jsmith@firm.com</a><p>Assistant <a href="tel:2125552345">2125552345</a></p></div>'
    p = extract(html, "https://firm.com/people/jane-smith")["records"][0]
    assert (
        next(c for c in p["contacts"] if c["kind"] == "phone")["owner"]
        == "support_staff"
    )


def test_incidental_unassigned_occurrence_does_not_demote_owner(tmp_path):
    from attorneyscraper.models import RunConfig, Source
    from attorneyscraper.store import Store

    store = Store(tmp_path / "ownership.db")
    urls = ["https://firm.com/people/jane-smith", "https://firm.com/people/john-jones"]
    rid = store.create(RunConfig(sources=[Source(url=u) for u in urls]))
    for name, url in zip(["Jane Smith", "John Jones"], urls):
        result = extract(f"<h1>{name}</h1><p>Partner</p><p>jsmith@firm.com</p>", url)
        store.finish_page(store.next_page(rid, "firm.com"), result)
    jane = next(p for p in store.records(rid) if p["name"] == "Jane Smith")
    assert jane["contacts"][0]["owner"] == "individual"


def test_budget_reaches_profile_before_non_person_links(tmp_path):
    import httpx

    from attorneyscraper.engine import Manager
    from attorneyscraper.fetch import Fetcher
    from attorneyscraper.models import RunConfig, Source
    from attorneyscraper.store import Store

    visited = []

    def factory(**kwargs):
        def handler(request):
            visited.append(request.url.path)
            body = {
                "/robots.txt": "User-agent: *\nAllow: /",
                "/": '<h1>Welcome</h1><a href="/california-privacy-notice">California Privacy Notice</a><a href="/people">Our People</a>',
                "/people": '<a href="/people/jane-smith">Jane Smith</a>',
                "/people/jane-smith": "<h1>Jane Smith</h1><p>Partner</p><p>jsmith@firm.com</p>",
            }.get(request.url.path, "<h1>California Privacy Notice</h1>")
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})

        return Fetcher(
            delay=0,
            stop=kwargs.get("stop"),
            transport=httpx.MockTransport(handler),
            validate=lambda u: u,
        )

    store = Store(tmp_path / "priority.db")
    rid = store.create(
        RunConfig(sources=[Source(url="https://firm.com/")], max_pages=3, max_depth=2)
    )
    manager = Manager(store, factory)
    manager.start(rid)
    manager.thread.join(10)
    assert not manager.thread.is_alive()
    assert [p["name"] for p in store.records(rid)] == ["Jane Smith"]
    assert "/california-privacy-notice" not in visited
