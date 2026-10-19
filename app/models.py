from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def utcnow():
    # Naive UTC is deliberate: portable SQLite/PostgreSQL comparisons, API always adds Z.
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase):
    pass

class Control(Base):
    __tablename__ = 'control'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    jobs_created: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    run_number: Mapped[int] = mapped_column(Integer, default=1)

class Hospital(Base):
    __tablename__ = 'hospitals'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500), index=True)
    city: Mapped[str | None] = mapped_column(String(300), nullable=True)

class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey('hospitals.id'))
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    list_url: Mapped[str] = mapped_column(String(2048), unique=True)
    sitemap_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    profile_url_pattern: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default='official')
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewer: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048))
    __table_args__ = (UniqueConstraint('url', 'generation', name='uq_jobs_url_generation'),)
    host: Mapped[str] = mapped_column(String(253), default='', index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    source_id: Mapped[int | None] = mapped_column(ForeignKey('sources.id'), nullable=True)
    refresh_of_job_id: Mapped[int | None] = mapped_column(ForeignKey('jobs.id'), nullable=True)
    run_number: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    llm_call_reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_call_sequence: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class Snapshot(Base):
    __tablename__ = 'snapshots'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[int | None] = mapped_column(ForeignKey('sources.id'), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default='official')
    retrieved_at: Mapped[str] = mapped_column(String(40))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)

class Proposal(Base):
    __tablename__ = 'proposals'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'), unique=True)
    data: Mapped[dict] = mapped_column(JSON)
    normalized_name: Mapped[str] = mapped_column(String(300), default='', index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    duplicate_ids: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

class Doctor(Base):
    __tablename__ = 'doctors'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey('proposals.id'), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    specialty: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str | None] = mapped_column(String(300), nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey('sources.id'), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default='official')
    qualifications: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    experience_years: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    sub_specialty: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    languages: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    opd_timings: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    consultation_fee: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    profile_photo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

class DoctorSource(Base):
    __tablename__ = 'doctor_sources'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey('doctors.id'), index=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey('proposals.id'))
    __table_args__ = (UniqueConstraint('doctor_id', 'proposal_id', name='uq_doctor_sources'),)

class LLMUsage(Base):
    __tablename__ = 'llm_usage'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    run_number: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint('job_id', 'attempt', name='uq_llm_usage_attempt'),)

class Affiliation(Base):
    __tablename__ = 'affiliations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey('doctors.id'))
    source_proposal_id: Mapped[int | None] = mapped_column(ForeignKey('proposals.id'), nullable=True)
    name: Mapped[str] = mapped_column(String(500))
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey('hospitals.id'), nullable=True)
    city: Mapped[str | None] = mapped_column(String(300), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON)

class Contact(Base):
    __tablename__ = 'contacts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey('doctors.id'))
    source_proposal_id: Mapped[int | None] = mapped_column(ForeignKey('proposals.id'), nullable=True)
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(500))
    scope: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[list] = mapped_column(JSON)

class Registration(Base):
    __tablename__ = 'registrations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey('doctors.id'))
    source_proposal_id: Mapped[int | None] = mapped_column(ForeignKey('proposals.id'), nullable=True)
    number: Mapped[str] = mapped_column(String(300))
    authority: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[list] = mapped_column(JSON)

class Audit(Base):
    __tablename__ = 'audit_log'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey('jobs.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

# Keep the indexed projection correct for ORM writes, including imports/tests.
from sqlalchemy import event
@event.listens_for(Proposal, 'before_insert')
@event.listens_for(Proposal, 'before_update')
def index_proposal_name(mapper, connection, target):
    from app.validation import normalize_name
    doctor = target.data.get('doctor', {}) if isinstance(target.data, dict) else {}
    target.normalized_name = normalize_name(doctor.get('name', '')) if isinstance(doctor, dict) and isinstance(doctor.get('name', ''), str) else ''

class HostPacing(Base):
    __tablename__ = 'host_pacing'
    hostname: Mapped[str] = mapped_column(String(253), primary_key=True)
    next_request_at: Mapped[datetime] = mapped_column(DateTime)
    last_request_at: Mapped[datetime] = mapped_column(DateTime)
    interval_seconds: Mapped[float] = mapped_column(Float)

class DiscoveryRun(Base):
    __tablename__ = 'discovery_runs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('sources.id'))
    state: Mapped[str] = mapped_column(String(24), index=True)
    owner: Mapped[str] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
