"""Local web UI: paste a URL, watch the crawl, download the Excel file."""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .config import ScrapeConfig
from .crawl import Crawler, CrawlResult
from .export import to_csv, to_json, to_xlsx
from .extract import extract_page
from .fetch import Fetcher
from .models import DoctorRecord

STATIC = Path(__file__).parent / "static"
OUT_DIR = Path.cwd() / "results"
app = FastAPI(title="OncoScraper", docs_url=None, redoc_url=None)
_STORE = None


def store():
    """The local database (results/oncoscraper.db next to the Excel files)."""
    global _STORE
    from .store import Store
    path = Path(os.environ.get("ONCOSCRAPER_DB") or OUT_DIR / "oncoscraper.db")
    if _STORE is None or _STORE.path != path:
        _STORE = Store(path)
    return _STORE


def _safe(fn, *a, **k):
    """Saving history must never break a crawl."""
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        print(f"[history] could not save: {exc}")


class JobIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    max_pages: int = Field(200, ge=1, le=5000)
    max_depth: int = Field(4, ge=0, le=10)
    delay_seconds: float = Field(1.5, ge=0, le=30)
    render: str = "auto"
    all_specialties: bool = False
    use_sitemaps: bool = True
    include_subdomains: bool = False


class PageIn(BaseModel):
    url: str
    render: str = "auto"


class Job:
    def __init__(self, body: JobIn):
        self.id = uuid.uuid4().hex[:10]
        self.body = body
        self.state = "running"
        self.log: deque[dict] = deque(maxlen=400)
        self.progress: dict = {"fetched": 0, "doctors_total": 0, "oncologists": 0, "queued": 0}
        self.result: CrawlResult | None = None
        self.error: str | None = None
        self.started = time.time()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.crawler: Crawler | None = None
        self.xlsx: Path | None = None
        first = body.urls[0] if body.urls else ""
        self.site_name = (urlsplit(first if "://" in first else "https://" + first).hostname or first).removeprefix("www.")

    def on_event(self, e: dict) -> None:
        if e["event"] == "page":
            self.progress = {k: e[k] for k in ("fetched", "doctors_total", "oncologists", "queued")}
            if e["fetched"] % 25 == 0 and self.crawler:  # keep the history up to date during long crawls
                _safe(lambda: store().save_doctors(self.id, self.snapshot().doctors, self.site_name))
            self.log.append({"url": e["url"], "type": e["type"], "status": e["status"],
                             "doctors": e["doctors_on_page"], "error": e.get("error")})
        elif e["event"] == "sitemap":
            self.log.append({"url": f"sitemap: {e['urls']} URLs", "type": "info", "status": "", "doctors": 0})

    def run(self) -> None:
        b = self.body
        try:
            cfg = ScrapeConfig(max_pages=b.max_pages, max_depth=b.max_depth, delay_seconds=b.delay_seconds,
                               render=b.render, oncology_only=not b.all_specialties, use_sitemaps=b.use_sitemaps,
                               include_subdomains=b.include_subdomains)
            self.crawler = Crawler(cfg, self.on_event, self.stop, self.pause)
            self.result = self.crawler.run([u.strip() for u in b.urls if u.strip()])
            OUT_DIR.mkdir(exist_ok=True)
            self.xlsx = to_xlsx(self.result, OUT_DIR / f"oncologists-{time.strftime('%Y%m%d-%H%M%S')}-{self.id}.xlsx")
            self.state = "stopped" if self.stop.is_set() else "done"
            _safe(store().save_doctors, self.id, self.result.doctors, self.site_name)
        except Exception as exc:  # surface any crash to the UI
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"
        _safe(store().update_run, self.id, state=self.state, finished=time.time(), error=self.error,
              stats=(self.result.stats if self.result else {}), xlsx=str(self.xlsx) if self.xlsx else None)

    def snapshot(self) -> CrawlResult:
        """What has been found so far, while the crawl is still running (copies; the crawl is not touched)."""
        from . import oncology
        from .contacts import demote_shared, demote_sitewide
        from .models import DoctorRecord
        from .profile import clean_fields
        c = self.crawler
        docs = [DoctorRecord.from_full_dict(d.to_full_dict()) for d in list(c.merger.records)] if c else []
        for d in docs:
            clean_fields(d)
            oncology.veto(d)
        if c:
            demote_sitewide(docs, {k: set(v) for k, v in list(c.value_pages.items())})
        demote_shared(docs)
        res = CrawlResult([u.strip() for u in self.body.urls if u.strip()], docs, list(c.result_pages) if c else [])
        onco = [d for d in docs if d.is_oncology]
        res.stats = {"partial": "yes - downloaded while the crawl was running", "pages_fetched": self.progress.get("fetched", 0),
                     "doctors_found": len(docs), "oncologists": len(onco),
                     "seconds": round(time.time() - self.started, 1)}
        return res

    def doctors(self):
        if self.result:
            docs = self.result.doctors
        elif self.crawler:
            docs = list(self.crawler.merger.records)
        else:
            docs = []
        if not self.body.all_specialties:
            docs = [d for d in docs if d.is_oncology]
        return docs


