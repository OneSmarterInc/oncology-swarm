"""Crawl a list of hospitals one by one and combine the results.

Input: a hospital list in the standard template (.xlsx or .csv, see sources.py).
Each hospital gets its own page budget and settings. Results are saved per
hospital as they finish, so an interrupted run resumes without re-crawling
finished sites.
"""
from __future__ import annotations

import json
from collections import Counter
import re
import threading
import time
from pathlib import Path
from typing import Callable

from .config import ScrapeConfig
from .contacts import demote_shared, demote_sitewide
from .crawl import Crawler, CrawlResult, Merger, PageLog
from .export import to_xlsx
from .models import DoctorRecord
from .sources import Source


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "site"


def _reason(note: str) -> str:
    """Short, readable reason for a failed page."""
    n = note.lower()
    if "robots.txt" in n:
        return "blocked by robots.txt"
    if "certificate" in n or "ssl" in n:
        return "SSL certificate error"
    if "proxy" in n:
        return "proxy error"
    if "timeout" in n or "timed out" in n:
        return "timeout"
    if "connecterror" in n or "getaddrinfo" in n or "name resolution" in n:
        return "cannot connect"
    m = re.search(r"http (\d{3})", n)
    if m:
        return f"HTTP {m.group(1)}" + (" even in browser" if "real browser" in n else "")
    return note[:80] or "unknown"


def _failure_note(failed: list[dict], total: int) -> str:
    """Readable summary. Dead links (404/410) are links on the hospital's own site to pages
    that no longer exist; they are reported separately from real failures."""
    reasons = Counter(_reason(p.get("note") or "") for p in failed)
    dead = reasons.pop("HTTP 404", 0) + reasons.pop("HTTP 410", 0)
    parts = []
    if dead:
        parts.append(f"{dead} dead link{'s' if dead > 1 else ''} on the site (404, page removed)")
    for r, n in reasons.most_common(3):
        label = {"HTTP 500": "server error (500)", "HTTP 502": "server error (502)", "HTTP 503": "server busy (503)"}.get(r, r)
        parts.append(f"{n} {label}")
    if not dead or reasons:
        return f"{sum(reasons.values())} of {total} pages could not be read: " + "; ".join(parts)
    return "; ".join(parts)


def snapshot_xlsx(live: dict, out: str | Path) -> Path:
    """Write what has been found so far (finished hospitals + the one in progress)."""
    merger = Merger()
    for d in list(live.get("merger").records if live.get("merger") else []):
        merger.add(DoctorRecord.from_full_dict(d.to_full_dict()))
    pages = list(live.get("pages") or [])
    summary = [dict(r) for r in live.get("summary") or []]
    crawler, src = live.get("crawler"), live.get("src")
    if crawler is not None and src is not None:
        current = [DoctorRecord.from_full_dict(d.to_full_dict()) for d in list(crawler.merger.records)]
        demote_sitewide(current, {k: set(v) for k, v in list(crawler.value_pages.items())})
        for c in current:
            c.source_hospital = src.hospital
            if not c.get("hospital"):
                c.set("hospital", src.hospital, "hospital list", "Hospital Name column of the uploaded list", c.source_url)
            merger.add(c)
    demote_shared(merger.records)
    if crawler is not None and src is not None:  # counted after shared desk numbers are moved
        mine = [d for d in merger.records if d.source_hospital == src.hospital]
        onco = [d for d in mine if d.is_oncology]
        summary.append({"row": src.row, "hospital": src.hospital, "status": "in progress",
                        "pages": "", "doctors": len(mine), "oncologists": len(onco),
                        "with_phone": sum(1 for d in onco if d.get("phone")),
                        "with_email": sum(1 for d in onco if d.get("email")),
                        "with_any_contact": sum(1 for d in onco if any(d.get(f) for f in CONTACT_FIELDS)),
                        "start_urls": " ".join(src.urls), "note": "partial: crawl still running"})
    sources = live.get("sources") or []
    result = CrawlResult([u for s in sources for u in s.urls], merger.records, pages)
    onco = result.oncologists
    result.stats = {
        "partial": "yes - downloaded while the batch was running",
        "hospitals_finished": len(live.get("summary") or []), "hospitals_total": len(sources),
        "pages_fetched": len(pages), "doctors_found": len(result.doctors), "oncologists": len(onco),
        "oncologists_with_doctor_phone": sum(1 for d in onco if d.get("phone")),
        "oncologists_with_doctor_email": sum(1 for d in onco if d.get("email")),
        "oncologists_with_any_contact": sum(1 for d in onco if any(d.get(f) for f in CONTACT_FIELDS)),
        "seconds": round(time.time() - live.get("t0", time.time()), 1),
    }
    return to_xlsx(result, out, sites=summary)


CONTACT_FIELDS = ("phone", "email", "contact_phone", "contact_email")


