"""Golden tests: each fixture imitates a real-world page layout."""
from pathlib import Path

import pytest

from oncoscraper.extract import extract_page
from oncoscraper.models import Page
from oncoscraper.robots import RobotsPolicy

SITE = Path(__file__).parent / "site"


def run(rel, url_path=None):
    html = (SITE / rel).read_text(encoding="utf-8")
    url = "https://sunrise.example/" + (url_path or rel.replace(".html", "").replace("__", "?"))
    return extract_page(Page(url, url, 200, html))


def doc(pr, name):
    return next(d for d in pr.doctors if d.get("name") == name)


@pytest.fixture(scope="module", autouse=True)
def build():
    import make_site
    make_site.build()


def test_label_value_profile():
    pr = run("doctor/dr-meenu-walia.html")
    assert pr.page_type == "profile"
    d = pr.doctors[0]
    assert d.get("name") == "Dr. Meenu Walia"
    assert d.get("designation") == "Senior Director - Medical Oncology"
    assert d.get("specialty") == "Medical Oncology"
    assert d.get("hospital") == "Sunrise Super Speciality Hospital, Saket, New Delhi"
    assert d.get("city") == "New Delhi"
    assert d.get("qualifications") == ["MBBS", "MD (Medicine)", "DM (Medical Oncology)"]
    assert d.get("opd_timings") == ["Mon - Fri: 10:00 AM - 4:00 PM", "Sat: 10:00 AM - 1:00 PM"]
    assert d.get("awards") == ["Best Oncologist Award, Delhi Medical Association 2015"]
    assert d.is_oncology
    # Related-doctor carousel becomes extra listing records with profile links.
    related = {r.get("name"): r.profile_url for r in pr.doctors[1:]}
    assert related["Dr. Rahul Verma"].endswith("/doctor/dr-rahul-verma")
    # Footer phone/email must not be attached to the doctor.
    assert not d.get("phone") and not d.get("email")


def test_jsonld_profile():
    d = run("doctor/dr-asha-rao.html").doctors[0]
    assert d.get("qualifications") == ["MBBS", "MD (Radiotherapy)", "DNB"]
    assert d.get("languages") == ["English", "Kannada", "Hindi"]
    assert d.get("registration") == "DMC-45821"
    assert d.get("city") == "Gurugram"
    assert d.layers["languages"] == "jsonld"


def test_nextjs_shell_profile_without_browser():
    pr = run("doctor/dr-rahul-verma.html")
    d = pr.doctors[0]
    assert pr.page_type == "profile" and d.get("name") == "Dr. Rahul Verma"
    assert d.get("specialty") == "Surgical Oncology"
    assert d.get("expertise") == ["Oral cancer surgery", "Thyroid cancer", "Robotic surgery"]
    assert d.get("about").startswith("Dr. Rahul Verma is a head and neck")
    assert d.layers["qualifications"] == "embedded_json"


def test_messy_profile_name_from_title_and_dotted_degrees():
    d = run("doctor/dr-priya-nair.html").doctors[0]
    assert d.get("name") == "Dr. Priya Nair"
    assert d.get("qualifications") == ["MBBS", "MS (Obst. & Gynae)", "MCh (Gynaecologic Oncology)"]
    assert d.get("experience_years") == "12"
    assert d.get("opd_timings") == ["Tue, Thu 9 am - 1 pm"]


def test_microdata_profile():
    d = run("doctor/dr-anil-gupta.html").doctors[0]
    assert d.get("hospital") == "Sunrise Hospital, Noida"
    assert "Paediatric Oncology" in d.get("sub_specialties")
    assert d.get("experience_years") == "17"


def test_non_oncologist_is_flagged():
    d = run("doctor/dr-sunil-kumar.html").doctors[0]
    assert not d.is_oncology  # 'oncology' only appears in his free-text bio


def test_card_listing_and_pagination():
    pr = run("find-a-doctor.html")
    assert pr.page_type == "listing"
    assert {d.get("name") for d in pr.doctors} == {"Dr. Meenu Walia", "Dr. Sunil Kumar", "Dr. Rahul Verma", "Dr. Neha Joshi"}
    mw = doc(pr, "Dr. Meenu Walia")
    assert mw.profile_url.endswith("/doctor/dr-meenu-walia")  # not the booking link
    assert mw.get("experience_years") == "28"
    assert pr.pagination_links == ["https://sunrise.example/find-a-doctor?page=2"]
    assert [d.get("name") for d in pr.doctors if d.is_oncology] == ["Dr. Meenu Walia", "Dr. Rahul Verma"]


def test_military_rank_in_name():
    pr = run("find-a-doctor__page=2.html")
    assert doc(pr, "Dr. Kavita Menon").is_oncology


def test_team_cards_without_dr_prefix():
    pr = run("specialities/cancer-care.html")
    assert pr.page_type == "listing"
    names = {d.get("name") for d in pr.doctors}
    assert names == {"Dr. Meenu Walia", "Dr. Asha Rao", "Dr. Priya Nair", "Dr. Anil Gupta"}
    assert doc(pr, "Dr. Asha Rao").get("qualifications") == ["MBBS", "MD (Radiotherapy)"]


def test_faculty_table():
    pr = run("faculty/radiation-oncology.html")
    hc = doc(pr, "Dr. Harish Chandra")
    assert hc.get("designation") == "Professor & Head"
    assert hc.get("email") == "hchandra@inst.example"
    assert hc.get("department") == "Department of Radiation Oncology"
    assert all(d.is_oncology for d in pr.doctors)


def test_embedded_state_listing():
    pr = run("doctors-json.html")
    assert pr.page_type == "listing"
    fs = doc(pr, "Dr. Farah Siddiqui")
    assert fs.get("experience_years") == "11" and fs.profile_url.endswith("/doctor/dr-farah-siddiqui")
    assert not doc(pr, "Dr. Neha Joshi").is_oncology


def test_home_page_has_no_false_doctors():
    pr = run("index.html", "")
    assert pr.doctors == []
    assert pr.page_type == "other"


def test_robots_wildcards():
    rp = RobotsPolicy("User-agent: *\nAllow:\nDisallow: /private/\nDisallow: *?speciality=*\n"
                      "Disallow: /book-an-appointment/doctor\n\nUser-agent: ClaudeBot\nAllow: /\n", "Mozilla/5.0 OncoScraper/1.0")
    assert rp.allowed("https://x.in/doctor/dr-a")
    assert not rp.allowed("https://x.in/find-a-doctor?speciality=oncology")
    assert not rp.allowed("https://x.in/private/a")
    assert not rp.allowed("https://x.in/book-an-appointment/doctor/dr-a")
    assert rp.allowed("https://x.in/find-a-doctor?page=2")


def test_robots_http_status_rules():
    """RFC 9309: 403 on robots.txt means no rules; 5xx means stay out."""
    import httpx
    from oncoscraper.config import ScrapeConfig
    from oncoscraper.fetch import Fetcher

    def run(status):
        f = Fetcher(ScrapeConfig(delay_seconds=0))
        f.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text="no")))
        return f.robots_for("https://x.example/a").allowed("https://x.example/find-a-doctor"), f.robots_status

    assert run(403)[0] is True and "403" in run(403)[1]["https://x.example"]
    assert run(404)[0] is True
    assert run(503)[0] is False
