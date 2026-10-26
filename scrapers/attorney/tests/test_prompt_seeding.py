"""Prompt-driven seeding.

Search responses are stubbed, so these check the portal's behaviour rather than
live search quality. The cases that matter are the refusals: a directory must
never become a seed, and a prompt that produces nothing must fail loudly rather
than start an empty run.
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

from attorneyscraper.web import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    import resolve_websites
    import seedsources.adapters as adapters

    def stub(urls):
        monkeypatch.setattr(
            resolve_websites,
            "ddgs_search",
            lambda q, max_results=8, attempts=3: list(urls),
        )

    original = adapters.SearchSource.__init__
    monkeypatch.setattr(
        adapters.SearchSource,
        "__init__",
        lambda self, searcher=None, scorer=None, **kw: original(
            self,
            searcher=resolve_websites.ddgs_search,
            scorer=resolve_websites.pick,
            **kw,
        ),
    )

    app = create_app(db_path=str(tmp_path / "t.db"))
    with TestClient(app) as http:
        token = http.get("/api/session").json()["token"]
        http.headers.update({"x-session-token": token})
        http.stub = stub
        yield http


def test_endpoints_are_registered(client):
    assert client.app.state.__dict__.get("prompt_error") is None


def test_prompt_is_read_without_a_model(client):
    client.stub(["https://hallrender.com/attorneys/"])
    body = client.post(
        "/api/prompt/preview",
        json={"prompt": "healthcare law firms in Ohio", "delay": 0},
    ).json()
    assert body["understood"]["vertical"] == "lawyers"
    assert body["understood"]["location"] == "Ohio"
    assert body["understood"]["discipline"] == "healthcare"


def test_directories_never_become_seeds(client):
    client.stub(
        [
            "https://www.justia.com/lawyers",
            "https://www.avvo.com/ohio",
            "https://hallrender.com/attorneys/",
        ]
    )
    body = client.post(
        "/api/prompt/preview",
        json={"prompt": "healthcare law firms in Ohio", "delay": 0},
    ).json()
    hosts = {s["url"] for s in body["seeds"]}
    assert hosts == {"https://hallrender.com/attorneys/"}


def test_all_aggregators_fails_loudly(client):
    client.stub(["https://www.justia.com/a", "https://www.avvo.com/b"])
    body = client.post(
        "/api/prompt/preview",
        json={"prompt": "healthcare law firms in Ohio", "delay": 0},
    ).json()
    assert body["crawlable"] == 0
    tried = body["sources_tried"][-1]
    assert tried["ok"] is False and "aggregator" in tried["error"]


def test_empty_result_refuses_to_start_a_run(client):
    client.stub(["https://www.justia.com/a"])
    response = client.post(
        "/api/prompt/run", json={"prompt": "healthcare law firms in Ohio", "delay": 0}
    )
    assert response.status_code == 422
    assert "No seed produced a URL" in response.json()["detail"]


def test_one_source_per_host(client):
    client.stub(
        [
            "https://www.bricker.com/team",
            "https://www.bricker.com/practices/health-care-regulatory",
            "https://hallrender.com/attorneys/",
        ]
    )
    response = client.post(
        "/api/prompt/run",
        json={
            "prompt": "healthcare law firms in Ohio",
            "delay": 0,
            "max_pages": 1,
            "max_depth": 0,
        },
    )
    assert response.status_code == 201
    hosts = {s["url"].split("/")[2] for s in response.json()["config"]["sources"]}
    assert len(hosts) == len(response.json()["config"]["sources"])