def run_batch(sources: list[Source], out: str | Path, base: ScrapeConfig, per_site_pages: int,
              resume: bool = True, progress: Callable[[dict], None] | None = None,
              stop_event: threading.Event | None = None, live: dict | None = None) -> Path:
    progress = progress or (lambda e: None)
    stop_event = stop_event or threading.Event()
    out = Path(out)
    site_dir = out.with_name(out.stem + "_sites")
    site_dir.mkdir(parents=True, exist_ok=True)
    merger = Merger()
    pages: list[PageLog] = []
    summary: list[dict] = []
    t0 = time.time()
    if live is not None:  # lets the web UI write a partial Excel at any time
        live.update(merger=merger, pages=pages, summary=summary, sources=sources, crawler=None, src=None, t0=t0)
    for i, src in enumerate(sources, 1):
        if stop_event.is_set():
            break
        slug = f"{_slug(src.hospital)}-r{src.row}"
        jpath = site_dir / f"{slug}.json"
        progress({"event": "site_start", "index": i, "total": len(sources), "hospital": src.hospital, "urls": src.urls})
        if resume and jpath.is_file():
            data = json.loads(jpath.read_text(encoding="utf-8"))
            status = "resumed"
        else:
            cfg = ScrapeConfig(**{
                **base.__dict__,
                "max_pages": src.max_pages or per_site_pages,
                "extra_profile_patterns": list(base.extra_profile_patterns) + src.patterns,
                "include_subdomains": base.include_subdomains or src.include_subdomains,
                "render": src.render or base.render,
            })
            try:
                crawler = Crawler(cfg, progress, stop_event)
                if live is not None:
                    live.update(crawler=crawler, src=src)
                res = crawler.run(src.urls)
                data = {"hospital": src.hospital, "urls": src.urls, "stats": res.stats,
                        "doctors": [d.to_full_dict() for d in res.doctors],
                        "pages": [p.__dict__ for p in res.pages], "error": None}
            except Exception as exc:  # one bad site must not stop the batch
                data = {"hospital": src.hospital, "urls": src.urls, "stats": {}, "doctors": [], "pages": [],
                        "error": f"{type(exc).__name__}: {exc}"}
            stopped = bool(data["stats"].get("stopped_early"))
            all_failed = bool(data["pages"]) and all(p["page_type"] == "error" for p in data["pages"])
            if not stopped and not data["error"] and not all_failed:  # a hospital cut short by Stop is crawled again next time
                jpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            status = "error" if data["error"] else ("stopped" if stopped else ("failed" if all_failed else "done"))
        docs = [DoctorRecord.from_full_dict(d) for d in data["doctors"]]
        for d in docs:
            d.source_hospital = src.hospital
            if not d.get("hospital"):  # the name from the hospital list beats an empty cell
                d.set("hospital", src.hospital, "hospital list", "Hospital Name column of the uploaded list", d.source_url)
            merger.add(d)
        pages.extend(PageLog(**p) for p in data["pages"])
        if live is not None:
            live.update(crawler=None, src=None)
        onco = [d for d in docs if d.is_oncology]
        failed = [p for p in data["pages"] if p["page_type"] == "error"]
        robots = sum(1 for p in failed if "robots.txt" in (p.get("note") or ""))
        note = data.get("error") or ""
        if not note and failed:
            note = _failure_note(failed, len(data["pages"]))
        if src.warnings:
            note = (note + "; " if note else "") + "; ".join(src.warnings)
        summary.append({
            "row": src.row, "hospital": src.hospital, "status": status, "pages": len(data["pages"]),
            "doctors": len(docs), "oncologists": len(onco),
            "with_phone": sum(1 for d in onco if d.get("phone")),
            "with_email": sum(1 for d in onco if d.get("email")),
            "with_any_contact": sum(1 for d in onco if any(d.get(f) for f in ("phone", "email", "contact_phone", "contact_email"))),
            "start_urls": " ".join(src.urls), "note": note,
        })
        progress({"event": "site_done", **summary[-1]})
    demote_shared(merger.records)
    result = CrawlResult([u for s in sources for u in s.urls], merger.records, pages)
    onco = result.oncologists
    result.stats = {
        "hospitals": len(summary), "pages_fetched": len(pages), "doctors_found": len(result.doctors),
        "oncologists": len(onco),
        "oncologists_with_doctor_phone": sum(1 for d in onco if d.get("phone")),
        "oncologists_with_doctor_email": sum(1 for d in onco if d.get("email")),
        "oncologists_with_any_contact": sum(1 for d in onco if any(d.get(f) for f in ("phone", "email", "contact_phone", "contact_email"))),
        "seconds": round(time.time() - t0, 1), "stopped_early": stop_event.is_set(),
    }
    progress({"event": "batch_done", **result.stats})
    return to_xlsx(result, out, sites=summary)
