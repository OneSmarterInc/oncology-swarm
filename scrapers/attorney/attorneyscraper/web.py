from __future__ import annotations

import base64
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .classify import discovery_queries
from .engine import Manager
from .export import csv_bytes, parse_sources, workbook_bytes
from .extract import extract
from .fetch import FetchError
from .models import CATEGORIES, Review, RunConfig, Source, site_key
from .security import BodyLimit
from .store import Store


def create_app(db_path=None, fetcher_factory=None):
    store = Store(db_path or os.environ.get("ATTORNEY_DB", "results/attorneys.db"))
    manager = Manager(
        store, **({"fetcher_factory": fetcher_factory} if fetcher_factory else {})
    )
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        with store.owner:
            store.recover()
            try:
                yield
            finally:
                manager.close()

    app = FastAPI(
        title="Healthcare Attorney Scraper", version="0.1.0", lifespan=lifespan
    )
    app.state.store, app.state.manager = store, manager
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def local_protection(request: Request, call_next):
        origin = request.headers.get("origin")
        try:
            parsed_origin = urlsplit(origin) if origin else None
            invalid_origin = parsed_origin and (
                parsed_origin.scheme not in {"http", "https"}
                or parsed_origin.netloc != request.headers.get("host")
            )
        except ValueError:
            invalid_origin = True
        if invalid_origin:
            return JSONResponse(
                {"detail": "Cross-origin requests are not allowed"}, status_code=403
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(
                request.headers.get("x-session-token", ""), token
            ):
                return JSONResponse(
                    {"detail": "Missing session token. Reload the page."},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Run or person not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/session")
    def session():
        return {"token": token, "categories": CATEGORIES}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/runs")
    def runs(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        return store.runs(offset, limit)

    @app.get("/api/history")
    def history(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        return store.history(offset, limit)

    @app.post("/api/page-test")
    def page_test(source: Source):
        if not manager.lock.acquire(blocking=False):
            raise HTTPException(
                409, "Another operation is active. Try again when it finishes."
            )
        try:
            if manager.thread and manager.thread.is_alive():
                raise HTTPException(
                    409, "Pause the active crawl before testing a page."
                )
            fetcher = manager.fetcher_factory(delay=1.5)
            try:
                page = fetcher.get(source.url, site_key(source.url))
                result = extract(
                    page.text, page.url, source.organization, source.city, source.state
                )
                return {
                    "url": page.url,
                    "records": result["records"],
                    "links": len(result["links"]),
                    "text_length": result["text_length"],
                }
            except FetchError as exc:
                raise HTTPException(400, str(exc)) from exc
            finally:
                fetcher.close()
        finally:
            manager.lock.release()

    @app.delete("/api/runs/{rid}")
    def delete_run(rid: str):
        with manager.lock:
            if (
                manager.active_id == rid
                and manager.thread
                and manager.thread.is_alive()
            ):
                raise HTTPException(409, "Stop this run before deleting it.")
            store.delete(rid)
        return {"deleted": True}

    @app.post("/api/runs/{rid}/clone")
    def clone_run(rid: str):
        config = RunConfig(**store.get(rid)["config"])
        config.name = config.name[:110] + " (rerun)"
        return start(config)

    @app.post("/api/runs", status_code=201)
    def start(config: RunConfig):
        with manager.lock:
            if manager.thread and manager.thread.is_alive():
                raise HTTPException(409, "A run is already active")
            rid = store.create(config)
            manager.start(rid)
        return store.get(rid)

    @app.get("/api/runs/{rid}")
    def run(rid: str):
        return store.get(rid)

    @app.post("/api/runs/{rid}/{action}")
    def control(rid: str, action: str):
        store.get(rid)
        if action == "retry":
            manager.start(rid, retry=True)
        elif action in {"pause", "resume", "stop"}:
            manager.control(rid, action)
        else:
            raise HTTPException(404, "Unknown action")
        return store.get(rid)

    @app.get("/api/runs/{rid}/pages")
    def pages(
        rid: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)
    ):
        store.get(rid)
        return store.page_log(rid, offset, limit)

    @app.get("/api/runs/{rid}/audit")
    def audit(rid: str):
        store.get(rid)
        return store.audit(rid)

    def filtered(
        rid=None,
        q="",
        category="",
        status="",
        has_email=False,
        organization="",
        city="",
        has_phone=False,
        has_contact=False,
    ):
        if rid:
            store.get(rid)
        data = store.records(rid)
        return [
            p
            for p in data
            if (
                not q
                or q.casefold()
                in (
                    p["name"]
                    + " "
                    + p["organization"]
                    + " "
                    + p["city"]
                    + " "
                    + p["state"]
                    + " "
                    + p["title"]
                    + " "
                    + " ".join(c["value"] for c in p["contacts"])
                ).casefold()
            )
            and (not organization or organization == p["organization"])
            and (not city or city == p["city"])
            and (
                not has_phone
                or any(
                    c["kind"] == "phone" and c["owner"] == "individual"
                    for c in p["contacts"]
                )
            )
            and (not has_contact or bool(p["contacts"]))
            and (not category or category in p["categories"])
            and (not status or p["status"] == status)
            and (
                not has_email
                or any(
                    c["kind"] == "email" and c["owner"] == "individual"
                    for c in p["contacts"]
                )
            )
        ]

    @app.get("/api/directory")
    def directory(
        rid: str | None = None,
        q: str = "",
        category: str = "",
        status: str = "",
        has_email: bool = False,
        organization: str = "",
        city: str = "",
        has_phone: bool = False,
        has_contact: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        data = filtered(
            rid,
            q,
            category,
            status,
            has_email,
            organization,
            city,
            has_phone,
            has_contact,
        )
        return {"total": len(data), "items": data[offset : offset + limit]}

    @app.get("/api/summary")
    def summary():
        people = store.records()
        firms = {}
        for person in people:
            org = person["organization"]
            row = firms.setdefault(
                org,
                {
                    "organization": org,
                    "people": 0,
                    "email": 0,
                    "phone": 0,
                    "contact": 0,
                },
            )
            row["people"] += 1
            row["email"] += any(
                c["kind"] == "email" and c["owner"] == "individual"
                for c in person["contacts"]
            )
            row["phone"] += any(
                c["kind"] == "phone" and c["owner"] == "individual"
                for c in person["contacts"]
            )
            row["contact"] += bool(person["contacts"])
        return {
            "firms": sorted(firms.values(), key=lambda r: r["organization"].casefold()),
            "cities": sorted({p["city"] for p in people if p["city"]}),
            "database": str(Path(store.path).resolve()),
            "people": len(people),
            "confirmed": sum(p["status"] == "confirmed" for p in people),
            "needs_review": sum(p["status"] == "needs_review" for p in people),
            "individual_emails": sum(
                any(
                    c["owner"] == "individual" and c["kind"] == "email"
                    for c in p["contacts"]
                )
                for p in people
            ),
            "categories": {
                k: sum(k in p["categories"] for p in people) for k in CATEGORIES
            },
        }

    @app.post("/api/review")
    def review(request: Review, rid: str, key: str):
        store.review(rid, key, request.status, request.note)
        return {"saved": True}

    @app.get("/api/export/{kind}")
    def export(
        kind: str,
        rid: str | None = None,
        q: str = "",
        category: str = "",
        status: str = "",
        has_email: bool = False,
        organization: str = "",
        city: str = "",
        has_phone: bool = False,
        has_contact: bool = False,
    ):
        data = filtered(
            rid,
            q,
            category,
            status,
            has_email,
            organization,
            city,
            has_phone,
            has_contact,
        )
        if kind == "csv":
            content, media = csv_bytes(data), "text/csv; charset=utf-8"
        elif kind == "xlsx":
            content, media = (
                workbook_bytes(
                    data,
                    store.get(rid) if rid else None,
                    store.pages(rid) if rid else [],
                    store.audit(rid) if rid else [],
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        elif kind == "json":
            import json

            content, media = json.dumps(data, indent=2).encode(), "application/json"
        else:
            raise HTTPException(404, "Choose csv, xlsx or json")
        return Response(
            content,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="attorneys-{rid or "directory"}.{kind}"'
            },
        )

    @app.post("/api/sources/validate")
    async def validate_sources(request: Request):
        payload = await request.json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("filename"), str)
            or not isinstance(payload.get("data"), str)
        ):
            raise ValueError("Provide a filename and base64 file data")
        try:
            data = base64.b64decode(payload.get("data", ""), validate=True)
        except Exception as e:
            raise ValueError("Invalid file encoding") from e
        sources = parse_sources(payload.get("filename", ""), data)
        return {"sources": [s.model_dump() for s in sources]}

    @app.get("/api/template")
    def template():
        return Response(
            "URL,Organization,City,State,Enabled\nhttps://example.com/attorneys/,,,,no\n",
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="attorney-sources-template.csv"'
            },
        )

    @app.get("/api/discovery")
    def discovery(
        category: str, location: str = Query("United States", max_length=100)
    ):
        if category not in CATEGORIES:
            raise ValueError("Unknown category")
        return {"queries": discovery_queries(category, location)}

    app.add_middleware(BodyLimit)
    return app
