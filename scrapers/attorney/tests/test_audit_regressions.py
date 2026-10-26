"""Regression coverage for the 25 October audit findings."""

import io
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from attorneyscraper.engine import Manager
from attorneyscraper.export import workbook_bytes
from attorneyscraper.extract import extract, phone_values
from attorneyscraper.fetch import Fetcher, public_url
from attorneyscraper.models import RunConfig, Source, canonical_url
from attorneyscraper.store import Store
from attorneyscraper.web import create_app


def test_f01_navigation_links_are_discarded():
    result = extract(
        '<nav><a href="/attorneys">Attorneys</a></nav><main><p>Welcome to our legal practice, serving clients throughout the country.</p></main>',
        "https://firm.com/",
    )
    assert any(link["url"] == "https://firm.com/attorneys" for link in result["links"])


def test_f02_contiguous_phone_is_lost():
    assert phone_values("+12125552345") == {"+1 212-555-2345"}
    assert phone_values("212-555-2345")


def test_f03_valid_surname_is_rejected():
    result = extract(
        "<h1>Jane Lawyer</h1><p>Attorney advising hospitals on healthcare compliance.</p>",
        "https://firm.com/jane-lawyer",
    )
    assert result["records"][0]["name"] == "Jane Lawyer"


def test_f04_jsonld_invalid_name_does_not_crash():
    result = extract(
        '<h1>Jane Smith</h1><script type="application/ld+json">{"@type":"Person","name":null}</script>',
        "https://firm.com/jane-smith",
    )
    assert result["records"][0]["name"] == "Jane Smith"


def test_f05_sidebar_person_contacts_are_lost():
    result = extract(
        '<main><h1>Jane Smith</h1><p>Attorney advising hospitals on healthcare compliance.</p></main><aside><a href="mailto:jsmith@firm.com">Email Jane</a></aside>',
        "https://firm.com/jane-smith",
    )
    assert any(
        c["value"] == "jsmith@firm.com" for c in result["records"][0]["contacts"]
    )


def test_f06_cross_person_email_misattribution():
    result = extract(
        '<main><h1>Jane Smith</h1><p>Attorney advising hospitals on healthcare compliance.</p><section><h2>John Smith</h2><a href="mailto:john.smith@firm.com">Contact John Smith</a></section></main>',
        "https://firm.com/jane-smith",
    )
    assert not any(
        c["value"] == "john.smith@firm.com" and c["owner"] == "individual"
        for c in result["records"][0]["contacts"]
    )


def test_f07_person_schema_contacts_discarded_when_card_exists():
    result = extract(
        '<main><article class="attorney-card"><h2><a href="/jane-smith">Jane Smith</a></h2><p>Attorney, HIPAA compliance</p></article></main><script type="application/ld+json">{"@type":"Person","name":"Jane Smith","url":"/jane-smith","email":"jsmith@firm.com"}</script>',
        "https://firm.com/people",
    )
    assert len(result["records"]) == 1
    assert any(
        c["value"] == "jsmith@firm.com" for c in result["records"][0]["contacts"]
    )


def test_f08_resume_can_reduce_progress(tmp_path):
    store = Store(tmp_path / "p.db")
    rid = store.create(RunConfig(sources=[{"url": "https://firm.com/"}], max_pages=100))
    store.set_site(rid, "firm.com", "completed")
    before = store.get(rid)["progress"]["percent"]
    observed = []

    class Fake:
        def close(self):
            pass

    def factory(**kwargs):
        observed.append(store.get(rid)["progress"]["percent"])
        return Fake()

    # Resume starts every site again and sets its status to running first.
    with store.db() as db:
        db.execute("UPDATE pages SET status='done' WHERE run_id=?", (rid,))
    manager = Manager(store, factory)
    manager._site(
        rid,
        "firm.com",
        Source(url="https://firm.com/"),
        RunConfig(sources=[{"url": "https://firm.com/"}]),
    )
    assert before == 99 and observed == []
    assert store.get(rid)["progress"]["percent"] == 99


def test_f09_history_silently_hides_old_runs(tmp_path):
    store = Store(tmp_path / "h.db")
    config = RunConfig(sources=[{"url": "https://firm.com/"}])
    first = store.create(config)
    for _ in range(200):
        store.create(config)
    assert len(store.runs()) == 200
    assert first not in {r["id"] for r in store.runs()}
    assert store.get(first)
    assert store.history(200)["items"][0]["id"] == first
    assert store.history()["total"] == 201


def test_f10_second_process_recovery_changes_active_jobs(tmp_path):
    path = tmp_path / "shared.db"
    store = Store(path)
    rid = store.create(RunConfig(sources=[{"url": "https://firm.com/"}]))
    store.set_status(rid, "running")
    store.next_page(rid, "firm.com")
    other = Store(path)
    import pytest

    with store.owner:
        with pytest.raises(ValueError, match="already in use"):
            other.recover()
    assert store.get(rid)["status"] == "running"
    assert store.pages(rid)[0]["status"] == "fetching"


