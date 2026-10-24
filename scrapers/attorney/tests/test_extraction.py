import json

import pytest

from attorneyscraper.classify import classify
from attorneyscraper.extract import clean_name, decode_emails, extract, phone_values


@pytest.mark.parametrize(
    "text,category",
    [
        (
            "Attorney Jane represents patients in medical malpractice cases.",
            "plaintiff",
        ),
        ("Attorney Jane defends hospitals in medical malpractice lawsuits.", "defense"),
        (
            "Attorney Jane advises on healthcare regulatory compliance and HIPAA.",
            "compliance",
        ),
        (
            "Attorney Jane represents healthcare whistleblowers in False Claims Act actions.",
            "fraud",
        ),
        ("Attorney Jane handles healthcare transactions and mergers.", "transactional"),
        ("Attorney Jane previously worked as a registered nurse.", "nurse"),
        (
            "Attorney Jane is counsel to the state department of health.",
            "public_health",
        ),
        (
            "Attorney Jane advises hospitals on bioethics and informed consent.",
            "bioethics",
        ),
    ],
)
def test_eight_categories(text, category):
    r = classify(text)
    assert category in r["categories"]
    assert r["status"] == "confirmed"
    assert r["classification_evidence"][category]


def test_generic_fraud_not_healthcare():
    assert (
        classify(
            "Attorney Jane handles False Claims Act cases for defense contractors."
        )["categories"]
        == []
    )


def test_nonlawyer_excluded():
    assert (
        classify(
            "Jane assists attorneys with medical malpractice defense.", "Paralegal"
        )["status"]
        == "excluded"
    )


def test_ambiguous_role_needs_review():
    assert (
        classify("Jane specializes in healthcare compliance.")["status"]
        == "needs_review"
    )


@pytest.mark.parametrize(
    "name",
    [
        "Our Attorneys",
        "Medical Malpractice",
        "View Profile",
        "Meet Our Team",
        "Jane Smith 123",
        "Practice Areas",
    ],
)
def test_reject_headings(name):
    assert not clean_name(name)


@pytest.mark.parametrize(
    "name", ["Jane A. Smith", "E. Drew Britcher", "José García", "Anne-Marie O’Neill"]
)
def test_real_name_formats(name):
    assert clean_name(name) == name


def test_obfuscated_email():
    assert decode_emails("jane [at] firm [dot] com") == {"jane@firm.com"}


def test_us_phone_extensions():
    assert phone_values("201.444.1644 ext. 101") == {"+1 201-444-1644 ext 101"}


def test_attribution_footer_support_and_fax():
    html = """<header>info@firm.com</header><main><h1>Jane Smith</h1><h2>Attorney</h2>
    <p>Jane defends hospitals in medical malpractice cases.</p>
    <p><a href="mailto:jsmith@firm.com">Email Jane</a></p>
    <p>Direct: <a href="tel:212-555-2345">212-555-2345</a></p>
    <p>Assistant: Kelly <a href="mailto:kelly@firm.com">kelly@firm.com</a> 212-555-3456</p>
    <p>Fax: <a href="tel:212-555-4567">212-555-4567</a></p></main>
    <footer>jane@unrelated.com 212-555-6789</footer>"""
    p = extract(html, "https://firm.com/people/jane", "Firm")["records"][0]
    contacts = {c["value"]: c["owner"] for c in p["contacts"]}
    assert contacts["jsmith@firm.com"] == "individual"
    assert contacts["kelly@firm.com"] == "support_staff"
    assert contacts["+1 212-555-2345"] == "individual"
    assert "+1 212-555-4567" not in contacts
    assert "jane@unrelated.com" not in contacts
    assert p["categories"] == ["defense"]


def test_listing_does_not_inherit_practice_area():
    html = """<main><h1>Healthcare Attorneys</h1><p>Our attorneys handle HIPAA, healthcare transactions and fraud.</p>
    <article><h2><a href="/people/jane">Jane Smith</a></h2><p>Attorney</p></article>
    <article><h2><a href="/people/john">John Jones</a></h2><p>Attorney</p></article></main>"""
    records = extract(html, "https://firm.com/people/")["records"]
    assert len(records) == 2
    assert all(not p["categories"] and p["status"] == "needs_review" for p in records)


def test_jsonld_person_only():
    obj = [
        {"@type": "Organization", "name": "Smith Legal", "email": "info@firm.com"},
        {
            "@type": "Person",
            "name": "Jane Smith",
            "jobTitle": "Attorney",
            "description": "Healthcare compliance and HIPAA",
            "email": "abc@firm.com",
            "url": "/people/jane",
        },
    ]
    html = '<script type="application/ld+json">' + json.dumps(obj) + "</script>"
    records = extract(html, "https://firm.com/people/")["records"]
    assert len(records) == 1 and records[0]["name"] == "Jane Smith"
    assert records[0]["contacts"][0]["owner"] == "individual"


def test_no_guessed_contacts():
    p = extract(
        "<h1>Jane Smith</h1><p>Attorney handling healthcare compliance.</p>",
        "https://firm.com/jane",
    )["records"][0]
    assert p["contacts"] == []


def test_cloudflare_email():
    value = "jsmith@firm.com"
    key = 17
    encoded = bytes([key] + [ord(c) ^ key for c in value]).hex()
    p = extract(
        f'<h1>Jane Smith</h1><p>Attorney handles HIPAA.</p><a data-cfemail="{encoded}">Email</a>',
        "https://firm.com/jane",
    )["records"][0]
    assert p["contacts"][0]["value"] == value
