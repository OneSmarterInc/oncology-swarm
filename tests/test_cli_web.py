import json
import time

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from oncoscraper import web
from oncoscraper.cli import main
from oncoscraper.extract import extract_page
from oncoscraper.models import Page


def test_cli_scrape_writes_excel(site_url, tmp_path, capsys, monkeypatch):
    out = tmp_path / "onc.xlsx"
    monkeypatch.setenv("ONCOSCRAPER_ALLOW_PRIVATE", "1")
    rc = main(["scrape", site_url, "--out", str(out), "--delay", "0", "--render", "never", "--max-pages", "60"])
    assert rc == 0
    wb = load_workbook(out)
    assert wb.sheetnames == ["Summary", "Oncologists", "All doctors", "Evidence", "Pages"]
    names = [r[0].value for r in wb["Oncologists"].iter_rows(min_row=2)]
    assert "Dr. Meenu Walia" in names and "Dr. Sunil Kumar" not in names
    assert wb["Evidence"].max_row > 50
    assert "oncologists" in capsys.readouterr().out


def test_cli_file_command(tmp_path, capsys):
    p = tmp_path / "p.html"
    p.write_text("<!-- source: https://h.example/doctor/dr-x-y -->\n<html><body><h1>Dr. Xavier Yadav</h1>"
                 "<p>Consultant - Radiation Oncology</p><p>MBBS, MD (Radiotherapy)</p></body></html>")
    assert main(["file", str(p)]) == 0
    out = capsys.readouterr().out
    assert "Dr. Xavier Yadav" in out and "Radiation Oncology" in out


def test_web_ui_job_flow(site_url, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "OUT_DIR", tmp_path)
    monkeypatch.setenv("ONCOSCRAPER_ALLOW_PRIVATE", "1")
    client = TestClient(web.app)
    assert "OncoScraper" in client.get("/").text
    jid = client.post("/api/jobs", json={"urls": [site_url], "delay_seconds": 0, "render": "never", "max_pages": 60}).json()["id"]
    for _ in range(100):
        st = client.get(f"/api/jobs/{jid}").json()
        if st["state"] != "running":
            break
        time.sleep(0.1)
    assert st["state"] == "done", st
    assert st["progress"]["oncologists"] >= 11
    assert all(d["is_oncology"] for d in st["doctors"])
    r = client.get(f"/api/jobs/{jid}/download.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "Dr. Asha Rao" in client.get(f"/api/jobs/{jid}/download.csv").text
    assert json.loads(client.get(f"/api/jobs/{jid}/download.json").text)["stats"]["oncologists"] >= 11


def test_next_app_router_stream():
    payload = json.dumps({"doctor": {"name": "Dr. Omkar Pai", "speciality": "Medical Oncology", "designation": "Consultant",
                                     "qualification": "MBBS, MD, DM", "experience": "9 Years"}})
    chunk = json.dumps("5:" + payload)[1:-1]
    html = f'<html><body><div id="__next"></div><script>self.__next_f.push([1,"{chunk}"])</script></body></html>'
    pr = extract_page(Page("https://h.example/doctors/dr-omkar-pai", "https://h.example/doctors/dr-omkar-pai", 200, html))
    d = pr.doctors[0]
    assert d.get("name") == "Dr. Omkar Pai" and d.get("experience_years") == "9" and d.is_oncology


def test_jsonld_itemlist_listing():
    items = [{"@type": "Physician", "name": f"Dr. {n}", "medicalSpecialty": "Surgical Oncology",
              "url": f"/doctors/dr-{n.lower().replace(' ', '-')}"} for n in ("Ira Sen", "Om Rao", "Tara Das")]
    html = ('<html><head><script type="application/ld+json">' + json.dumps({"@type": "ItemList", "itemListElement": items})
            + '</script></head><body><h1>Our Oncologists</h1></body></html>')
    pr = extract_page(Page("https://h.example/oncologists", "https://h.example/oncologists", 200, html))
    assert pr.page_type == "listing" and len(pr.doctors) == 3
    assert {d.profile_url for d in pr.doctors} == {"https://h.example/doctors/dr-ira-sen", "https://h.example/doctors/dr-om-rao", "https://h.example/doctors/dr-tara-das"}
