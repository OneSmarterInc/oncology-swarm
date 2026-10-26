"""Regression tests built from wrong values seen in a real 7-hospital batch (Oct 2026)."""
import json

from oncoscraper.crawl import Merger
from oncoscraper.extract import extract_page
from oncoscraper.models import DoctorRecord, Page
from oncoscraper.profile import clean_fields, clean_registration, clean_specialty, city_from
from oncoscraper.oncology import score, veto
from oncoscraper.vocab import SKIP_URL, clean_name

LAYOUT = "<html><head><title>{t}</title>{head}</head><body><main>{body}</main></body></html>"


def page(url, body, title="Doctor", head=""):
    return extract_page(Page(url, url, 200, LAYOUT.format(t=title, head=head, body=body)))


def test_profile_page_mentions_are_not_extra_doctors():
    video = {"@context": "https://schema.org", "@type": "VideoObject",
             "name": "Dr. Mohana Vamsy, sheds light on Breast Cancer causes", "uploadDate": "2024-01-01"}
    body = """<h1>Dr. Swathi Krishna</h1><p>Consultant - Medical Oncology</p><p>MBBS, MD, DM (Medical Oncology)</p>
    <h3>Awards</h3><ul><li>Dr. APJ Abdul Kalam Excellence Award 2023</li><li>Dr. M. S. Gujral Gold Medal</li>
    <li>Dr. JC Brash Memorial Prize</li></ul>
    <p>Dr. Krishna has a special interest in benign hematology and childhood leukemias.</p>"""
    pr = page("https://moc.example/our-team/dr-swathi-krishna", body,
              head=f'<script type="application/ld+json">{json.dumps(video)}</script>')
    assert [d.get("name") for d in pr.doctors] == ["Dr. Swathi Krishna"]


def test_listing_with_a_name_heading_is_still_a_listing():
    cards = "".join(f'<div class="card"><h3><a href="/dr-profile/dr-{n.lower()}-x">Dr. {n} X</a></h3>'
                    f'<p>Clinical Oncology</p></div>' for n in ["Anila", "Bhanu", "Chitra", "Deepa"])
    pr = page("https://aoi.example/our-doctors/clinical-oncologists", "<h1>Dr. Anila X</h1>" + cards)
    assert pr.page_type == "listing" and len(pr.doctors) >= 4


def test_names():
    assert clean_name("APJ Abdul Kalam Excellence Award") is None
    assert clean_name("JC Brash Memorial") is None
    assert clean_name("Dr Md. Izhar Ali") == "Md. Izhar Ali"
    assert clean_name("Sravya B Medicial") == "Sravya B"
    assert clean_name("Rahul D L") == "Rahul D L"
    assert clean_name("DEPARTMENT ACTIVITY") is None
    assert clean_name("Sudhir Kumar Rawal Uro") == "Sudhir Kumar Rawal"


def _rec(**values):
    r = DoctorRecord(source_url="https://h.example/doctor/x", profile_url="https://h.example/doctor/x")
    for k, v in values.items():
        r.set(k, v, "patterns", str(v), r.source_url)
    return r


def test_specialty_cleaning():
    assert clean_specialty("ECMO (European Certification in Medical Oncology)") is None
    assert clean_specialty("European Society of Medical Oncology (ESMO) No 509214") is None
    assert clean_specialty("Finalist – ISMPO-Torrent Young Oncology Award (Zonal Round, 2023)") is None
    assert clean_specialty("How the Da Vinci Xi Robot is Transforming Urology Surgery") is None
    assert clean_specialty("Speciality- Medical and Pediatric Hemato-Oncologist.") == "Medical and Pediatric Hemato-Oncologist"
    assert clean_specialty("Medical Oncology, Cancer Care / Oncology") == "Medical Oncology"


def test_designation_registration_opd_department():
    r = _rec(designation="Radiation Oncology", opd_timings=["Address", "Mon - Sat: 9.00 am - 5.00 pm"],
             department="Home")
    clean_fields(r)
    assert r.get("designation") is None and r.get("specialty") == "Radiation Oncology"
    assert r.get("opd_timings") == ["Mon - Sat: 9.00 am - 5.00 pm"] and r.get("department") is None
    r2 = _rec(designation="Dr. Sravya B | Cancer Doctor | Omega Multispeciality Hospital")
    clean_fields(r2)
    assert r2.get("designation") is None
    assert clean_registration("/24762 date", "MCI/24762 date") == "MCI/24762"
    assert clean_registration("00517", "MMC 00517") == "MMC 00517"
    assert clean_registration("12", "Reg 12") is None