def test_f11_review_feature_remains_in_export():
    book = load_workbook(io.BytesIO(workbook_bytes([])))
    assert "Review history" not in book.sheetnames
    assert "Run audit" in book.sheetnames
    assert "Review status" not in [c.value for c in book["Attorneys"][1]]


def test_f12_malformed_origin_returns_500(tmp_path):
    with TestClient(
        create_app(tmp_path / "web.db"), raise_server_exceptions=False
    ) as client:
        assert (
            client.get("/api/health", headers={"Origin": "http://["}).status_code == 403
        )


def test_f13_mismatched_ports_rejected():
    import pytest

    for url in ["http://firm.com:443/path", "https://firm.com:80/path"]:
        with pytest.raises(ValueError):
            canonical_url(url)


def test_s01_connect_uses_only_validated_ip():
    import httpcore

    from attorneyscraper.transport import PublicBackend

    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch.object(
            httpcore.SyncBackend, "connect_tcp", return_value="stream"
        ) as connect,
    ):
        assert PublicBackend().connect_tcp("rebind.example", 443) == "stream"
        assert connect.call_args.args[0] == "8.8.8.8"


def test_f14_depth_limit_counts_irrelevant_external_links(tmp_path):
    def factory(**kwargs):
        def handler(req):
            text = (
                "User-agent: *\nAllow: /"
                if req.url.path == "/robots.txt"
                else '<main><h1>Jane Smith</h1><p>Attorney advising hospitals on healthcare regulatory compliance.</p><a href="https://unrelated.example/">External website</a></main>'
            )
            return httpx.Response(
                200,
                text=text,
                headers={
                    "content-type": "text/plain"
                    if req.url.path == "/robots.txt"
                    else "text/html"
                },
            )

        return Fetcher(
            delay=0,
            stop=kwargs.get("stop"),
            transport=httpx.MockTransport(handler),
            validate=lambda u: u,
        )

    store = Store(tmp_path / "depth.db")
    rid = store.create(
        RunConfig(sources=[{"url": "https://firm.com/jane-smith"}], max_depth=0)
    )
    manager = Manager(store, factory)
    manager.start(rid)
    manager.thread.join(5)
    assert store.get(rid)["status"] == "completed"
    assert store.get(rid)["sites"][0]["reason"] == "queue_exhausted"
    assert store.get(rid)["pages_queued"] == 0


def test_f15_location_of_first_seed_applied_to_other_seeds(tmp_path):
    def factory(**kwargs):
        def handler(req):
            text = (
                "User-agent: *\nAllow: /"
                if req.url.path == "/robots.txt"
                else "<h1>Jane Smith</h1><p>Attorney advising hospitals on healthcare compliance.</p>"
            )
            return httpx.Response(
                200,
                text=text,
                headers={
                    "content-type": "text/plain"
                    if req.url.path == "/robots.txt"
                    else "text/html"
                },
            )

        return Fetcher(
            delay=0,
            stop=kwargs.get("stop"),
            transport=httpx.MockTransport(handler),
            validate=lambda u: u,
        )

    store = Store(tmp_path / "city.db")
    rid = store.create(
        RunConfig(
            sources=[
                {"url": "https://firm.com/ny/jane-smith", "city": "New York"},
                {"url": "https://firm.com/la/jane-smith", "city": "Los Angeles"},
            ]
        )
    )
    manager = Manager(store, factory)
    manager.start(rid)
    manager.thread.join(5)
    records = store.records(rid)
    assert len(records) == 2
    assert {p["city"] for p in records} == {"New York", "Los Angeles"}


def test_security_basic_boundaries_hold(tmp_path):
    with TestClient(create_app(tmp_path / "security.db")) as client:
        assert (
            client.post(
                "/api/runs", json={"sources": [{"url": "https://firm.com"}]}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/session", headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/session", headers={"Host": "evil.example"}).status_code
            == 400
        )
        assert client.get("/static/../store.py").status_code == 404
        assert (
            client.get("/api/directory", params={"q": "' OR 1=1 --"}).json()["total"]
            == 0
        )
        assert (
            "frame-ancestors 'none'"
            in client.get("/").headers["content-security-policy"]
        )
    from attorneyscraper.export import safe

    assert safe('=HYPERLINK("bad")').startswith("'")
    import pytest

    from attorneyscraper.fetch import FetchError

    with patch(
        "attorneyscraper.fetch.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
    ):
        with pytest.raises(FetchError):
            public_url("https://private.example/")


def test_generic_section_heading_is_not_treated_as_another_person():
    result = extract(
        "<h1>Jane Smith</h1><section><h2>Professional Experience</h2><p>Attorney handling healthcare compliance.</p><p>jsmith@firm.com</p></section>",
        "https://firm.com/jane-smith",
    )
    assert result["records"][0]["status"] == "confirmed"
    assert any(
        c["value"] == "jsmith@firm.com" for c in result["records"][0]["contacts"]
    )


def test_middle_initial_email_still_attributed_without_surname_substring_matching():
    from attorneyscraper.extract import owned_email

    assert owned_email("jcmiller@firm.com", "Juliane C. Miller")
    assert not owned_email("john.smith@firm.com", "Jane Smith")