JOBS: dict[str, Job] = {}


ROW_KEYS = ("name", "designation", "specialty", "sub_specialties", "qualifications", "experience_years", "hospital",
            "city", "phone", "email", "contact_phone", "contact_email", "profile_url", "source_url", "is_oncology",
            "page_type")


def _rows(docs, limit: int = 3000) -> list[dict]:
    out = []
    for d in docs[:limit]:
        x = d.to_dict()
        out.append({k: x.get(k) for k in ROW_KEYS} | {"fields": len(d.filled_fields()),
                                                      "source_hospital": d.source_hospital})
    return out


def _state(job) -> str:
    return "paused" if job.state == "running" and job.pause.is_set() else job.state


def _pause(job, paused: bool) -> dict:
    if job.state != "running":
        raise HTTPException(409, f"cannot pause or resume: the run is {job.state}")
    (job.pause.set if paused else job.pause.clear)()
    _safe(store().update_run, job.id, state=_state(job))
    return {"ok": True, "state": _state(job)}


@app.get("/static/{name}.js")
def static_js(name: str):
    if name not in ("app", "portal", "prompt"):
        raise HTTPException(404, "not found")
    return Response((STATIC / f"{name}.js").read_text(encoding="utf-8"), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.post("/api/jobs")
def start(body: JobIn):
    if body.render not in ("never", "auto", "always"):
        raise HTTPException(400, "render must be never, auto or always")
    job = Job(body)
    JOBS[job.id] = job
    title = ", ".join((urlsplit(u if "://" in u else "https://" + u).hostname or u).removeprefix("www.") for u in body.urls[:3])
    _safe(store().create_run, job.id, "crawl", title, body.model_dump())
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id}


@app.get("/api/jobs/{job_id}")
def status(job_id: str, since: int = 0):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    rows = _rows(job.doctors())
    return {"id": job.id, "state": _state(job), "error": job.error, "progress": job.progress,
            "max_pages": job.body.max_pages, "elapsed": round(time.time() - job.started),
            "stats": job.result.stats if job.result else None, "log": list(job.log)[-60:], "doctors": rows}