def test_non_oncologists_are_vetoed():
    for title in ("Chief Dietician", "Director & Senior Interventional Cardiologist", "Medical Physicist"):
        r = _rec(designation=title, about="works in the oncology department")
        score(r, "oncology")
        assert not r.is_oncology, title
    r = _rec(designation="Consultant", specialty="Surgical Oncology")
    score(r, "")
    assert r.is_oncology and not veto(r)


def test_locality_gives_the_right_city():
    assert city_from("Chief Oncologist Omega Hospitals Gachibowli ... branch at Puducherry") == "Hyderabad"
    assert city_from("Consultant at M|O|C Cancer Care, Pusa Road, Karol Bagh") == "New Delhi"


def test_same_doctor_on_two_pages_is_merged():
    m = Merger()
    a = _rec(name="Dr. Raman Narang", city="New Delhi")
    a.profile_url = a.source_url = "https://moc.example/our-team/dr-raman-narang"
    b = _rec(name="Dr. Raman Narang", city="New Delhi")
    b.profile_url = b.source_url = "https://moc.example/north-india/doctor/dr-raman-narang"
    c = _rec(name="Dr. Ch. Mohana Vamsy")
    d = _rec(name="Dr. Mohana Vamsy")
    d.profile_url = "https://other.example/"
    e1, e2 = _rec(name="Dr. A. Kumar"), _rec(name="Dr. B. Kumar")
    e1.profile_url, e2.profile_url = "https://h.example/a", "https://h.example/b"
    for r in (a, b, c, d, e1, e2):
        m.add(r)
    names = sorted(r.get("name") for r in m.records)
    assert names == ["Dr. A. Kumar", "Dr. B. Kumar", "Dr. Ch. Mohana Vamsy", "Dr. Raman Narang"]


def test_news_pages_are_skipped():
    assert SKIP_URL.search("/newspress/doctor-ndtv-hindi-targeted-immunotherapy/")
    assert SKIP_URL.search("/doctor-talk")


def test_hospital_and_qualification_cleaning():
    for bad in ("20+ years", "https://www.maxhealthcare.in/our-specialities/cancer-care",
                "Explore outstanding cancer care at Omega Hospitals"):
        r = _rec(hospital=bad)
        clean_fields(r)
        assert r.get("hospital") is None, bad
    r = _rec(qualifications=["MBBS", "Consultant - Orthopaedic Oncology", "DM (Medical Oncology)"])
    clean_fields(r)
    assert r.get("qualifications") == ["MBBS", "DM (Medical Oncology)"]


def test_review_findings_real_names_and_merges():
    for raw, want in [("Ratna Devi", "Ratna Devi"), ("Bharat Bhushan", "Bharat Bhushan"), ("K. Shri Ram", "K. Shri Ram"),
                      ("Rajesh Nagar", "Rajesh Nagar"), ("Sudeep Gupta Tata Memorial Centre", "Sudeep Gupta"),
                      ("Rajesh Sharma Hospital Director", "Rajesh Sharma"), ("Priya Rao Institute of Oncology", "Priya Rao")]:
        assert clean_name(raw) == want, raw
    m = Merger()
    a, b = _rec(name="Dr. K. Srinivas Rao"), _rec(name="Dr. P. Srinivas Rao")
    a.profile_url, b.profile_url = "https://h.example/d/101", "https://h.example/d/202"
    c, d = _rec(name="Dr. Rajesh Kumar", city="Hisar"), _rec(name="Dr. Rajesh Kumar")
    c.profile_url, d.profile_url = "https://h.example/d/1", "https://h.example/d/2"
    for r in (a, b, c, d):
        m.add(r)
    assert len(m.records) == 4


