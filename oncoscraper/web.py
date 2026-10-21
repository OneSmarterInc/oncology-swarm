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
            self.crawler = Crawler(cfg, self.on_event, self.stop)
            self.result = self.crawler.run([u.strip() for u in b.urls if u.strip()])
            OUT_DIR.mkdir(exist_ok=True)
            self.xlsx = to_xlsx(self.result, OUT_DIR / f"oncologists-{time.strftime('%Y%m%d-%H%M%S')}-{self.id}.xlsx")
            self.state = "stopped" if self.stop.is_set() else "done"
        except Exception as exc:  # surface any crash to the UI
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"

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
    docs = job.doctors()
    rows = []
    for d in docs[:2000]:
        x = d.to_dict()
        rows.append({k: x.get(k) for k in ("name", "designation", "specialty", "sub_specialties", "qualifications",
                                            "experience_years", "hospital", "city", "phone", "email", "contact_phone",
                                            "contact_email", "profile_url", "source_url", "is_oncology", "page_type")}
                    | {"fields": len(d.filled_fields())})
    return {"id": job.id, "state": job.state, "error": job.error, "progress": job.progress,
            "max_pages": job.body.max_pages, "elapsed": round(time.time() - job.started),
            "stats": job.result.stats if job.result else None, "log": list(job.log)[-60:], "doctors": rows}


@app.post("/api/jobs/{job_id}/stop")
def stop(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job.stop.set()
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download.{fmt}")
def download(job_id: str, fmt: str):
    job = JOBS.get(job_id)
    if not job or not job.result:
        raise HTTPException(404, "results not ready")
    if fmt == "xlsx" and job.xlsx:
        return FileResponse(job.xlsx, filename=job.xlsx.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "csv":
        return Response(to_csv(job.doctors()).encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="oncologists-{job.id}.csv"'})
    if fmt == "json":
        return Response(to_json(job.result, not job.body.all_specialties), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="oncologists-{job.id}.json"'})
    raise HTTPException(400, "format must be xlsx, csv or json")


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

    from .sources import read_sources
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
    except Exception as exc:
        raise HTTPException(400, f"Could not read the file: {exc}") from None
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
        self.xlsx: Path | None = None

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
                                  progress=self._capture, stop_event=self.stop)
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


@app.get("/api/batch/{job_id}")
def batch_status(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    return {"id": job.id, "state": job.state, "error": job.error, "current": job.current, "index": job.index,
            "total": len(job.sources), "elapsed": round(time.time() - job.started), "progress": job.progress,
            "sites": job.sites, "stats": job.stats, "log": list(job.log)[-60:]}


@app.post("/api/batch/{job_id}/stop")
def batch_stop(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    job.stop.set()
    return {"ok": True}


@app.get("/api/batch/{job_id}/download.xlsx")
def batch_download(job_id: str):
    job = BATCHES.get(job_id)
    if not job or not job.xlsx:
        raise HTTPException(404, "results not ready")
    return FileResponse(job.xlsx, filename=job.xlsx.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
