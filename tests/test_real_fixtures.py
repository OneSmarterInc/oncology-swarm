"""Offline tests for short real-source text excerpts, never native-page coverage.

The HTML wrappers are locally reconstructed; see fixtures/real/README.md.
No network requests, application seed jobs, approvals, or exports are made.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.extractor import extract
from app.fetcher import FetchResult, visible_text
from app.validation import validate_proposal


ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "real"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
GOLDEN = json.loads((ROOT / "golden.json").read_text())["cases"]
CASES = MANIFEST["cases"]


def source(case):
    raw = (ROOT / case["fixture_file"]).read_bytes()
    document = raw.decode("utf-8")
    # Here retrieved_at is the offline excerpt's observation timestamp, not a
    # claimed origin HTTP retrieval. The manifest makes that distinction explicit.
    return FetchResult(case["source_url"], visible_text(document), document,
                       case["web_observed_at"], hashlib.sha256(raw).hexdigest(), False)


def test_manifest_does_not_misrepresent_reconstructed_markup_as_live_capture():
    assert MANIFEST["not_raw_html_capture"] is True
    assert MANIFEST["not_live_fetcher_evidence"] is True
    assert MANIFEST["not_approved_directory_data"] is True
    assert len(CASES) == len(GOLDEN) == 3
    assert {case["id"] for case in CASES} == set(GOLDEN)


def test_pilot_report_keeps_blocked_backend_and_unapproved_sources_explicit():
    report_path = ROOT.parents[1] / "reports" / "live-pilot-v2.json"
    if not report_path.is_file():
        pytest.skip("Private live-pilot research output is intentionally not committed")
    report = json.loads(report_path.read_text())
    pages = report["pages"]
    summary = report["summary"]
    assert len(pages) == summary["candidate_profiles"] == 6
    assert len({page["institution"] for page in pages}) == 2
    assert sum(page["backend"]["status"] == "failed" for page in pages) == 1
    assert sum(page["backend"]["status"] == "not_attempted" for page in pages) == 5
    assert summary["backend_successful_profiles"] == 0
    assert summary["backend_http_requests_reserved"] == 0
    assert summary["raw_source_html_fixtures"] == 0
    assert summary["native_page_extraction_accuracy"] is None
    assert report["source_candidates"]["automatic_import"] is False
    for candidate in report["source_candidates"]["candidates"]:
        assert candidate["approval_status"] == "UNAPPROVED"
        assert candidate["enabled"] is False
        assert candidate["approved_at"] is None and candidate["reviewer"] is None
        assert urlsplit(candidate["doctors_list_url"]).hostname == candidate["official_hostname"]
    for page in pages:
        assert page["backend"]["retrieved_at"] is None
        assert page["backend"]["content_hash"] is None
        assert page["backend"]["http_status"] is None
        assert page["backend"]["proposal"] is None
        assert page["web_research"]["not_backend_fetch_evidence"] is True


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_real_excerpt_provenance_hash_and_short_factual_spans(case):
    fixture = source(case)
    assert urlsplit(case["source_url"]).scheme == "https"
    assert urlsplit(case["source_url"]).hostname == "www.maxhealthcare.in"
    assert case["source_type"] == "official_institution"
    assert datetime.fromisoformat(case["web_observed_at"]).tzinfo is not None
    assert case["original_html_captured"] is False
    assert case["origin_retrieved_at"] is None
    assert case["origin_http_status"] is None
    assert case["origin_body_sha256"] is None
    assert case["fixture_sha256"] == fixture.content_hash
    assert case["permission_status"] == "limited_private_research_no_broad_reuse_license_established"
    assert "RECONSTRUCTED excerpt" in fixture.html
    assert "data-field" not in fixture.html and "application/ld+json" not in fixture.html
    expected = GOLDEN[case["id"]]
    text = "\n".join(expected[key] for key in ("name", "specialty", "hospital")) + "\n"
    assert hashlib.sha256(text.encode()).hexdigest() == case["excerpt_text_sha256"]
    assert len(text.split()) == case["excerpt_word_count"] <= 25
    assert all(value in fixture.text for value in expected.values())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_real_excerpt_name_is_literal_and_unlabelled_fields_stay_unassigned(case):
    fixture = source(case)
    proposal = extract(fixture)
    expected = GOLDEN[case["id"]]
    assert proposal["synthetic"] is False
    assert proposal["doctor"]["name"] == expected["name"]
    assert proposal["doctor"]["specialty"] is None
    assert proposal["doctor"]["city"] is None
    assert proposal["affiliations"] == []
    assert proposal["contacts"] == []
    assert proposal["registration"] == []
    assert all(value is None for key, value in proposal["doctor"].items() if key != "name")
    assert validate_proposal(proposal, fixture) == []
    assert {item["field"] for item in proposal["evidence"]} == {"doctor.name"}
    for evidence in proposal["evidence"]:
        assert evidence["support_text"] in fixture.text
        assert evidence["value"] in evidence["support_text"]
        assert evidence["source_url"] == case["source_url"]
        assert evidence["retrieved_at"] == case["web_observed_at"]
