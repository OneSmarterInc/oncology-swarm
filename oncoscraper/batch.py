"""Crawl a list of hospitals one by one and combine the results.

Input: a CSV with at least a URL column. Recognised columns (any subset):
  hospital, official_domain, doctors_list_url, oncology_url, start_url, url, max_pages
Each hospital gets its own page budget. Results are saved per hospital as they
finish, so an interrupted run can be resumed without re-crawling finished sites.
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Callable

from .config import ScrapeConfig
from .crawl import Crawler, CrawlResult, Merger, PageLog
from .export import to_xlsx
from .models import DoctorRecord


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "site"


def read_sources(path: str | Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            urls: list[str] = []
            for key in ("start_url", "url", "oncology_url", "doctors_list_url"):
                for u in re.split(r"[\s;|]+", row.get(key, "")):
                    if (u.lower().startswith(("http://", "https://")) and u not in urls
                            and not u.lower().split("?")[0].endswith(".pdf")):  # PDFs are not read yet
                        urls.append(u)
            if not urls and row.get("official_domain"):
                urls.append("https://" + row["official_domain"].removeprefix("https://").removeprefix("http://").strip("/") + "/")
            if not urls:
                continue
            name = row.get("hospital") or re.sub(r"^https?://", "", urls[0]).split("/")[0]
            mp = row.get("max_pages", "")
            pattern = row.get("profile_pattern", "")
            patterns = [pattern] if pattern.startswith("/") and len(pattern) > 2 and "<" not in pattern else []
            rows.append({"hospital": name, "urls": urls, "max_pages": int(mp) if mp.isdigit() else None,
                         "patterns": patterns})
    return rows


def run_batch(sources: list[dict], out: str | Path, base: ScrapeConfig, per_site_pages: int,
              resume: bool = True, progress: Callable[[dict], None] | None = None) -> Path:
    progress = progress or (lambda e: None)
    out = Path(out)
    site_dir = out.with_name(out.stem + "_sites")
    site_dir.mkdir(parents=True, exist_ok=True)
    merger = Merger()
    pages: list[PageLog] = []
    summary: list[dict] = []
    t0 = time.time()
    for i, src in enumerate(sources, 1):
        slug = _slug(src["hospital"])
        jpath = site_dir / f"{slug}.json"
        progress({"event": "site_start", "index": i, "total": len(sources), "hospital": src["hospital"], "urls": src["urls"]})
        if resume and jpath.is_file():
            data = json.loads(jpath.read_text(encoding="utf-8"))
            status = "resumed"
        else:
            cfg = ScrapeConfig(**{**base.__dict__, "max_pages": src["max_pages"] or per_site_pages,
                                  "extra_profile_patterns": list(base.extra_profile_patterns) + src.get("patterns", [])})
            try:
                res = Crawler(cfg, progress).run(src["urls"])
                data = {"hospital": src["hospital"], "urls": src["urls"], "stats": res.stats,
                        "doctors": [d.to_full_dict() for d in res.doctors],
                        "pages": [p.__dict__ for p in res.pages], "error": None}
            except Exception as exc:  # one bad site must not stop the batch
                data = {"hospital": src["hospital"], "urls": src["urls"], "stats": {}, "doctors": [], "pages": [],
                        "error": f"{type(exc).__name__}: {exc}"}
            jpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            status = "error" if data["error"] else "done"
        docs = [DoctorRecord.from_full_dict(d) for d in data["doctors"]]
        for d in docs:
            d.source_hospital = src["hospital"]
            merger.add(d)
        pages.extend(PageLog(**p) for p in data["pages"])
        onco = [d for d in docs if d.is_oncology]
        blocked = sum(1 for p in data["pages"] if p["page_type"] == "error")
        summary.append({
            "hospital": src["hospital"], "status": status, "pages": len(data["pages"]), "doctors": len(docs),
            "oncologists": len(onco), "with_email": sum(1 for d in onco if d.get("email")),
            "start_urls": " ".join(src["urls"]),
            "note": data.get("error") or (f"{blocked} pages failed or blocked" if blocked else ""),
        })
        progress({"event": "site_done", **summary[-1]})
    result = CrawlResult([u for s in sources for u in s["urls"]], merger.records, pages)
    result.stats = {
        "hospitals": len(sources), "pages_fetched": len(pages), "doctors_found": len(result.doctors),
        "oncologists": len(result.oncologists),
        "oncologists_with_email": sum(1 for d in result.oncologists if d.get("email")),
        "seconds": round(time.time() - t0, 1),
    }
    return to_xlsx(result, out, sites=summary)
