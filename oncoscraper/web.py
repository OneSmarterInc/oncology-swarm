"""Local web UI: paste a URL, watch the crawl, download the Excel file."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .config import ScrapeConfig
from .crawl import Crawler, CrawlResult
from .export import to_csv, to_json, to_xlsx
from .extract import extract_page
from .fetch import Fetcher

STATIC = Path(__file__).parent / "static"
OUT_DIR = Path.cwd() / "results"
app = FastAPI(title="OncoScraper", docs_url=None, redoc_url=None)


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

    def on_event(self, e: dict) -> None:
        if e["event"] == "page":
            self.progress = {k: e[k] for k in ("fetched", "doctors_total", "oncologists", "queued")}
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
        except Exception as exc:  # surface any crash to the UI
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"

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
    return {"ok": True, "state": _state(job)}


@app.get("/static/app.js")
def app_js():
    return Response((STATIC / "app.js").read_text(encoding="utf-8"), media_type="application/javascript",
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
    def __init__(self, body: BatchIn, sources):
        self.id = uuid.uuid4().hex[:10]
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
            self.sites.append({k: v for k, v in e.items() if k != "event"})
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
            out = OUT_DIR / f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{self.id}.xlsx"
            self.xlsx = run_batch(self.sources, out, base, b.pages_per_site, resume=False,
                                  progress=self._capture, stop_event=self.stop, live=self.live,
                                  pause_event=self.pause)
            self.state = "stopped" if self.stop.is_set() else "done"
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"

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
    crawler, src = live.get("crawler"), live.get("src")
    if crawler is not None and src is not None:
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
    return {"id": job.id, "state": _state(job), "error": job.error, "current": job.current, "index": job.index,
            "total": len(job.sources), "elapsed": round(time.time() - job.started), "progress": job.progress,
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
