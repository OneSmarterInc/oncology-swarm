"""Prompt -> hospitals -> doctors -> Excel, with local sites and no model.

Each hospital is served on its own 127.0.0.x host, as on the real web. Only the
site's own pages decide whether it qualifies.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from oncoscraper.config import ScrapeConfig
from oncoscraper.crawl import Crawler
from oncoscraper.discovery_services import DiscoveryDB
from oncoscraper.models import DoctorRecord
from oncoscraper.prompt_jobs import PromptIn, PromptManager, export_sources
from oncoscraper.search_candidates import exclusion_reason
from oncoscraper.simple_plan import simple_doctor_plan
from oncoscraper.site_discovery import (
    candidate_hosts, discover_hospitals, names_from_results, read_request, site_queries, survey_site,
)
from oncoscraper.store import Store
from oncoscraper.verified_discovery import filter_records


def card(name, spec, unit, email):
    slug = name.lower().replace("dr. ", "dr-").replace(" ", "-").replace(".", "")
    return (f"<div class='doctor-card'><h3><a href='/doctor/{slug}'>{name}</a></h3>"
            f"<p class='designation'>Senior Consultant</p><p class='spec'>{spec}</p>"
            f"<p class='loc'>{unit}</p><p>Email: <a href='mailto:{email}'>{email}</a></p></div>")


SITES = {
    # key -> (name, address, kind words, doctors)
    "lotus": ("Lotus Cancer Hospital", "Saket, New Delhi, Delhi 110017", "Comprehensive cancer care",
              [("Dr. Meera Kapoor", "Medical Oncology", "Lotus Cancer Hospital, Delhi", "meera.kapoor@lotuscancer.in"),
               ("Dr. Arjun Sethi", "Radiation Oncology", "Lotus Cancer Hospital, Delhi", "arjun.sethi@lotuscancer.in"),
               ("Dr. Kavita Rao", "Surgical Oncology", "Lotus Cancer Hospital, Mumbai", "kavita.rao@lotuscancer.in"),
               ("Dr. Sanjay Gupta", "Cardiology", "Lotus Cancer Hospital, Delhi", "sanjay.gupta@lotuscancer.in")]),
    "deccan": ("Deccan Cancer Hospital", "Shivaji Nagar, Pune, Maharashtra", "Cancer care and oncology",
               [("Dr. Vivek Patil", "Medical Oncology", "Deccan Cancer Hospital, Pune", "vivek.patil@deccancancer.in")]),
    "nodoc": ("Capital Cancer Hospital", "Dwarka, New Delhi, Delhi", "Cancer treatment", []),
    "eyecare": ("Vision Eye Hospital", "Karol Bagh, New Delhi, Delhi", "Eye care and cataract surgery",
                [("Dr. Ritu Jain", "Ophthalmology", "Vision Eye Hospital, Delhi", "ritu.jain@visioneye.in")]),
}
ORDER = ["lotus", "deccan", "nodoc", "eyecare"]


def page(site, body):
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta property='og:site_name' content='{site}'>"
            f"<title>{site}</title></head><body><nav><a href='/'>Home</a> <a href='/find-a-doctor'>Find a Doctor</a>"
            f" <a href='/about'>About</a></nav><main>{body}</main></body></html>")


class SiteHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        name, address, kind, doctors = SITES[self.server.site_key]
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            body, ctype = "User-agent: *\nAllow: /\n", "text/plain"
        elif path == "/find-a-doctor" and doctors:
            body = page(name, "<h1>Find a Doctor</h1><div class='doctor-grid'>"
                        + "".join(card(*d) for d in doctors) + f"</div><footer>{name}, {address}</footer>")
            ctype = "text/html; charset=utf-8"
        elif path == "/about":
            body, ctype = page(name, f"<h1>About {name}</h1><p>{name} is at {address}.</p>"), "text/html; charset=utf-8"
        elif path == "/":
            body = page(name, f"<h1>{name}</h1><p>{kind} at {address}.</p><p><a href='/find-a-doctor'>Our Doctors</a></p>")
            ctype = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def hospital_web():
    urls, servers = {}, []
    for index, key in enumerate(ORDER):
        server = ThreadingHTTPServer((f"127.0.0.{index + 2}", 0), SiteHandler)
        server.site_key = key
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        urls[key] = f"http://127.0.0.{index + 2}:{server.server_address[1]}/"
    yield urls
    for server in servers:
        server.shutdown()


class Search:
    def __init__(self, urls):
        self.urls, self.queries = urls, []

    def search(self, query):
        self.queries.append(query)
        return [self.urls[k] for k in ORDER] + ["https://www.practo.com/delhi/oncologist"]


def make_crawler(pages=12):
    def make():
        return Crawler(ScrapeConfig(max_pages=pages, delay_seconds=0, allow_private_hosts=True, render="never",
                                    retries=0, use_sitemaps=False, stale_pages=0))
    return make


# ---- reading the prompt ---------------------------------------------------------------------

@pytest.mark.parametrize("prompt,count,target", [
    ("Find 10 cancer hospitals in Delhi and their oncologist pages", 10, "oncologists"),
    ("find 5 hospitals in Pune with oncologists", 5, "oncologists"),
    ("List 3 hospitals in Navi Mumbai doctors pages", 3, "doctors"),
])
def test_simple_plan_reads_count_kind_city(prompt, count, target):
    plan = simple_doctor_plan(prompt)
    assert plan and plan.requested_institutions == count and plan.target == target
    assert {c.id for c in plan.criteria} == {"institution_type", "location"}


@pytest.mark.parametrize("prompt", ["Find 10 cancer hospitals in Delhi not Noida and their oncologists",
                                    "Find 5 hospitals near me with oncologists", "oncologists chahiye Delhi mein"])
def test_simple_plan_refuses_anything_it_could_misread(prompt):
    assert simple_doctor_plan(prompt) is None


def test_read_request_named_hospitals_and_specialties():
    named = read_request("Get doctors of Tata Memorial Hospital and Rajiv Gandhi Cancer Institute")
    assert named.names == ["Tata Memorial Hospital", "Rajiv Gandhi Cancer Institute"]
    special = read_request("5 hospitals in Pune with radiation oncologists who have email")
    assert special.count == 5 and special.location == "Pune"
    assert special.specialties == ["Radiation Oncology"] and special.unfiltered_conditions


def test_queries_and_portal_exclusion():
    queries = site_queries("cancer", "Delhi")
    assert "cancer hospitals in Delhi" in queries and all(len(q) < 200 for q in queries)
    assert exclusion_reason("https://www.practo.com/delhi/oncologist")
    assert exclusion_reason("https://www.justdial.com/Delhi/Oncologists")
    assert not exclusion_reason("https://www.rgcirc.org/find-a-doctor/")
    hosts = candidate_hosts([{"url": "https://www.practo.com/x"}, {"url": "https://www.rgcirc.org/doctors",
                                                                    "title": "Rajiv Gandhi Cancer Institute"}])
    assert hosts == [("rgcirc.org", "https://www.rgcirc.org/")]
    names = names_from_results([{"title": "Rajiv Gandhi Cancer Institute and Research Centre - Delhi"},
                                {"title": "Top 10 Oncologists in Delhi | Practo"}])
    assert any("Rajiv Gandhi Cancer Institute" in n for n in names)


# ---- proving each site --------------------------------------------------------------------

def test_survey_accepts_a_real_cancer_hospital(hospital_web):
    hospital, rejected = survey_site(hospital_web["lotus"], make_crawler(), kind="cancer", location="Delhi")
    assert rejected is None, rejected
    assert hospital.name == "Lotus Cancer Hospital"
    assert hospital.oncologists_found == 3 and any("find-a-doctor" in u for u in hospital.doctor_urls)
    assert set(hospital.evidence) >= {"location", "institution_type", "doctors"}


@pytest.mark.parametrize("key,reason", [("deccan", "not Delhi"), ("nodoc", "No page on this site listed"),
                                        ("eyecare", "No page on this site listed")])
def test_survey_rejects_for_a_stated_reason(hospital_web, key, reason):
    hospital, rejected = survey_site(hospital_web[key], make_crawler(), kind="cancer", location="Delhi")
    assert hospital is None and reason in rejected.reason


def test_discover_hospitals_keeps_only_proven_sites(hospital_web):
    search = Search(hospital_web)
    hospitals, rejected, notes = discover_hospitals(search, make_crawler(), want=3, kind="cancer", location="Delhi",
                                                    max_searches=2, workers=2, site_seconds=30)
    assert [h.name for h in hospitals] == ["Lotus Cancer Hospital"]
    assert len(rejected) == 3
    assert not any("practo" in r.host for r in rejected)  # portals are never even tried


# ---- whole workflow ------------------------------------------------------------------------------

@pytest.fixture
def workflow(tmp_path, monkeypatch, hospital_web):
    import oncoscraper.prompt_jobs as jobs
    requests = []

    class SearchHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            requests.append(self.path)
            results = [{"url": hospital_web[k], "title": SITES[k][0]} for k in ORDER]
            data = json.dumps({"results": results}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), SearchHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SEARXNG_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("MODEL_PROVIDERS", "ollama")
    monkeypatch.setattr(jobs, "ScrapeConfig", lambda **kw: ScrapeConfig(
        **{**kw, "delay_seconds": 0, "retries": 0, "allow_private_hosts": True, "use_sitemaps": False}))
    calls = []
    monkeypatch.setattr(jobs, "ModelRouter", lambda *a, **k: calls.append(a) or SimpleNamespace(close=lambda: None))
    history = Store(tmp_path / "history.db")
    db = DiscoveryDB(tmp_path / "prompt-discovery.db")
    manager = PromptManager(db, history, tmp_path / "results")
    monkeypatch.setattr(manager, "start_worker", lambda: None)
    yield manager, requests, calls
    manager.close()
    server.shutdown()
    db.db.close()


def test_prompt_to_oncologists_excel_without_any_model(workflow):
    manager, searches, calls = workflow
    job = manager.create(PromptIn(prompt="Find 1 cancer hospital in Delhi and their oncologists", render="never",
                                  pages_per_hospital=20, site_pages=10, site_seconds=30), start=False)
    manager.execute(job["id"])
    saved = manager.db.get(job["id"])
    assert saved["state"] in {"done", "partial"}, saved.get("error")
    assert searches and calls == []
    report = saved["report"]
    assert report["plan"]["hospitals"][0]["name"] == "Lotus Cancer Hospital"
    wb = load_workbook(saved["xlsx"], read_only=True, data_only=True)
    text = json.dumps([list(ws.values) for ws in wb.worksheets], default=str)
    wb.close()
    assert "meera.kapoor@lotuscancer.in" in text and "arjun.sethi@lotuscancer.in" in text
    assert "sanjay.gupta@" not in text          # cardiologist: not an oncologist
    assert "kavita.rao@" not in text            # the chain places her in Mumbai
    assert any("outside Delhi" in i for i in report["issues"])
    assert saved["stats"]["oncologists"] == 2
    assert len(manager.history.run_doctors(job["id"])) == 2
    assert manager.history.get_run(job["id"])["kind"] == "prompt"


def test_specialty_and_unfiltered_conditions_are_reported(workflow):
    manager, _, _ = workflow
    job = manager.create(PromptIn(prompt="Find 2 cancer hospitals in Delhi with radiation oncologists who have email",
                                  render="never", pages_per_hospital=20, site_pages=10, site_seconds=30), start=False)
    manager.execute(job["id"])
    saved = manager.db.get(job["id"])
    issues = " ".join(saved["report"]["issues"])
    assert "not applied" in issues and "Requested 2 hospitals" in issues
    docs = manager.history.run_doctors(job["id"])
    assert [d.get("name") for d in docs] == ["Dr. Arjun Sethi"]


def test_hospital_list_only_and_upload_template(workflow, tmp_path):
    from oncoscraper.sources import read_sources
    manager, _, _ = workflow
    job = manager.create(PromptIn(prompt="Find 1 cancer hospital in Delhi and their oncologists", auto_scrape=False,
                                  site_pages=10, site_seconds=30), start=False)
    manager.execute(job["id"])
    saved = manager.db.get(job["id"])
    assert saved["state"] == "sources_ready"
    path = export_sources(saved, tmp_path / "sources.xlsx")
    sources, problems = read_sources(path)
    assert [s.hospital for s in sources] == ["Lotus Cancer Hospital"] and not problems
    assert any("find-a-doctor" in u for u in sources[0].urls)


def test_pause_resume_and_restart(workflow):
    manager, searches, _ = workflow
    job = manager.create(PromptIn(prompt="Find 1 cancer hospital in Delhi and their oncologists"), start=False)
    assert manager.action(job["id"], "pause")["state"] == "paused" and not searches
    assert manager.action(job["id"], "resume")["state"] == "queued"
    manager.update(job["id"], state="running")
    again = PromptManager(manager.db, manager.history, manager.out_dir)
    assert again.db.get(job["id"])["state"] == "interrupted"


def test_portal_api(workflow, monkeypatch):
    import oncoscraper.web as web
    manager, _, _ = workflow
    monkeypatch.setattr(web, "_STORE", manager.history)
    monkeypatch.setattr(web, "_PROMPT_MANAGER", manager)
    monkeypatch.setattr(web, "OUT_DIR", manager.out_dir)
    monkeypatch.setenv("ONCOSCRAPER_DB", str(manager.history.path))
    client = TestClient(web.app)
    assert client.get("/api/discovery/config").status_code == 200
    assert client.get("/static/prompt.js").status_code == 200
    r = client.post("/api/discovery", json={"prompt": "Find 1 cancer hospital in Delhi and their oncologists",
                                            "render": "never", "pages_per_hospital": 20, "site_pages": 10})
    assert r.status_code == 200
    job_id = r.json()["id"]
    assert client.get(f"/api/discovery/{job_id}/download.xlsx").status_code == 409
    manager.execute(job_id)
    status = client.get(f"/api/discovery/{job_id}").json()
    assert status["download_ready"] and "xlsx" not in status
    assert client.get(f"/api/runs/{job_id}/download.xlsx").status_code == 200
    assert client.get(f"/api/discovery/{job_id}/sources.xlsx").status_code == 200
    assert client.get(f"/api/discovery/{job_id}/evidence.json").status_code == 200
    assert any(r["id"] == job_id and r["kind"] == "prompt" for r in client.get("/api/runs").json()["runs"])
    assert client.post("/api/discovery", json={"prompt": "   "}).status_code == 422
    assert client.post("/api/discovery", json={"prompt": "x", "search_url": "http://evil"}).status_code == 422
    assert client.delete(f"/api/runs/{job_id}").status_code == 200
    assert manager.db.get(job_id) is None


# ---- person filter --------------------------------------------------------------------------

def test_filter_keeps_existing_records_and_never_writes_contacts():
    from oncoscraper.prompt_discovery import Report
    from oncoscraper.verified_discovery import RecordBatchDecision
    a = DoctorRecord(source_url="https://h.in/d")
    a.set("name", "Dr. A", "card")
    a.set("languages", ["Tamil"], "card", "Languages: Tamil")
    b = DoctorRecord(source_url="https://h.in/d")
    b.set("name", "Dr. B", "card")
    report = Report(prompt="Tamil speaking oncologists", plan={"criteria": [
        {"id": "lang", "requirement": "Speaks Tamil", "prompt_quote": "Tamil speaking", "applies_to": "person"}]})

    class Model:
        def ask(self, schema, instruction, payload):
            assert schema is RecordBatchDecision
            return RecordBatchDecision(decisions=[
                {"id": r["id"], "accepted": "Tamil" in r["record_text"], "reason": "stated",
                 "evidence": [{"criterion_id": "lang", "quote": "Tamil"}] if "Tamil" in r["record_text"] else []}
                for r in payload["records"]])

    kept, audit = filter_records(report, [a, b], Model())
    assert kept == [a] and len(audit) == 2


# ---- crawler upgrades ------------------------------------------------------------------------

def test_personal_links_and_patient_stories():
    from oncoscraper.extract import extract_page
    from oncoscraper.models import Page
    html = """<html><head><title>Our Doctors | Lotus Cancer Hospital</title></head><body><main>
    <h2>Our Oncologists</h2><div class="doctor-grid">
    <div class="doctor-card"><h3>Dr. Meera Kapoor</h3><p>Senior Consultant, Medical Oncology</p>
      <a href="https://www.linkedin.com/in/meera-kapoor-onco">LinkedIn</a>
      <a href="https://scholar.google.com/citations?user=AbC123xyz">Scholar</a></div>
    <div class="doctor-card"><h3>Dr. Arjun Sethi</h3><p>Consultant, Radiation Oncology</p>
      <a href="https://orcid.org/0000-0002-1825-0097">ORCID</a></div>
    <div class="doctor-card"><h3>Dr. Kavita Rao</h3><p>Consultant, Surgical Oncology</p></div></div>
    <h2>Patient Stories</h2><div class="testimonial-slider">
    <div class="item"><h3>Dr. Ramesh Iyer</h3><p>Retired professor, treated for lymphoma by our oncology team</p></div></div>
    <footer><a href="https://www.linkedin.com/company/lotus-cancer-hospital">Follow us</a></footer>
    </main></body></html>"""
    result = extract_page(Page(url="https://lotus.example/doctors", status=200, html=html, final_url="https://lotus.example/doctors"))
    by_name = {d.get("name"): d for d in result.doctors}
    assert "Dr. Ramesh Iyer" not in by_name
    assert by_name["Dr. Meera Kapoor"].get("research_links") == [
        "https://www.linkedin.com/in/meera-kapoor-onco", "https://scholar.google.com/citations?user=AbC123xyz"]
    assert by_name["Dr. Arjun Sethi"].get("research_links") == ["https://orcid.org/0000-0002-1825-0097"]
    assert not any("company" in str(d.get("research_links")) for d in result.doctors)
