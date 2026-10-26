"""Repeatable local synthetic load measurement; never touches user data."""

import copy
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient

from attorneyscraper.extract import extract
from attorneyscraper.models import RunConfig
from attorneyscraper.web import create_app


def main():
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Path(tmp) / "load.db")
        store = app.state.store
        rid = store.create(RunConfig(sources=[{"url": "https://firm.com/"}]))
        example = extract(
            "<h1>Jane Smith</h1><p>Attorney handles HIPAA and healthcare compliance.</p><p>jsmith@firm.com</p>",
            "https://firm.com/jane-smith",
            "Example Firm",
        )["records"][0]
        with store.db() as db:
            for i in range(10000):
                url = f"https://firm.com/person-{i}"
                person = copy.deepcopy(example)
                person.update(
                    key=str(i), name=f"Person {i}", profile_url=url, source_url=url
                )
                db.execute(
                    "INSERT INTO observations VALUES(?,?,?,?)",
                    (rid, url, str(i), json.dumps(person)),
                )
                db.execute(
                    "INSERT INTO pages(run_id,host,url,depth,priority,status) VALUES(?,?,?,1,1,'done')",
                    (rid, "firm.com", url),
                )
        measurements = {}
        with TestClient(app) as client:
            for label, url in [
                ("run_snapshot", f"/api/runs/{rid}"),
                ("page_log_100", f"/api/runs/{rid}/pages?limit=100"),
                ("history", "/api/history"),
                ("directory_cold", "/api/directory?limit=50"),
                ("directory_warm", "/api/directory?limit=50"),
                ("summary", "/api/summary"),
                ("xlsx_export", f"/api/export/xlsx?rid={rid}"),
            ]:
                start = time.perf_counter()
                r = client.get(url)
                elapsed = time.perf_counter() - start
                assert r.status_code == 200, (label, r.text[:100])
                if label == "page_log_100":
                    assert len(r.json()) == 100
                if label.startswith("directory"):
                    assert r.json()["total"] == 10000 and len(r.json()["items"]) == 50
                measurements[label] = {
                    "seconds": round(elapsed, 3),
                    "bytes": len(r.content),
                }
        print(
            json.dumps(
                {
                    "synthetic_people": 10000,
                    "synthetic_pages": 10001,
                    "measurements": measurements,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
