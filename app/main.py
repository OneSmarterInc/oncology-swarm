from contextlib import asynccontextmanager
import hmac
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Response, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, update, or_
from app.config import get_settings
from app.db import Database
from app.models import Control, Job, Snapshot, Proposal, Doctor, Audit, utcnow
from app.service import Service, Conflict, BudgetExceeded, WorkPaused
from app.export import export_xlsx

STATIC = Path(__file__).parent / 'static'

def display_name(data):
    doctor = data.get('doctor') if isinstance(data, dict) else None
    name = doctor.get('name') if isinstance(doctor, dict) else None
    return name if isinstance(name, str) else None


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class SeedInput(Input):
    urls: list[str] = Field(min_length=1, max_length=100)

class EditInput(Input):
    data: dict
    expected_version: int = Field(ge=1, strict=True)

class ActorInput(Input):
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)

class ReviewInput(ActorInput):
    expected_version: int = Field(ge=1, strict=True)
    duplicate_acknowledged: bool = Field(default=False, strict=True)

class VersionedItem(Input):
    id: int = Field(ge=1, strict=True)
    expected_version: int = Field(ge=1, strict=True)

class BulkInput(ActorInput):
    items: list[VersionedItem] = Field(min_length=1, max_length=25)
    human_acknowledged: bool = Field(strict=True)

class MergeInput(ReviewInput):
    target_job_id: int = Field(ge=1, strict=True)
    target_expected_version: int = Field(ge=1, strict=True)

class RefreshInput(ReviewInput):
    acknowledge_possible_additional_model_charge: bool = Field(default=False, strict=True)

class RetryInput(Input):
    acknowledge_possible_additional_model_charge: bool = Field(default=False, strict=True)

class SourceInput(ActorInput):
    name: str = Field(min_length=1, max_length=500)
    city: str = Field(default='', max_length=300)
    hostname: str = Field(min_length=1, max_length=253)
    list_url: str = Field(min_length=1, max_length=2048)
    sitemap_url: str | None = Field(default=None, max_length=2048)
    profile_url_pattern: str | None = Field(default=None, max_length=300)

class DiscoverInput(Input):
    max_pages: int = Field(default=10, ge=1, le=50, strict=True)
    max_profiles: int = Field(default=200, ge=1, le=1000, strict=True)
    use_sitemap: bool = Field(default=False, strict=True)

class ControlInput(Input):
    paused: bool = Field(strict=True)


