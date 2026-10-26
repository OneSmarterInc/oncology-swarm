"""Registry-first seeding for the batch path.

Search responses are stubbed, so these check portal behaviour rather than live
search quality. The cases that matter are the refusals: a doctor portal must
never become a source, a prompt that resolves nothing must fail rather than
start an empty batch, and two seeds on one host must not both be crawled.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("seedsources", reason="shared seedsources package not installed")

from oncoscraper.seed_discovery import seeds_to_csv  # noqa: E402
from oncoscraper.web import app  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    import resolve_websites
    import seedsources.adapters as adapters

    original = adapters.SearchSource.__init__
    monkeypatch.setattr(
        adapters.SearchSource,
        "__init__",
        lambda self, searcher=None, scorer=None, **kw: original(
            self, searcher=resolve_websites.ddgs_search, scorer=resolve_websites.pick, **kw
        ),
    )

    http = TestClient(app)

    def stub(urls):
        monkeypatch.setattr(
            resolve_websites, "ddgs_search", lambda q, max_results=8, attempts=3: list(urls)
        )

    http.stub = stub
    return http


def test_endpoints_registered(client):
    assert getattr(app.state, "seed_error", None) is None


def test_prompt_is_read_without_a_model(client):
    client.stub(["https://www.rgcirc.org/"])
    body = client.post(
        "/api/seeds/preview",
        json={"prompt": "oncologists from hospitals in Mumbai", "delay": 0},
    ).json()
    assert body["understood"]["vertical"] == "hospitals"
    assert body["understood"]["location"] == "Mumbai"
    assert body["understood"]["discipline"] == "oncology"
    assert body["understood"]["target"] == "people"


def test_doctor_portals_are_never_sources(client):
    client.stub(
        [
            "https://www.practo.com/mumbai",
            "https://www.vaidam.com/oncology",
            "https://www.rgcirc.org/",
        ]
    )
    body = client.post(
        "/api/seeds/preview",
        json={"prompt": "oncologists from hospitals in Mumbai", "delay": 0},
    ).json()
    assert {s["url"] for s in body["seeds"]} == {"https://www.rgcirc.org/"}


def test_all_aggregators_refuses_to_start(client):
    client.stub(["https://www.practo.com/a", "https://www.lybrate.com/b"])
    response = client.post(
        "/api/seeds/run", json={"prompt": "oncologists from hospitals in Mumbai", "delay": 0}
    )
    assert response.status_code == 422
    assert "No seed produced a URL" in response.json()["detail"]


def test_csv_matches_the_upload_template():
    csv_text = seeds_to_csv(
        [
            {"name": "Rajiv Gandhi Cancer Institute", "url": "https://www.rgcirc.org/", "location": "Delhi"},
            {"name": "No Website Hospital", "url": None, "location": "Pune"},
        ]
    )
    header, row = csv_text.strip().splitlines()[:2]
    assert header == "Hospital Name,Official Website,City,Enabled"
    assert row.startswith("Rajiv Gandhi Cancer Institute,https://www.rgcirc.org/,Delhi")
    assert "No Website Hospital" not in csv_text


def test_one_row_per_host():
    csv_text = seeds_to_csv(
        [
            {"name": "Max Saket", "url": "https://www.maxhealthcare.in/", "location": "Delhi"},
            {"name": "Max Mohali", "url": "https://maxhealthcare.in/branches", "location": "Mohali"},
            {"name": "RGCI", "url": "https://www.rgcirc.org/", "location": "Delhi"},
        ]
    )
    assert len(csv_text.strip().splitlines()) == 3  # header plus two sites
    assert "Max Mohali" not in csv_text
