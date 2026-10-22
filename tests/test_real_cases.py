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