def test_review_findings_fields_and_veto():
    assert clean_specialty("Radiation Oncology (Board Certified)") == "Radiation Oncology"
    assert clean_specialty("Medical Oncology - Member ESMO") == "Medical Oncology"
    assert clean_specialty("Immunotherapy") == "Immunotherapy"
    from oncoscraper.profile import clean_designation
    assert clean_designation("Chairman, Medical Oncology")[0] == "Chairman, Medical Oncology"
    assert clean_designation("Consultant (Medical Oncology)") == ("Consultant", "Medical Oncology")
    for title in ("Radiation Oncologist & Medical Physicist", "Chief Executive & Senior Consultant - Medical Oncology"):
        r = _rec(designation=title)
        score(r, "")
        assert r.is_oncology, title
    r = _rec(photo_url="https://h.example/img.php?id=7&region=south")
    assert r.get("photo_url").endswith("id=7&region=south")


def test_profile_with_related_doctors_strip_stays_a_profile():
    cards = "".join(f'<div class="card"><h3><a href="/doctors/{i}">Dr. {n} Rao</a></h3><p>Medical Oncology</p></div>'
                    for i, n in enumerate(["Anil", "Bina", "Chetan", "Deepa"], 100))
    body = "<h1>Dr. Swathi K</h1><p>Consultant - Medical Oncology</p><p>MBBS, MD, DM</p><h2>Related doctors</h2>" + cards
    pr = page("https://h.example/doctors/4567", body)
    assert pr.page_type == "profile" and pr.doctors[0].get("name") == "Dr. Swathi K"
    assert SKIP_URL.search("/doctor/newsha") is None


def _fortis_pair():
    card = _rec(name="Dr. Divesh Goyal", city="Jaipur", hospital="Fortis Healthcare, Jaipur")
    card.source_url = "https://www.fortis.example/doctors"
    card.profile_url = None
    card.page_type = "listing"
    profile = _rec(name="Dr. Divesh Goyal", city="Jaipur", hospital="Fortis Escorts Hospital, Jaipur")
    profile.source_url = profile.profile_url = "https://fortis.example/doctors/divesh-goyal"
    return card, profile


def test_fortis_card_profile_hospital_variants_merge_in_both_orders():
    for reverse in (False, True):
        card, profile = _fortis_pair()
        card.set("email", "doctor@example.test", "listing_card")
        m = Merger()
        for r in ((profile, card) if reverse else (card, profile)):
            m.add(r)
        assert len(m.records) == 1
        merged = m.records[0]
        assert merged.page_type == "profile"
        assert merged.get("hospital") == "Fortis Escorts Hospital, Jaipur"
        assert merged.get("email") == "doctor@example.test"
        assert card.source_url in [merged.source_url, *merged.found_on]


def test_hospital_variant_merge_requires_identity_evidence():
    for change in ("city", "missing_city", "host", "name", "source_hospital", "two_profiles"):
        card, profile = _fortis_pair()
        if change == "city":
            profile.values["city"] = "Delhi"
        elif change == "missing_city":
            profile.values.pop("city")
        elif change == "host":
            profile.source_url = "https://different.example/doctors/divesh-goyal"
        elif change == "name":
            profile.values["name"] = "Dr. Dinesh Goyal"
        elif change == "source_hospital":
            card.source_hospital, profile.source_hospital = "Fortis A", "Fortis B"
        else:
            card.page_type = "profile"
        m = Merger()
        m.add(card)
        m.add(profile)
        assert len(m.records) == 2, change


def test_same_profile_url_does_not_merge_different_batch_hospitals():
    m = Merger()
    for hospital in ("Hospital A", "Hospital B", "Hospital A", "Hospital B"):
        r = _rec(name="Dr. Divesh Goyal")
        r.source_hospital = hospital
        m.add(r)
    assert len(m.records) == 2


def test_affiliation_suffix_removed_without_truncating_names():
    for raw in ("Jyoti Anand IOSPL", "JYOTI ANAND IOSPL", "Dr. Jyoti Anand IOSPL"):
        assert clean_name(raw) == "Jyoti Anand"
    assert clean_name("Jyoti ANAND") == "Jyoti ANAND"
    assert clean_name("RAHUL D L") == "Rahul D L"
    pr = page("https://h.example/doctors/jyoti-anand",
              "<h1>Dr. Jyoti Anand IOSPL</h1><p>Consultant Medical Oncology</p>")
    assert pr.doctors[0].get("name") == "Dr. Jyoti Anand"


def test_fortis_variant_merge_with_different_profile_paths():
    card, profile = _fortis_pair()
    card.profile_url = "https://fortis.example/jaipur/doctors/divesh-goyal"
    profile.values["city"] = " jaipur "
    m = Merger()
    m.add(card)
    m.add(profile)
    assert len(m.records) == 1