@app.post("/api/jobs/{job_id}/stop")
def stop(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job.stop.set()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    job = JOBS.get(job_id) or _missing("job")
    return _pause(job, True)


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    job = JOBS.get(job_id) or _missing("job")
    return _pause(job, False)


def _missing(what: str):
    raise HTTPException(404, f"{what} not found")


@app.get("/api/jobs/{job_id}/download.{fmt}")
def download(job_id: str, fmt: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if fmt not in ("xlsx", "csv", "json"):
        raise HTTPException(400, "format must be xlsx, csv or json")
    partial = job.result is None
    if partial and not job.crawler:
        raise HTTPException(404, "nothing found yet")
    try:
        result = job.snapshot() if partial else job.result
    except Exception as exc:  # the crawl changed under us; a second click works
        raise HTTPException(503, f"could not copy results, try again: {exc}") from None
    tag = "-partial-" + time.strftime("%H%M%S") if partial else ""
    if fmt == "xlsx":
        if not partial and job.xlsx:
            path = job.xlsx
        else:
            OUT_DIR.mkdir(exist_ok=True)
            path = to_xlsx(result, OUT_DIR / f"oncologists-{job.id}{tag}.xlsx")
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "csv":
        docs = result.doctors if job.body.all_specialties else [d for d in result.doctors if d.is_oncology]
        return Response(to_csv(docs).encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="oncologists-{job.id}{tag}.csv"'})
    return Response(to_json(result, not job.body.all_specialties), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="oncologists-{job.id}{tag}.json"'})


@app.post("/api/page")
def test_page(body: PageIn):
    cfg = ScrapeConfig(render=body.render, delay_seconds=0)
    fetcher = Fetcher(cfg)
    try:
        page = fetcher.get(body.url)
    finally:
        fetcher.close()
    pr = extract_page(page)
    return {"status": page.status, "final_url": page.final_url, "error": page.error, "rendered": page.rendered,
            "page_type": pr.page_type, "signals": pr.signals,
            "doctors": [{k: v for k, v in d.to_dict().items() if v not in (None, [], {}, "")} for d in pr.doctors],
            "profile_links": pr.profile_links[:50], "pagination_links": pr.pagination_links}


# ---------------------------------------------------------------------------
# Batch: upload a hospital list (standard template) and crawl every hospital.
class UploadIn(BaseModel):
    filename: str = Field(max_length=200)
    content_base64: str = Field(max_length=20_000_000)


class BatchIn(UploadIn):
    pages_per_site: int = Field(150, ge=1, le=5000)
    workers: int = Field(4, ge=1, le=8)  # hospitals crawled at the same time
    max_depth: int = Field(4, ge=0, le=10)
    delay_seconds: float = Field(1.5, ge=0, le=30)
    render: str = "auto"
    all_specialties: bool = False
    use_sitemaps: bool = True


def _load_upload(body: UploadIn):
    import base64
    import tempfile

    from .sources import HeaderError, read_sources
    suffix = Path(body.filename).suffix.lower()
    if suffix not in (".xlsx", ".xlsm", ".csv"):
        raise HTTPException(400, "Upload an .xlsx or .csv file made from the template")
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except ValueError:
        raise HTTPException(400, "File could not be decoded") from None
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(data)
        tmp = Path(fh.name)
    try:
        return read_sources(tmp)
    except HeaderError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        raise HTTPException(400, f"Could not read the file. Save it as .xlsx or .csv from the template and try again ({exc})") from None
    finally:
        tmp.unlink(missing_ok=True)


class BatchJob:
    def __init__(self, body: BatchIn, sources, run_id: str | None = None, resume: bool = False):
        self.id = run_id or uuid.uuid4().hex[:10]
        self.resume = resume
        self.body = body
        self.sources = sources
        self.state = "running"
        self.current = ""
        self.index = 0
        self.sites: list[dict] = []
        self.log: deque[dict] = deque(maxlen=300)
        self.progress: dict = {}
        self.stats: dict | None = None
        self.error: str | None = None
        self.started = time.time()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.xlsx: Path | None = None
        self.live: dict = {}

    def on_event(self, e: dict) -> None:
        if e["event"] == "site_start":
            self.current, self.index = e["hospital"], e["index"]
            self.log.append({"url": f"=== {e['hospital']}", "type": "info", "status": "", "doctors": 0})
        elif e["event"] == "site_done":
            site = {k: v for k, v in e.items() if k != "event"}
            self.sites.append(site)
            _safe(store().save_site, self.id, site)
            merger = self.live.get("merger")
            if merger is not None:  # this hospital's doctors go into the history now
                _safe(lambda: store().save_doctors(self.id, [d for d in list(merger.records)
                                                             if d.source_hospital == site["hospital"]]))
        elif e["event"] == "page":
            self.progress = {k: e[k] for k in ("fetched", "doctors_total", "oncologists", "queued")}
            self.log.append({"url": e["url"], "type": e["type"], "status": e["status"],
                             "doctors": e["doctors_on_page"], "error": e.get("error")})

    def run(self) -> None:
        from .batch import run_batch
        b = self.body
        try:
            base = ScrapeConfig(max_depth=b.max_depth, delay_seconds=b.delay_seconds, render=b.render,
                                oncology_only=not b.all_specialties, use_sitemaps=b.use_sitemaps)
            OUT_DIR.mkdir(exist_ok=True)
            out = OUT_DIR / f"batch-{self.id}.xlsx"  # fixed name: a resumed batch continues in the same files
            self.xlsx = run_batch(self.sources, out, base, b.pages_per_site, resume=self.resume,
                                  progress=self._capture, stop_event=self.stop, live=self.live,
                                  pause_event=self.pause, workers=b.workers)
            self.state = "stopped" if self.stop.is_set() else "done"
            if self.live.get("merger") is not None:
                _safe(store().save_doctors, self.id, list(self.live["merger"].records))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"
        _safe(store().update_run, self.id, state=self.state, finished=time.time(), error=self.error,
              stats=self.stats or {}, xlsx=str(self.xlsx) if self.xlsx else None)

    def _capture(self, e: dict) -> None:
        if e["event"] == "batch_done":
            self.stats = {k: v for k, v in e.items() if k != "event"}
        else:
            self.on_event(e)


BATCHES: dict[str, BatchJob] = {}


@app.get("/api/template.xlsx")
def template_download():
    import tempfile

    from .sources import write_template
    tmp = Path(tempfile.gettempdir()) / "oncoscraper_hospital_list_template.xlsx"
    write_template(tmp)
    return FileResponse(tmp, filename="hospital_list_template.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/sources/check")
def sources_check(body: UploadIn):
    sources, problems = _load_upload(body)
    return {"usable": len(sources), "sources": [s.as_dict() for s in sources],
            "problems": [p.__dict__ for p in problems]}


@app.post("/api/batch")
def batch_start(body: BatchIn):
    if body.render not in ("never", "auto", "always"):
        raise HTTPException(400, "render must be never, auto or always")
    sources, problems = _load_upload(body)
    if not sources:
        raise HTTPException(400, "No usable hospitals in the file: " + "; ".join(p.problem for p in problems[:5]))
    job = BatchJob(body, sources)
    BATCHES[job.id] = job
    import base64
    settings = body.model_dump(exclude={"content_base64"})
    title = f"{body.filename} ({len(sources)} hospitals)"
    _safe(store().create_run, job.id, "batch", title, settings, body.filename, base64.b64decode(body.content_base64))
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id, "hospitals": len(sources), "problems": [p.__dict__ for p in problems]}


@app.get("/api/active")
def active():
    """The latest single crawl and batch, so a reloaded page can pick them up again."""
    def last(d):
        if not d:
            return None
        job = list(d.values())[-1]
        return {"id": job.id, "state": _state(job)}
    return {"job": last(JOBS), "batch": last(BATCHES)}


@app.get("/api/batch/{job_id}/doctors")
def batch_doctors(job_id: str):
    """Doctors found so far in a batch (finished hospitals plus the one being crawled)."""
    job = BATCHES.get(job_id) or _missing("batch")
    live = job.live
    docs = list(live["merger"].records) if live.get("merger") else []
    for crawler, src in list((live.get("active") or {}).values()):
        for d in list(crawler.merger.records):
            d.source_hospital = d.source_hospital or src.hospital
            docs.append(d)
    if not job.body.all_specialties:
        docs = [d for d in docs if d.is_oncology]
    return {"doctors": _rows(docs)}


@app.get("/api/batch/{job_id}")
def batch_status(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    running = [{"row": src.row, "hospital": src.hospital, "pages": len(c.result_pages),
                "oncologists": sum(1 for d in list(c.merger.records) if d.is_oncology)}
               for c, src in sorted(list((job.live.get("active") or {}).values()), key=lambda x: x[1].row)]
    progress = {"fetched": sum(r["pages"] for r in running), "oncologists": sum(r["oncologists"] for r in running)}
    return {"id": job.id, "state": _state(job), "error": job.error,
            "current": ", ".join(r["hospital"] for r in running) or job.current, "running": running,
            "index": len(job.sites) + len(running),
            "total": len(job.sources), "elapsed": round(time.time() - job.started), "progress": progress,
            "sites": job.sites, "stats": job.stats, "log": list(job.log)[-60:]}


@app.post("/api/batch/{job_id}/stop")
def batch_stop(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    job.stop.set()
    return {"ok": True}


@app.post("/api/batch/{job_id}/pause")
def pause_batch(job_id: str):
    job = BATCHES.get(job_id) or _missing("batch")
    return _pause(job, True)


@app.post("/api/batch/{job_id}/resume")
def resume_batch(job_id: str):
    job = BATCHES.get(job_id) or _missing("batch")
    return _pause(job, False)


@app.get("/api/batch/{job_id}/download.xlsx")
def batch_download(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    if not job.xlsx:  # still running: write what has been found so far
        if not job.live.get("merger"):
            raise HTTPException(404, "nothing found yet")
        from .batch import snapshot_xlsx
        OUT_DIR.mkdir(exist_ok=True)
        part = OUT_DIR / f"batch-{job.id}-partial-{time.strftime('%H%M%S')}.xlsx"
        try:
            snapshot_xlsx(job.live, part)
        except Exception as exc:
            raise HTTPException(500, f"could not write partial Excel: {exc}") from None
        return FileResponse(part, filename=part.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return FileResponse(job.xlsx, filename=job.xlsx.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Portal: run history, doctor directory, dashboard (all from the local database)
def _live_state(run_id: str) -> str | None:
    job = JOBS.get(run_id) or BATCHES.get(run_id)
    return _state(job) if job else None


@app.get("/api/runs")
def runs_list():
    runs = store().list_runs()
    for r in runs:
        r["state"] = _live_state(r["id"]) or r["state"]
        r["live"] = r["id"] in JOBS or r["id"] in BATCHES
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    r = store().get_run(run_id) or _missing("run")
    r["state"] = _live_state(run_id) or r["state"]
    r["sites"] = store().sites(run_id)
    return r


@app.delete("/api/runs/{run_id}")
def run_delete(run_id: str):
    if (store().get_run(run_id) or {}).get("kind") == "prompt":
        manager = prompt_manager()
        saved = manager.db.get(run_id)
        from .prompt_jobs import ACTIVE
        if saved and saved["state"] in ACTIVE:
            raise HTTPException(409, "Stop the prompt run before deleting it")
        manager.db.delete(run_id)
        # Exact job-owned files only; never follow a filename supplied by a client.
        import shutil
        shutil.rmtree(OUT_DIR / f"prompt-{run_id}-raw_sites", ignore_errors=True)
        for suffix in (".xlsx", "-raw.xlsx", "-sources.xlsx"):
            (OUT_DIR / f"prompt-{run_id}{suffix}").unlink(missing_ok=True)
    if _live_state(run_id) in ("running", "paused"):
        raise HTTPException(409, "Stop the run before deleting it")
    store().delete_run(run_id)
    return {"ok": True}


@app.get("/api/runs/{run_id}/download.{fmt}")
def run_download(run_id: str, fmt: str):
    if (store().get_run(run_id) or {}).get("kind") == "prompt" and fmt == "xlsx":
        job = prompt_manager().db.get(run_id)
        if job and job.get("xlsx") and Path(job["xlsx"]).is_file():
            return prompt_download(run_id)
    if run_id in JOBS:
        return download(run_id, fmt)
    if run_id in BATCHES and fmt == "xlsx":
        return batch_download(run_id)
    r = store().get_run(run_id) or _missing("run")
    if fmt == "xlsx" and r.get("xlsx") and Path(r["xlsx"]).is_file():
        p = Path(r["xlsx"])
        return FileResponse(p, filename=p.name, media_type=XLSX)
    docs = store().run_doctors(run_id)
    if not docs:
        raise HTTPException(404, "nothing saved for this run")
    return _export(docs, fmt, f"run-{run_id}", sites=store().sites(run_id) or None)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _export(docs, fmt: str, name: str, sites=None, only_onc: bool = True):
    if fmt == "csv":
        return Response(to_csv([d for d in docs if d.is_oncology or not only_onc]).encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    res = CrawlResult([], docs, [])
    onco = res.oncologists
    res.stats = {"oncologists": len(onco), "doctors_found": len(docs),
                 "oncologists_with_doctor_email": sum(1 for d in onco if d.get("email")),
                 "oncologists_with_doctor_phone": sum(1 for d in onco if d.get("phone"))}
    if fmt == "json":
        return Response(to_json(res, only_onc), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if fmt != "xlsx":
        raise HTTPException(400, "format must be xlsx, csv or json")
    OUT_DIR.mkdir(exist_ok=True)
    path = to_xlsx(res, OUT_DIR / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.xlsx", sites=sites)
    return FileResponse(path, filename=path.name, media_type=XLSX)


@app.post("/api/runs/{run_id}/resume")
def run_resume(run_id: str):
    """Carry on a batch that was cut off when the app closed: finished hospitals are not crawled again."""
    import base64
    from .sources import read_sources
    if _live_state(run_id) in ("running", "paused"):
        raise HTTPException(409, "This run is still going")
    r = store().get_run(run_id) or _missing("run")
    if r["kind"] == "prompt":
        return prompt_action(run_id, "resume")
    if r["kind"] != "batch":
        raise HTTPException(400, "Only hospital-list batches can be resumed; use Run again for a single website")
    name, data = store().upload_of(run_id)
    if not data:
        raise HTTPException(400, "The hospital list of this run was not saved; start a new batch")
    settings = {k: v for k, v in r["settings"].items() if k not in ("filename", "content_base64")}
    body = BatchIn(filename=name or "list.xlsx", content_base64=base64.b64encode(data).decode(), **settings)
    sources, _ = _load_upload(body)
    job = BatchJob(body, sources, run_id=run_id, resume=True)
    BATCHES[run_id] = job
    store().update_run(run_id, state="running", finished=None, error=None)
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": run_id, "hospitals": len(sources)}


@app.post("/api/runs/{run_id}/rerun")
def run_again(run_id: str):
    r = store().get_run(run_id) or _missing("run")
    if r["kind"] != "crawl":
        raise HTTPException(400, "Use Resume for a batch")
    return start(JobIn(**r["settings"]))


def _dir_filters(q: str = "", city: str = "", hospital: str = "", specialty: str = "", has_email: bool = False,
                 has_phone: bool = False, has_contact: bool = False, all_doctors: bool = False) -> dict:
    return dict(q=q.strip(), city=city, hospital=hospital, specialty=specialty.strip(), has_email=has_email,
                has_phone=has_phone, has_contact=has_contact, oncologists_only=not all_doctors)


@app.get("/api/directory")
def directory(q: str = "", city: str = "", hospital: str = "", specialty: str = "", has_email: bool = False,
              has_phone: bool = False, has_contact: bool = False, all_doctors: bool = False, page: int = 1):
    page = max(1, page)
    f = _dir_filters(q, city, hospital, specialty, has_email, has_phone, has_contact, all_doctors)
    total, rows = store().directory(**f, limit=100, offset=(page - 1) * 100)
    docs = [DoctorRecord.from_full_dict(r) for r in rows]
    return {"total": total, "page": page, "pages": max(1, -(-total // 100)), "doctors": _rows(docs, 100),
            "facets": store().facets()}


@app.get("/api/directory/download.{fmt}")
def directory_download(fmt: str, q: str = "", city: str = "", hospital: str = "", specialty: str = "",
                       has_email: bool = False, has_phone: bool = False, has_contact: bool = False,
                       all_doctors: bool = False):
    docs = store().directory_records(**_dir_filters(q, city, hospital, specialty, has_email, has_phone, has_contact,
                                                    all_doctors))
    if not docs:
        raise HTTPException(404, "No doctors match these filters")
    return _export(docs, fmt, "doctor-directory", only_onc=not all_doctors)


@app.get("/api/dashboard")
def dashboard():
    return store().dashboard()


# Prompt discovery is isolated from upload/single-site jobs, and shares their
# extraction engine and final history only after filtering is complete.
_PROMPT_MANAGER = None
_PROMPT_LOCK = threading.RLock()


def prompt_manager():
    global _PROMPT_MANAGER
    from .discovery_services import DiscoveryDB
    from .prompt_jobs import PromptManager
    with _PROMPT_LOCK:
        history = store()
        path = history.path.with_name("prompt-discovery.db")
        if _PROMPT_MANAGER is None or _PROMPT_MANAGER.db.path != path:
            if _PROMPT_MANAGER is not None:
                _PROMPT_MANAGER.close()
            _PROMPT_MANAGER = PromptManager(DiscoveryDB(path), history, OUT_DIR)
            _PROMPT_MANAGER.start_worker()
        return _PROMPT_MANAGER


from .prompt_jobs import PromptIn  # noqa: E402 - keep the workflow separate from legacy endpoints


@app.get("/api/discovery/config")
def prompt_config():
    from .discovery_services import settings
    config = settings()
    config["ready"] = config["ollama_configured"] or config["groq_configured"] or config["gemini_configured"]
    # This is configuration presence, not a claim that the remote APIs are healthy.
    return config


@app.post("/api/discovery")
def prompt_start(body: PromptIn):
    from .discovery_services import ConfigurationError
    try:
        job = prompt_manager().create(body)
    except ConfigurationError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"id": job["id"], "state": job["state"]}


@app.get("/api/discovery")
def prompt_list():
    return {"jobs": [{k: j.get(k) for k in ("id", "state", "phase", "started", "finished", "body")}
                     for j in prompt_manager().db.jobs()]}


@app.get("/api/discovery/{job_id}")
def prompt_status(job_id: str):
    manager = prompt_manager()
    job = manager.db.get(job_id) or _missing("prompt run")
    job["download_ready"] = bool(job.get("xlsx") and Path(job["xlsx"]).is_file())
    job.pop("xlsx", None)
    job["usage"] = manager.db.stats(job_id) or job.get("usage", [])
    return job


@app.get("/api/discovery/{job_id}/download.xlsx")
def prompt_download(job_id: str):
    job = prompt_manager().db.get(job_id) or _missing("prompt run")
    if not job.get("xlsx") or not Path(job["xlsx"]).is_file():
        raise HTTPException(409, "The filtered Excel is not ready. Check the run stage; progress is saved")
    path = Path(job["xlsx"])
    return FileResponse(path, filename=path.name, media_type=XLSX)


@app.get("/api/discovery/{job_id}/sources.xlsx")
def prompt_sources(job_id: str):
    from .prompt_jobs import export_sources
    job = prompt_manager().db.get(job_id) or _missing("prompt run")
    if not job.get("report", {}).get("candidates"):
        raise HTTPException(409, "No hospital pages found yet")
    path = export_sources(job, OUT_DIR / f"prompt-{job_id}-sources.xlsx")
    return FileResponse(path, filename=path.name, media_type=XLSX)


@app.get("/api/discovery/{job_id}/evidence.json")
def prompt_evidence(job_id: str):
    import json
    manager = prompt_manager()
    job = manager.db.get(job_id) or _missing("prompt run")
    return Response(json.dumps({"job": {key: job.get(key) for key in ("id", "state", "phase", "error", "retry_at", "progress")},
                                "report": job["report"], "filter_decisions": manager.db.cached(job_id, "filter_audit") or [],
                                "usage": manager.db.stats(job_id) or job.get("usage", [])}, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="prompt-{job_id}-evidence.json"'})


@app.post("/api/discovery/{job_id}/{action}")
def prompt_action(job_id: str, action: str):
    try:
        job = prompt_manager().action(job_id, action)
    except KeyError:
        raise HTTPException(404, "Prompt run not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"id": job_id, "state": job["state"], "kind": "prompt"}
