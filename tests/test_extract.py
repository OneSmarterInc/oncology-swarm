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


def _cf(addr, key=0x5A):
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in addr)


def test_emails_cloudflare_obfuscated_and_desk_split():
    html = f"""<html><body><main><h1>Dr. Kavya Iyer</h1><p>Consultant - Medical Oncology</p><p>MBBS, MD, DM</p>
    <p>Email: <a href="/cdn-cgi/l/email-protection" class="__cf_email__" data-cfemail="{_cf('kavya.iyer@hosp.example')}">[email&#160;protected]</a></p>
    <p>For appointments write to oncology [at] hosp [dot] example</p></main>
    <footer>info@hosp.example</footer></body></html>"""
    d = extract_page(Page("https://hosp.example/doctors/dr-kavya-iyer", "https://hosp.example/doctors/dr-kavya-iyer", 200, html)).doctors[0]
    assert d.get("email") == "kavya.iyer@hosp.example"
    assert d.get("contact_email") == "oncology@hosp.example"   # desk address, not the doctor's
    assert "info@" not in (d.get("contact_email") or "")        # footer is ignored


def test_cloudflare_mailto_link_and_unnamed_single_address():
    html = f"""<html><body><main><h1>Dr. S.K.S. Marya</h1><p>Chairman - Orthopaedics</p><p>MBBS, MS</p>
    <a href="/cdn-cgi/l/email-protection#{_cf('drsks@hosp.example', 0x21)}">Mail the doctor</a></main></body></html>"""
    d = extract_page(Page("https://hosp.example/doctor/sks-marya", "https://hosp.example/doctor/sks-marya", 200, html)).doctors[0]
    assert d.get("email") == "drsks@hosp.example"   # only non-desk address on his own page


def test_card_emails():
    cards = "".join(f'<div class="c"><h3>Dr. {n}</h3><p>Consultant - Radiation Oncology</p><p>{e}</p></div>'
                    for n, e in [("Ravi Nair", "ravi.nair(at)hosp(dot)example"), ("Tara Sen", "Email: appointments@hosp.example")])
    pr = extract_page(Page("https://hosp.example/oncology", "https://hosp.example/oncology", 200,
                           f"<html><body><main><h1>Radiation Oncology</h1>{cards}</main></body></html>"))
    by = {d.get("name"): d for d in pr.doctors}
    assert by["Dr. Ravi Nair"].get("email") == "ravi.nair@hosp.example"
    assert by["Dr. Tara Sen"].get("email") is None and by["Dr. Tara Sen"].get("contact_email") == "appointments@hosp.example"


def test_phones_doctor_vs_desk_and_formats():
    html = """<html><body><header>Emergency 011-2651-5050</header><main><h1>Dr. Kavya Iyer</h1>
    <p>Consultant - Medical Oncology</p><p>MBBS, MD, DM</p>
    <div class="label">Mobile</div><div class="value">98110 12345</div>
    <p>For appointments call (080) 2502 4444 or toll free 1800-102-1010.</p>
    <a href="https://wa.me/919845098450">WhatsApp Dr. Kavya</a>
    <p>Reg. No. KMC-1234567890. Fax: 080-2502 4445</p></main></body></html>"""
    d = extract_page(Page("https://hosp.example/doctors/dr-kavya-iyer", "https://hosp.example/doctors/dr-kavya-iyer", 200, html)).doctors[0]
    assert d.get("phone") == "+91 98110 12345; +91 98450 98450"
    assert d.get("contact_phone") == "+91 80 2502 4444"  # toll-free numbers are left out
    assert "2651" not in (d.get("phone") or "") + (d.get("contact_phone") or "")  # header ignored, fax ignored


def test_card_and_table_phones():
    cards = "".join(f'<div class="c"><h3>Dr. {n}</h3><p>Consultant - Surgical Oncology</p>{p}</div>'
                    for n, p in [("Ravi Nair", '<a href="tel:+919900112233">Call</a>'),
                                 ("Tara Sen", "<p>Appointments: 044-2474 1000</p>")])
    pr = extract_page(Page("https://hosp.example/onco", "https://hosp.example/onco", 200,
                           f"<html><body><main><h1>Surgical Oncology</h1>{cards}</main></body></html>"))
    by = {d.get("name"): d for d in pr.doctors}
    assert by["Dr. Ravi Nair"].get("phone") == "+91 99001 12233"
    assert by["Dr. Tara Sen"].get("contact_phone") == "+91 44 2474 1000" and not by["Dr. Tara Sen"].get("phone")
    table = ("<table><tr><th>Name</th><th>Designation</th><th>Mobile</th></tr>"
             "<tr><td>Dr. Om Rao</td><td>Professor</td><td>94440 12345</td></tr>"
             "<tr><td>Dr. Ila Das</td><td>Professor</td><td>94440 54321</td></tr></table>")
    pr = extract_page(Page("https://inst.example/rt", "https://inst.example/rt", 200,
                           f"<html><body><main><h2>Department of Radiation Oncology</h2>{table}</main></body></html>"))
    assert {d.get("name"): d.get("phone") for d in pr.doctors} == {"Dr. Om Rao": "+91 94440 12345", "Dr. Ila Das": "+91 94440 54321"}


