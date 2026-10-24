"""Opt-in real-site integration pilot. Fetches robots and five public biographies.

Default uses normal public-DNS validation. --remote-dns-proxy is a test adapter
for this sandbox's remote-resolving SOCKS proxy ONLY: allowlists the two audited
hosts instead of requiring unavailable local DNS. It is not exposed in the app.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attorneyscraper.engine import Manager
from attorneyscraper.export import csv_bytes, parse_sources
from attorneyscraper.fetch import Fetcher, FetchError
from attorneyscraper.models import RunConfig, canonical_url, site_key
from attorneyscraper.store import Store

EXPECTED = {
    "Juliane C. Miller": {
        "category": "defense",
        "email": "jcmiller@hccw.com",
        "status": "confirmed",
    },
    "Eli Fuchsberg": {
        "category": "plaintiff",
        "email": "e.fuchsberg@fuchsberg.com",
        "status": "confirmed",
    },
    "Kathleen Kettles": {
        "category": "nurse",
        "email": "k.kettles@fuchsberg.com",
        "status": "confirmed",
    },
    "Emma Klein": {
        "category": "plaintiff",
        "email": "e.klein@fuchsberg.com",
        "status": "confirmed",
    },
    "Jacqueline Cabrera": {
        "category": None,
        "email": "j.cabrera@fuchsberg.com",
        "status": "excluded",
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-dns-proxy", action="store_true")
    parser.add_argument("--out", default="results/real-pilot")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    sources = parse_sources(
        "pilot.csv", (root / "examples/real-profile-pilot.csv").read_bytes()
    )
    store = Store(out / "pilot.db")
    store.recover()
    config = RunConfig(
        name="Five real biographies, two official firms",
        sources=sources,
        max_pages=10,
        max_depth=0,
        parallel_sites=2,
        delay=1.5,
    )

    def validate(url):
        value = canonical_url(url)
        if site_key(value) not in {"hccw.com", "fuchsberg.com"}:
            raise FetchError("Outside audited pilot hosts")
        return value

    def factory(**kwargs):
        if args.remote_dns_proxy:
            kwargs["validate"] = validate
        fetcher = Fetcher(**kwargs)
        original = fetcher.get

        def get(url, site):
            page = original(url, site)
            # Temporary real HTML snapshots enable faithful regressions. Not packaged.
            (out / (url.rstrip("/").split("/")[-1] + ".html")).write_text(
                page.text, encoding="utf-8"
            )
            return page

        fetcher.get = get
        return fetcher

    manager = Manager(store, factory)
    rid = store.create(config)
    began = time.monotonic()
    manager.start(rid)
    manager.thread.join()
    records = store.records(rid)
    found = {p["name"]: p for p in records}
    checks = []
    for name, want in EXPECTED.items():
        person = found.get(name)
        checks.append(
            {
                "name": name,
                "found": person is not None,
                "status_ok": bool(person and person["status"] == want["status"]),
                "category_ok": bool(
                    person
                    and (
                        want["category"] is None
                        or want["category"] in person["categories"]
                    )
                ),
                "email_ok": bool(
                    person
                    and any(
                        c["value"] == want["email"] and c["owner"] == "individual"
                        for c in person["contacts"]
                    )
                ),
            }
        )
    report = {
        "run_id": rid,
        "elapsed_seconds": round(time.monotonic() - began, 2),
        "remote_dns_adapter": args.remote_dns_proxy,
        "run": store.get(rid),
        "checks": checks,
        "pages": store.pages(rid),
        "found": len(records),
        "expected": len(EXPECTED),
        "unexpected_names": sorted(set(found) - set(EXPECTED)),
        "support_attribution_ok": all(
            c["owner"] == "support_staff"
            for c in found.get("Juliane C. Miller", {}).get("contacts", [])
            if c["value"] in {"kallen@hccw.com", "ndore@hccw.com"}
        ),
        "fax_not_phone": all(
            c["value"] != "+1 212-398-1532" for p in records for c in p["contacts"]
        ),
    }
    report["passed"] = (
        all(
            all(c[k] for k in ("found", "status_ok", "category_ok", "email_ok"))
            for c in checks
        )
        and not report["unexpected_names"]
        and report["support_attribution_ok"]
        and report["fax_not_phone"]
    )
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "contacts.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (out / "contacts.csv").write_bytes(csv_bytes(records))
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "run_id",
                    "elapsed_seconds",
                    "found",
                    "expected",
                    "passed",
                    "checks",
                )
            },
            indent=2,
        )
    )
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