def _named(name, url, page_type="profile", hospital="Fortis Escorts Hospital, Jaipur"):
    r = DoctorRecord(source_url=url, profile_url=url if page_type == "profile" else None, page_type=page_type)
    r.set("name", name, "h1", name, url)
    r.set("hospital", hospital, "patterns", hospital, url)
    r.set("city", "Jaipur", "patterns", "Jaipur", url)
    return r


def test_acronym_glued_to_several_names_is_stripped_sitewide():
    from oncoscraper.name_suffix import strip_shared_name_suffix, trailing_acronym
    assert trailing_acronym("Dr. Jyoti Anand MHRC") == "MHRC"
    assert trailing_acronym("Dr. Jyoti ANAND") is None          # a surname in caps
    assert trailing_acronym("DR. JYOTI ANAND MHRC") is None      # whole name in caps
    assert trailing_acronym("Dr. Jyoti Anand MBBS") is None      # a degree, handled elsewhere
    assert trailing_acronym("Dr. Jyoti Anand III") is None
    recs = [_named("Dr. Jyoti Anand MHRC", "https://h.example/doctors", "listing"),
            _named("Dr. Ravi Kumar MHRC", "https://h.example/doctors", "listing"),
            _named("Dr. Suma Reddy MHRC", "https://h.example/doctors", "listing")]
    assert strip_shared_name_suffix(recs) == {"MHRC": 3}
    assert [r.get("name") for r in recs] == ["Dr. Jyoti Anand", "Dr. Ravi Kumar", "Dr. Suma Reddy"]


def test_single_all_caps_surname_is_not_an_acronym():
    from oncoscraper.name_suffix import strip_shared_name_suffix
    recs = [_named("Dr. Jyoti Anand MHRC", "https://h.example/doctors", "listing")]
    assert strip_shared_name_suffix(recs) == {}
    assert recs[0].get("name") == "Dr. Jyoti Anand MHRC"
    # Two REDDYs in caps next to a mixed-case Reddy: a family name, not an affiliation.
    recs = [_named("Dr. Ravi Kumar REDDY", "https://h.example/a"), _named("Dr. Suma Rani REDDY", "https://h.example/b"),
            _named("Dr. Anil Reddy", "https://h.example/c")]
    assert strip_shared_name_suffix(recs) == {}


def test_card_with_acronym_merges_with_clean_profile_after_crawl(site_url, monkeypatch, tmp_path):
    from oncoscraper.crawl import Crawler
    from oncoscraper.config import ScrapeConfig
    from oncoscraper.models import Page
    import oncoscraper.crawl as crawl_mod
    base = "https://acr.example"
    pages = {
        f"{base}/doctors": "<h1>Our Doctors</h1>" + "".join(
            f'<div class="card"><h3>Dr. {n} MHRC</h3><p>Consultant Medical Oncology</p></div>'
            for n in ("Jyoti Anand", "Ravi Kumar", "Suma Rani")) +
            '<a href="/doctor/jyoti-anand">Profile</a>',
        f"{base}/doctor/jyoti-anand": "<h1>Dr. Jyoti Anand</h1><p>Consultant Medical Oncology</p>"
                                       "<p>Email: jyoti.anand@acr.example</p>",
    }

    class FakeFetcher:
        requests_made = 0
        def __init__(self, cfg): pass
        def get(self, url, **kw):
            html = pages.get(url.rstrip("/"))
            return Page(url, url, 200 if html else 404, f"<html><body>{html}</body></html>" if html else "")
        def close(self): pass
        def allowed(self, url): return True

    monkeypatch.setattr(crawl_mod, "Fetcher", FakeFetcher)
    result = Crawler(ScrapeConfig(delay_seconds=0, render="never", use_sitemaps=False, max_pages=10)).run([f"{base}/doctors"])
    names = sorted(d.get("name") for d in result.doctors)
    assert names == ["Dr. Jyoti Anand", "Dr. Ravi Kumar", "Dr. Suma Rani"], names
    jyoti = next(d for d in result.doctors if d.get("name") == "Dr. Jyoti Anand")
    assert jyoti.page_type == "profile" and jyoti.get("email") == "jyoti.anand@acr.example"