def test_shared_number_moves_to_hospital_column():
    from oncoscraper.contacts import demote_shared
    from oncoscraper.models import DoctorRecord
    recs = []
    for i, n in enumerate(["A Rao", "B Sen", "C Das", "D Pai"]):
        r = DoctorRecord(source_url=f"https://h.example/{i}")
        r.values.update({"name": "Dr. " + n, "phone": "+91 98111 00000" + ("; +91 98222 0000%d" % i if i == 0 else ""),
                         "email": "appointments-team@h.example"})
        recs.append(r)
    assert demote_shared(recs) == 8
    assert recs[0].get("phone") == "+91 98222 00000"
    assert recs[1].get("phone") is None and recs[1].get("contact_phone") == "+91 98111 00000"
    assert recs[2].get("contact_email") == "appointments-team@h.example"


def test_phone_anomalies_from_real_runs():
    from oncoscraper import phones
    # page-number runs read as phones (Max: '+91 10 1112 1314')
    assert phones.find("Page 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25") == []
    assert phones.find("1011121314") == []
    # same mobile in two spacings becomes one value
    assert phones.normalise("+91 926 888 0303") == phones.normalise("+91 92688 80303") == "+91 92688 80303"
    # landlines with STD codes still read as landlines
    assert phones.normalise("080 4110 0000") == "+91 80 4110 0000"
    assert phones.normalise("0674 230 1234") == "+91 674 230 1234"
    # toll-free left out
    assert phones.find("Toll free 1800-208-2000") == []
    # lists of numbers still work
    assert [n for n, _ in phones.find("Mob: 98110 12345, 98110 54321")] == ["+91 98110 12345", "+91 98110 54321"]


def test_prose_is_not_an_email():
    from oncoscraper import emails
    text = "served@Dr.Ram, observer@st.jude, consultant@aoi-imphal.dm, info@rgcirc.org; oncology@rgcirc.he, x@hosp.co.in"
    assert emails.find(text) == ["info@rgcirc.org", "x@hosp.co.in"]


def test_branch_number_on_every_page_is_not_the_doctors():
    """MOC-style: each profile's data carries the branch number, which the site also lists on every page."""
    from oncoscraper.contacts import demote_shared, demote_sitewide
    from oncoscraper.models import DoctorRecord
    a = DoctorRecord(source_url="https://moc.in/our-team/dr-a")
    a.set("name", "Dr. A", "h1"); a.set("phone", "+91 85919 37291", "jsonld")
    b = DoctorRecord(source_url="https://moc.in/our-team/dr-b")
    b.set("name", "Dr. B", "h1"); b.set("phone", "+91 91378 57037", "jsonld")
    c = DoctorRecord(source_url="https://moc.in/our-team/dr-c")
    c.set("name", "Dr. C", "h1"); c.set("phone", "+91 98111 22233", "labels")
    pages = {"+91 85919 37291": {f"https://moc.in/p{i}" for i in range(20)},
             "+91 91378 57037": {"https://moc.in/our-team/dr-b"},
             "+91 98111 22233": {"https://moc.in/our-team/dr-c"}}
    demote_sitewide([a, b, c], pages)
    assert a.get("phone") is None and a.get("contact_phone") == "+91 85919 37291"
    assert b.get("phone") == "+91 91378 57037" and c.get("phone") == "+91 98111 22233"
    # the same number on two doctors is a shared desk/branch number
    d = DoctorRecord(source_url="https://moc.in/our-team/dr-d")
    d.set("name", "Dr. D", "h1"); d.set("phone", "+91 98111 22233", "jsonld")
    demote_shared([c, d])
    assert c.get("phone") is None and d.get("contact_phone") == "+91 98111 22233"