def create_app(settings=None, db=None):
    settings = settings or get_settings()
    database = db or Database(settings.database_url)
    service = Service(database, settings)
    @asynccontextmanager
    async def lifespan(app):
        settings.require_token()
        if settings.demo_auto_create and database.engine.dialect.name == 'sqlite':
            database.init_demo()
        else:
            database.ensure_control()
        yield
    app = FastAPI(title='Oncology Research Review', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service, app.state.db = service, database

    def auth(authorization: str | None = Header(default=None)):
        expected = 'Bearer ' + settings.admin_token
        if not authorization or not hmac.compare_digest(authorization.encode('utf-8'), expected.encode('utf-8')):
            raise HTTPException(401, 'Admin bearer token required', headers={'WWW-Authenticate': 'Bearer'})

    @app.middleware('http')
    async def headers(request: Request, call_next):
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > 1_000_000):
            return Response('Request too large', status_code=413)
        if request.method in {'POST', 'PUT', 'PATCH'} and not length:
            return Response('Content-Length required', status_code=411)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; connect-src 'self'"
        return response

    @app.exception_handler(Conflict)
    @app.exception_handler(BudgetExceeded)
    @app.exception_handler(WorkPaused)
    async def conflict_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={'detail': str(exc)})

    @app.get('/healthz')
    def health(): return {'status': 'ok'}

    @app.get('/')
    def index(): return FileResponse(STATIC / 'index.html')
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/api/status', dependencies=[Depends(auth)])
    def status():
        with database.session() as s:
            c = s.get(Control, 1)
            counts = dict(s.execute(select(Job.state, func.count()).group_by(Job.state)).all())
            return {'mode': settings.mode, 'paused': c.paused, 'requests_used': c.requests_used, 'max_requests': settings.max_requests, 'jobs_created': c.jobs_created, 'max_jobs': settings.max_jobs, 'worker_count': settings.worker_count, 'extraction_backend': getattr(settings, 'llm_provider', 'openai') if settings.llm_enabled else 'deterministic', 'llm_calls_used': c.llm_calls_used, 'max_llm_calls': settings.max_llm_calls, 'counts': counts, 'run_number': c.run_number, 'approved_doctors': s.scalar(select(func.count()).select_from(Doctor).where(Doctor.active.is_(True), Doctor.needs_review.is_(False))), 'allowed_domains': settings.allowed_domains, 'authentication': 'shared_admin_token', 'reviewer_identity': 'operator_supplied_label'}

    @app.post('/api/jobs', dependencies=[Depends(auth)])
    def enqueue(body: SeedInput):
        results = []
        for url in body.urls:
            try:
                job_id, created = service.enqueue(url)
                results.append({'url': url, 'id': job_id, 'created': created})
            except (ValueError, BudgetExceeded) as exc:
                results.append({'url': url, 'error': str(exc)})
        return {'results': results}

    @app.get('/api/jobs', dependencies=[Depends(auth)])
    def jobs(offset: int = Query(0, ge=0, le=2_147_483_647), limit: int = Query(25, ge=1, le=100),
             q: str = Query('', max_length=300), state: str = Query('', max_length=24),
             host: str = Query('', max_length=253), validation: Literal['', 'valid', 'invalid', 'missing'] = ''):
        with database.session() as s:
            stmt = select(Job, Proposal).outerjoin(Proposal, Proposal.job_id == Job.id)
            if state:
                stmt = stmt.where(Job.state == state)
            if host:
                normalized = host.strip().lower().rstrip('.')
                if not normalized or any(c in normalized for c in '/:@?#%_') or any(c.isspace() for c in normalized):
                    raise HTTPException(422, 'Host filter must be an exact hostname without scheme or path')
                stmt = stmt.where(Job.host == normalized)
            if q.strip():
                value = q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                stmt = stmt.where(or_(Job.url.ilike('%' + value + '%', escape='\\'), Proposal.data['doctor']['name'].as_string().ilike('%' + value + '%', escape='\\')))
            if validation == 'missing':
                stmt = stmt.where(Proposal.id.is_(None))
            elif validation == 'valid':
                stmt = stmt.where(Proposal.id.is_not(None), func.json_array_length(Proposal.validation_errors) == 0)
            elif validation == 'invalid':
                stmt = stmt.where(func.json_array_length(Proposal.validation_errors) > 0)
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            items = []
            for j, p in s.execute(stmt.order_by(Job.id.desc()).offset(offset).limit(limit)):
                duplicates = service.duplicate_ids(s, p.data, j.id) if p else []
                items.append({'id': j.id, 'url': j.url, 'host': urlsplit(j.url).hostname, 'state': j.state, 'source_id': j.source_id, 'generation': j.generation, 'refresh_of_job_id': j.refresh_of_job_id, 'llm_call_reserved': j.llm_call_reserved, 'attempts': j.attempts, 'last_error': j.last_error, 'heartbeat_at': j.heartbeat_at, 'lease_expires_at': j.lease_expires_at, 'name': display_name(p.data) if p else None, 'version': p.version if p else None, 'validation_error_count': len(p.validation_errors) if p else None, 'duplicate_job_ids': duplicates, 'bulk_eligible': bool(p and j.state == 'review' and not p.validation_errors and not duplicates)})
            return {'items': items, 'total': total, 'offset': offset, 'limit': limit}

    @app.post('/api/jobs/bulk-approve', dependencies=[Depends(auth)])
    def bulk_approve(body: BulkInput):
        if not body.human_acknowledged:
            raise HTTPException(422, 'Explicit human review acknowledgement is required')
        if len({i.id for i in body.items}) != len(body.items):
            raise HTTPException(422, 'Duplicate IDs in the selected batch are not allowed')
        return {'results': service.bulk_approve([i.model_dump() for i in body.items], body.reviewer, body.note, body.human_acknowledged)}

    @app.get('/api/jobs/{job_id}', dependencies=[Depends(auth)])
    def detail(job_id: int):
        with database.session() as s:
            j = s.get(Job, job_id)
            if not j: raise HTTPException(404, 'Job not found')
            p = s.scalar(select(Proposal).where(Proposal.job_id == job_id))
            source = s.scalar(select(Snapshot).where(Snapshot.job_id == job_id))
            doctor = s.scalar(select(Doctor).where(Doctor.proposal_id == p.id)) if p else None
            return {'id': j.id, 'url': j.url, 'state': j.state, 'source_id': j.source_id, 'generation': j.generation, 'refresh_of_job_id': j.refresh_of_job_id, 'llm_call_reserved': j.llm_call_reserved, 'attempts': j.attempts, 'last_error': j.last_error,
                    'doctor_id': doctor.id if doctor else None, 'doctor_active': doctor.active if doctor else None, 'doctor_needs_review': doctor.needs_review if doctor else None,
                    'proposal': {'data': p.data, 'version': p.version, 'validation_errors': p.validation_errors, 'duplicate_job_ids': service.duplicate_ids(s, p.data, job_id), 'reviewer': p.reviewer, 'review_note': p.review_note} if p else None,
                    'source': {'url': source.url, 'text': source.text, 'retrieved_at': source.retrieved_at, 'content_sha256': source.content_hash, 'synthetic': source.synthetic, 'source_type': getattr(source, 'source_type', None), 'source_id': getattr(source, 'source_id', None)} if source else None}

    @app.put('/api/jobs/{job_id}/proposal', dependencies=[Depends(auth)])
    def edit(job_id: int, body: EditInput):
        return {'validation_errors': service.edit(job_id, body.data, body.expected_version)}

    @app.post('/api/jobs/{job_id}/approve', dependencies=[Depends(auth)])
    def approve(job_id: int, body: ReviewInput):
        return {'state': service.review(job_id, body.expected_version, body.reviewer, body.note, True, body.duplicate_acknowledged)}

    @app.post('/api/jobs/{job_id}/reject', dependencies=[Depends(auth)])
    def reject(job_id: int, body: ReviewInput):
        return {'state': service.review(job_id, body.expected_version, body.reviewer, body.note, False)}

    @app.post('/api/jobs/{job_id}/withdraw', dependencies=[Depends(auth)])
    def withdraw(job_id: int, body: ReviewInput):
        return {'state': service.withdraw(job_id, body.expected_version, body.reviewer, body.note)}

    @app.post('/api/jobs/{job_id}/correct', dependencies=[Depends(auth)])
    def correct(job_id: int, body: ReviewInput):
        return {'state': service.correct(job_id, body.expected_version, body.reviewer, body.note)}

    @app.post('/api/jobs/{job_id}/merge', dependencies=[Depends(auth)])
    def merge(job_id: int, body: MergeInput):
        return service.merge(job_id, body.target_job_id, body.expected_version, body.target_expected_version, body.reviewer, body.note)

    @app.post('/api/jobs/{job_id}/unmerge', dependencies=[Depends(auth)])
    def unmerge(job_id: int, body: MergeInput):
        return service.unmerge(job_id, body.target_job_id, body.expected_version, body.target_expected_version, body.reviewer, body.note)

    @app.post('/api/jobs/{job_id}/refresh', dependencies=[Depends(auth)])
    def refresh_job(job_id: int, body: RefreshInput):
        return service.refresh_job(job_id, body.expected_version, body.reviewer, body.note, body.acknowledge_possible_additional_model_charge)

    @app.post('/api/jobs/{job_id}/retry', dependencies=[Depends(auth)])
    def retry(job_id: int, body: RetryInput):
        service.retry_failed(job_id, body.acknowledge_possible_additional_model_charge)
        return {'state': 'queued'}

    @app.post('/api/control', dependencies=[Depends(auth)])
    def control(body: ControlInput):
        with database.session.begin() as s:
            s.execute(update(Control).where(Control.id == 1).values(paused=body.paused, updated_at=utcnow()))
            s.add(Audit(action='pause' if body.paused else 'resume', actor='admin', details={}))
        return {'paused': body.paused}

    @app.post('/api/control/reset-budgets', dependencies=[Depends(auth)])
    def reset_budgets(body: ActorInput):
        return service.reset_budgets(body.reviewer, body.note)

    @app.get('/api/metrics', dependencies=[Depends(auth)])
    def metrics():
        return service.metrics()

    @app.get('/api/sources', dependencies=[Depends(auth)])
    def sources():
        return {'items': service.list_sources()}

    @app.post('/api/sources', dependencies=[Depends(auth)])
    def add_source(body: SourceInput):
        return service.add_source(**body.model_dump())

    @app.post('/api/sources/{source_id}/discover', dependencies=[Depends(auth)])
    def discover(source_id: int, body: DiscoverInput):
        return service.discover_source(source_id, **body.model_dump())

    @app.get('/api/export.xlsx', dependencies=[Depends(auth)])
    def export():
        return Response(export_xlsx(database), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="approved_directory.xlsx"'})
    return app

app = create_app()
