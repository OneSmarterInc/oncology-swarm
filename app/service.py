from datetime import timedelta
from copy import deepcopy
import re
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy import select, update, func, delete
from sqlalchemy.exc import IntegrityError
from app.models import Control, Job, Snapshot, Proposal, Doctor, Affiliation, Contact, Registration, Audit, Hospital, Source, DoctorSource, LLMUsage, HostPacing, DiscoveryRun, utcnow
from app.validation import validate_proposal, normalize_name

def proposal_name(data):
    doctor=data.get('doctor') if isinstance(data,dict) else None
    name=doctor.get('name') if isinstance(doctor,dict) else None
    return normalize_name(name) if isinstance(name,str) else ''

class Conflict(ValueError): pass
class BudgetExceeded(RuntimeError): pass
class WorkPaused(RuntimeError): pass

def canonical_url(raw):
    if not isinstance(raw, str) or len(raw) > 2048 or any(ord(x) < 33 for x in raw):
        raise ValueError('URL must be at most 2048 characters with no whitespace/control characters')
    p = urlsplit(raw)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Only HTTPS URLs without credentials or custom ports are accepted')
    host = p.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    if ':' in host:
        raise ValueError('IP addresses are not allowed')
    return urlunsplit(('https', host, p.path or '/', p.query, ''))

class Service:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def enqueue(self, raw, source_id=None, discovery_run_id=None, discovery_owner=None):
        url = canonical_url(raw)
        host = urlsplit(url).hostname
        if self.settings.mode == 'fixture':
            if url not in {'https://demo.example.invalid/doctors/asha-rao', 'https://demo.example.invalid/doctors/vikram-sen'}:
                raise ValueError('Fixture mode accepts only the two documented synthetic seed URLs')
        elif host not in self.settings.allowed_domains:
            raise ValueError('Host is not in ALLOWED_DOMAINS')
        try:
            with self.db.session.begin() as s:
                if discovery_run_id is not None:
                    s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
                    run=s.get(DiscoveryRun,discovery_run_id)
                    if not run or run.state!='running' or run.owner!=discovery_owner or run.lease_expires_at<=utcnow():
                        raise Conflict('Discovery lease lost; no further jobs allowed')
                existing = s.scalar(select(Job).where(Job.url == url).order_by(Job.generation.desc()).limit(1))
                if existing:
                    return existing.id, False
                source = self._resolve_source(s, host, source_id)
                result = s.execute(update(Control).where(Control.id == 1, Control.jobs_created < self.settings.max_jobs).values(jobs_created=Control.jobs_created + 1, updated_at=utcnow()))
                if result.rowcount != 1:
                    raise BudgetExceeded('MAX_JOBS reached; increase the configured cap deliberately')
                job = Job(url=url, host=host, state='queued', source_id=source.id if source else None, generation=0, run_number=s.get(Control, 1).run_number)
                s.add(job)
                s.flush()
                s.add(Audit(job_id=job.id, action='enqueue', actor='admin', details={'url': url}))
                return job.id, True
        except IntegrityError:
            with self.db.session() as s:
                existing = s.scalar(select(Job).where(Job.url == url).order_by(Job.generation.desc()).limit(1))
                if existing:
                    return existing.id, False
                raise

    def reserve_request(self, url, discovery_run_id=None, discovery_owner=None):
        with self.db.session.begin() as s:
            if discovery_run_id is not None:
                s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
                run=s.get(DiscoveryRun,discovery_run_id)
                if not run or run.state!='running' or run.owner!=discovery_owner or run.lease_expires_at<=utcnow():
                    raise Conflict('Discovery lease lost; no further requests allowed')
            result = s.execute(update(Control).where(Control.id == 1, Control.paused.is_(False), Control.requests_used < self.settings.max_requests).values(requests_used=Control.requests_used + 1, updated_at=utcnow()))
            if result.rowcount != 1:
                control = s.get(Control, 1)
                if control and control.paused:
                    raise WorkPaused('Research is paused')
                raise BudgetExceeded('HTTP request budget exhausted; raise MAX_REQUESTS deliberately before retrying failed work')

    def reserve_llm_call(self, job_id, owner):
        with self.db.session.begin() as s:
            fenced = s.execute(update(Job).where(Job.id == job_id, Job.state == 'leased', Job.lease_owner == owner, Job.lease_expires_at > utcnow(), Job.llm_call_reserved.is_(False)).values(llm_call_reserved=True, llm_call_sequence=Job.llm_call_sequence + 1))
            if fenced.rowcount != 1:
                raise Conflict('Model call already reserved or lease lost; no automatic paid retry')
            s.execute(update(Job).where(Job.id==job_id).values(run_number=s.get(Control,1).run_number))
            result = s.execute(update(Control).where(Control.id == 1, Control.paused.is_(False), Control.llm_calls_used < self.settings.max_llm_calls).values(llm_calls_used=Control.llm_calls_used + 1, updated_at=utcnow()))
            if result.rowcount != 1:
                control = s.get(Control, 1)
                if control and control.paused:
                    raise WorkPaused('Research is paused')
                raise BudgetExceeded('Model call budget exhausted; increase MAX_LLM_CALLS deliberately')

    def claim(self, owner):
        now = utcnow()
        with self.db.session.begin() as s:
            if s.get(Control, 1).paused:
                return None
            # Fencing token prevents a worker whose lease expired from committing later.
            expired = s.scalars(select(Job).where(Job.state == 'leased', Job.lease_expires_at < now)).all()
            for job in expired:
                state = 'failed' if job.llm_call_reserved or job.attempts >= self.settings.max_attempts else 'retry'
                s.execute(update(Job).where(Job.id == job.id, Job.state == 'leased', Job.lease_expires_at < now).values(state=state, lease_owner=None, lease_expires_at=None, available_at=now, last_error='Lease expired after a reserved model call; inspect possible billing before manual retry' if job.llm_call_reserved else 'Worker lease expired; work recovered', updated_at=now))
            candidate = s.scalar(select(Job).where(Job.state.in_(['queued', 'retry']), Job.available_at <= now).order_by(Job.created_at, Job.id).limit(1))
            if not candidate:
                return None
            result = s.execute(update(Job).where(Job.id == candidate.id, Job.state.in_(['queued', 'retry']), Job.available_at <= now).values(state='leased', attempts=Job.attempts + 1, lease_owner=owner, lease_expires_at=now + timedelta(seconds=self.settings.lease_seconds), heartbeat_at=now, updated_at=now))
            if result.rowcount != 1:
                return None
            s.add(Audit(job_id=candidate.id, action='lease', actor=owner, details={}))
            return candidate.id, candidate.url

    def heartbeat(self, job_id, owner):
        now = utcnow()
        with self.db.session.begin() as s:
            return s.execute(update(Job).where(Job.id == job_id, Job.state == 'leased', Job.lease_owner == owner, Job.lease_expires_at > now).values(lease_expires_at=now + timedelta(seconds=self.settings.lease_seconds), heartbeat_at=now)).rowcount == 1

    def duplicate_ids(self, s, data, exclude_job=None):
        name = proposal_name(data)
        if not name:
            return []
        # Indexed exact-name candidates; identity decisions remain with reviewers.
        statement = select(Proposal.job_id).join(Job).where(Proposal.normalized_name == name, Proposal.job_id != (exclude_job or -1), Job.state.not_in(['withdrawn', 'merged', 'superseded', 'unchanged']))
        current = s.get(Job, exclude_job) if exclude_job else None
        if current and current.refresh_of_job_id:
            statement = statement.where(Proposal.job_id != current.refresh_of_job_id)
        return sorted(set(s.scalars(statement)))

    def finish(self, job_id, owner, fetched, data):
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id == 1).values(updated_at=utcnow()))
            job = s.get(Job, job_id)
            if not job or job.state != 'leased' or job.lease_owner != owner or job.lease_expires_at <= utcnow():
                return False
            if job.source_id:
                source = s.get(Source, job.source_id)
                if not source or not source.active or urlsplit(fetched.url).hostname != source.hostname:
                    raise Conflict('Fetched page no longer belongs to its approved official source')
            snapshot = s.scalar(select(Snapshot).where(Snapshot.job_id==job_id))
            if snapshot:
                if any(getattr(snapshot,key)!=getattr(fetched,key) for key in ('url','text','content_hash','retrieved_at','synthetic')):
                    raise Conflict('Captured refresh snapshot is immutable; create a new refresh job')
            else:
                snapshot = Snapshot(job_id=job_id, url=fetched.url, text=fetched.text, content_hash=fetched.content_hash, retrieved_at=fetched.retrieved_at, synthetic=fetched.synthetic, source_id=job.source_id, source_type='synthetic' if fetched.synthetic else ('official' if job.source_id else 'unclassified'))
            previous = s.scalar(select(Snapshot).where(Snapshot.job_id == job.refresh_of_job_id)) if job.refresh_of_job_id else None
            unchanged = self._unchanged_eligible(s,job,snapshot.content_hash)
            if data is None and not unchanged:
                raise Conflict('Refresh eligibility changed during fetch; retry to prepare a fresh proposal')
            errors = [] if unchanged else validate_proposal(data, snapshot)

            # Claim fence is conditional; same transaction atomically creates source/proposal.
            changed = s.execute(update(Job).where(Job.id == job_id, Job.state == 'leased', Job.lease_owner == owner, Job.lease_expires_at > utcnow()).values(state='unchanged' if unchanged else 'review', lease_owner=None, lease_expires_at=None, last_error=None, updated_at=utcnow()))
            if changed.rowcount != 1:
                return False
            s.add(snapshot)
            if unchanged:
                s.add(Audit(job_id=job_id, action='refresh_unchanged', actor=owner, details={'previous_job_id': job.refresh_of_job_id, 'hash': snapshot.content_hash}))
                return True
            if previous:
                old_p = s.scalar(select(Proposal).where(Proposal.job_id == job.refresh_of_job_id))
                if old_p:
                    s.execute(update(Doctor).where(Doctor.proposal_id == old_p.id).values(needs_review=True))
                s.add(Audit(job_id=job_id, action='refresh_changed' if previous.content_hash != snapshot.content_hash else 'refresh_recheck', actor=owner, details={'previous_job_id': job.refresh_of_job_id, 'previous_hash': previous.content_hash, 'new_hash': snapshot.content_hash}))
            s.add(Proposal(job_id=job_id, data=data, normalized_name=proposal_name(data), validation_errors=errors, duplicate_ids=self.duplicate_ids(s, data, job_id)))
            s.add(Audit(job_id=job_id, action='proposed', actor=owner, details={'validation_error_count': len(errors)}))
            return True

    def fail(self, job_id, owner, error, retryable=False):
        with self.db.session.begin() as s:
            job = s.get(Job, job_id)
            if not job or job.state != 'leased' or job.lease_owner != owner:
                return
            retry = retryable and job.attempts < self.settings.max_attempts
            # No automatic retries for budget/security/robots failures.
            s.execute(update(Job).where(Job.id == job_id, Job.state == 'leased', Job.lease_owner == owner).values(state='retry' if retry else 'failed', lease_owner=None, lease_expires_at=None, available_at=utcnow() + timedelta(seconds=min(300, 2 ** job.attempts * 5)) if retry else utcnow(), last_error=str(error)[:1500], updated_at=utcnow()))
            s.add(Audit(job_id=job_id, action='retry' if retry else 'failed', actor=owner, details={'reason': str(error)[:1500]}))

    def edit(self, job_id, data, expected_version):
        with self.db.session.begin() as s:
            # Same lock as approval: an edit cannot race a publish and split its evidence.
            s.execute(update(Control).where(Control.id == 1).values(updated_at=utcnow()))
            job = s.get(Job, job_id)
            p = s.scalar(select(Proposal).where(Proposal.job_id == job_id))
            snap = s.scalar(select(Snapshot).where(Snapshot.job_id == job_id))
            if not job or not p or job.state != 'review':
                raise Conflict('Only jobs awaiting review can be edited')
            previous_data = deepcopy(p.data)
            errors = validate_proposal(data, snap)
            dupes = self.duplicate_ids(s, data, job_id)
            result = s.execute(update(Proposal).where(Proposal.id == p.id, Proposal.version == expected_version).values(data=data, normalized_name=proposal_name(data), validation_errors=errors, duplicate_ids=dupes, version=Proposal.version + 1))
            if result.rowcount != 1:
                raise Conflict('Proposal changed; reload before editing')
            s.add(Audit(job_id=job_id, action='edit', actor='admin', details={'version': expected_version + 1, 'previous_data': previous_data, 'new_data': data}))
            return errors

    def review(self, job_id, expected_version, reviewer, note, approve, duplicate_acknowledged=False):
        with self.db.session.begin() as s:
            # Lock the control row to serialize human approvals across workers/databases.
            s.execute(update(Control).where(Control.id == 1).values(updated_at=utcnow()))
            job = s.get(Job, job_id)
            p = s.scalar(select(Proposal).where(Proposal.job_id == job_id))
            snap = s.scalar(select(Snapshot).where(Snapshot.job_id == job_id))
            if not job or not p or job.state != 'review' or p.version != expected_version:
                raise Conflict('Job is not awaiting this version of human review')
            errors = validate_proposal(p.data, snap)
            duplicates = self.duplicate_ids(s, p.data, job_id)
            if approve and job.refresh_of_job_id:
                old_job=s.get(Job,job.refresh_of_job_id)
                old_p=s.scalar(select(Proposal).where(Proposal.job_id==job.refresh_of_job_id))
                old_d=s.scalar(select(Doctor).where(Doctor.proposal_id==old_p.id)) if old_p else None
                if not old_job or old_job.state!='approved' or not old_d or not old_d.active:
                    raise Conflict('Original source was withdrawn, corrected or merged; reconcile it before approving refresh')
                self._assert_unmerged(s,old_d,old_p)
            if approve and errors:
                raise Conflict('Validation blocked approval: ' + '; '.join(errors))
            if approve and duplicates and not duplicate_acknowledged:
                raise Conflict(f'Possible duplicate jobs {duplicates}; acknowledge after comparing the evidence')
            p.validation_errors, p.duplicate_ids = errors, duplicates
            p.reviewed_at, p.reviewer, p.review_note = utcnow(), reviewer, note
            p.version += 1
            if approve:
                data = p.data
                d = s.scalar(select(Doctor).where(Doctor.proposal_id == p.id))
                if d is None:
                    d = Doctor(proposal_id=p.id)
                    s.add(d)
                identity = data['doctor']
                for field in ('name', 'specialty', 'city', 'qualifications', 'experience_years', 'sub_specialty', 'languages', 'opd_timings', 'consultation_fee', 'profile_photo_url'):
                    setattr(d, field, identity.get(field))
                d.normalized_name = normalize_name(identity['name'])
                d.synthetic, d.active, d.needs_review = snap.synthetic, True, False
                d.source_id, d.source_type, d.approved_at = snap.source_id, snap.source_type, utcnow()
                s.flush()
                if not s.scalar(select(DoctorSource.id).where(DoctorSource.doctor_id == d.id, DoctorSource.proposal_id == p.id)):
                    s.add(DoctorSource(doctor_id=d.id, proposal_id=p.id))
                source = s.get(Source, snap.source_id) if snap.source_id else None
                hospital = s.get(Hospital, source.hospital_id) if source else None
                for kind, model in [('affiliations', Affiliation), ('contacts', Contact), ('registration', Registration)]:
                    s.execute(delete(model).where(model.doctor_id == d.id))
                    for i, row in enumerate(data[kind]):
                        evidence = [e for e in data['evidence'] if e['field'].startswith(f'{kind}.{i}.')]
                        item = model(doctor_id=d.id, source_proposal_id=p.id, evidence=evidence, **row)
                        if model is Affiliation and hospital and normalize_name(row['name']) == hospital.normalized_name:
                            item.hospital_id = hospital.id
                        s.add(item)
                if job.refresh_of_job_id:
                    old_job = s.get(Job, job.refresh_of_job_id)
                    old_p = s.scalar(select(Proposal).where(Proposal.job_id == old_job.id))
                    if old_p:
                        s.execute(update(Doctor).where(Doctor.proposal_id == old_p.id).values(active=False))
                    old_job.state = 'superseded'
                    s.add(Audit(job_id=old_job.id, action='superseded', actor=reviewer, details={'replacement_job_id': job.id}))
                job.state = 'approved'
            else:
                job.state = 'failed'
                job.last_error = 'Rejected by human reviewer: ' + note
            job.updated_at = utcnow()
            s.add(Audit(job_id=job_id, action='approve' if approve else 'reject', actor=reviewer, details={'note': note, 'duplicate_acknowledged': duplicate_acknowledged, 'duplicate_job_ids': duplicates}))
            return job.state

    def retry_failed(self, job_id, acknowledge_possible_additional_model_charge=False):
        with self.db.session.begin() as s:
            job = s.get(Job, job_id)
            if not job or job.state != 'failed':
                raise Conflict('Only failed fetch jobs can be retried')
            if job.refresh_of_job_id and s.scalar(select(Snapshot.id).where(Snapshot.job_id==job_id)):
                raise Conflict('Captured refresh evidence is retained; enqueue a new refresh from the original record instead of retrying')
            if s.scalar(select(Proposal.id).where(Proposal.job_id == job_id)):
                raise Conflict('Human-rejected proposals cannot be refetched; their audit record is retained')
            if job.llm_call_reserved and not acknowledge_possible_additional_model_charge:
                raise Conflict('A prior model call was reserved and may have been billed. Explicitly acknowledge possible additional model charges to retry.')
            # Compare-and-swap prevents concurrent stale retries resetting a new
            # lease or clearing its paid-call reservation.
            changed=s.execute(update(Job).where(Job.id==job_id,Job.state=='failed',Job.llm_call_sequence==job.llm_call_sequence).values(
                llm_call_reserved=False,run_number=s.get(Control,1).run_number,state='queued',attempts=0,
                available_at=utcnow(),last_error=None,lease_owner=None,lease_expires_at=None))
            if changed.rowcount!=1:
                raise Conflict('Job changed during retry; reload before retrying')
            s.add(Audit(job_id=job_id, action='manual_retry', actor='admin', details={'acknowledge_possible_additional_model_charge': acknowledge_possible_additional_model_charge}))

    def _resolve_source(self, s, host, source_id=None):
        if self.settings.mode == 'fixture':
            return None
        query = select(Source).where(Source.hostname == host, Source.active.is_(True))
        if source_id is not None:
            query = query.where(Source.id == source_id)
        sources = s.scalars(query).all()
        if len(sources) != 1:
            raise ValueError('Choose one active, human-approved official source for this hostname')
        return sources[0]

    def list_sources(self):
        with self.db.session() as s:
            return [dict(id=x.id, hospital_id=h.id, name=h.name, city=h.city, hostname=x.hostname,
                         list_url=x.list_url, sitemap_url=x.sitemap_url, profile_url_pattern=x.profile_url_pattern,
                         source_type=x.source_type, reviewer=x.reviewer, approved_at=x.approved_at.isoformat()+'Z',
                         note=x.note, active=x.active)
                    for x,h in s.execute(select(Source, Hospital).join(Hospital)).all()]

    def add_source(self, name, city, hostname, list_url, reviewer, note='', sitemap_url=None, profile_url_pattern=None):
        url = canonical_url(list_url)
        host = urlsplit(url).hostname
        if host != hostname.lower().rstrip('.') or host not in self.settings.allowed_domains:
            raise ValueError('Official source hostname must match its list URL and ALLOWED_DOMAINS')
        if not name.strip() or not reviewer.strip():
            raise ValueError('Hospital name and accountable reviewer are required')
        if sitemap_url:
            sitemap_url = canonical_url(sitemap_url)
            if urlsplit(sitemap_url).hostname != host:
                raise ValueError('Sitemap must use the same approved hostname')
        if profile_url_pattern and (not profile_url_pattern.startswith('/') or len(profile_url_pattern)>300 or '?' in profile_url_pattern or '#' in profile_url_pattern):
            raise ValueError('Profile pattern is a literal path prefix starting with /, not a regex')
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            if s.scalar(select(Source.id).where(Source.list_url==url)):
                raise Conflict('This source list URL already exists')
            hospital = s.scalar(select(Hospital).where(Hospital.normalized_name==normalize_name(name), Hospital.city==city))
            if not hospital:
                hospital=Hospital(name=name, normalized_name=normalize_name(name), city=city)
                s.add(hospital); s.flush()
            source=Source(hospital_id=hospital.id,hostname=host,list_url=url,sitemap_url=sitemap_url,
                          profile_url_pattern=profile_url_pattern,reviewer=reviewer,note=note,source_type='official')
            s.add(source);s.flush()
            s.add(Audit(action='approve_source',actor=reviewer,details={'source_id':source.id,'hostname':host,'note':note,'scope':'internal official-site research only'}))
            id=source.id
        return next(x for x in self.list_sources() if x['id']==id)

    def discover_source(self, source_id, max_pages=10, max_profiles=200, use_sitemap=False):
        from dataclasses import asdict
        import threading
        import uuid
        from app.discovery import discover
        from app.fetcher import SafeFetcher
        if not 1<=max_pages<=50 or not 1<=max_profiles<=1000:
            raise ValueError('Discovery bounds must be 1..50 pages and 1..1000 profiles')
        owner=uuid.uuid4().hex
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            source=s.get(Source,source_id)
            if not source or not source.active:
                raise ValueError('Active approved source not found')
            if s.get(Control,1).paused:
                raise WorkPaused('Research is paused')
            if s.scalar(select(DiscoveryRun.id).where(DiscoveryRun.state=='running',DiscoveryRun.lease_expires_at>utcnow()).limit(1)):
                raise Conflict('Another discovery run is active; wait for it to finish')
            run=DiscoveryRun(source_id=source.id,state='running',owner=owner,lease_expires_at=utcnow()+timedelta(seconds=120))
            s.add(run);s.flush();run_id=run.id
            s.add(Audit(action='discovery_started',actor='admin',details={'source_id':source_id,'discovery_run_id':run_id}))
        done=threading.Event()
        def heartbeat():
            while not done.wait(20):
                with self.db.session.begin() as s:
                    result=s.execute(update(DiscoveryRun).where(DiscoveryRun.id==run_id,DiscoveryRun.owner==owner,DiscoveryRun.state=='running',DiscoveryRun.lease_expires_at>utcnow()).values(lease_expires_at=utcnow()+timedelta(seconds=120),updated_at=utcnow()))
                    if result.rowcount!=1:return
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        report={}
        try:
            fetcher=SafeFetcher(self.settings,lambda url:self.reserve_request(url,run_id,owner),pace_host=self.pace_host)
            result=discover(fetcher,source.list_url,max_pages=max_pages,max_profiles=max_profiles,
                            include_sitemap=use_sitemap,sitemap_url=source.sitemap_url,
                            profile_path_prefix=source.profile_url_pattern)
            report=asdict(result);report['discovery_run_id']=run_id;report['jobs']=[]
            for url in result.urls:
                try:
                    id,created=self.enqueue(url,source_id=source.id,discovery_run_id=run_id,discovery_owner=owner)
                    report['jobs'].append({'id':id,'url':url,'created':created})
                except (ValueError,BudgetExceeded) as exc:
                    report['jobs'].append({'url':url,'error':str(exc)})
                    if isinstance(exc,BudgetExceeded):break
            with self.db.session.begin() as s:
                s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
                committed=s.execute(update(DiscoveryRun).where(DiscoveryRun.id==run_id,DiscoveryRun.owner==owner,DiscoveryRun.state=='running',DiscoveryRun.lease_expires_at>utcnow()).values(state='complete',summary=report,updated_at=utcnow()))
                if committed.rowcount!=1:raise Conflict('Discovery lease lost before final commit')
                s.add(Audit(action='discover_source',actor='admin',details={'source_id':source_id,'result':report}))
            return report
        except Exception as exc:
            with self.db.session.begin() as s:
                s.execute(update(DiscoveryRun).where(DiscoveryRun.id==run_id,DiscoveryRun.owner==owner,DiscoveryRun.state=='running').values(state='failed',summary={'error':str(exc)[:1500]},updated_at=utcnow()))
                s.add(Audit(action='discovery_failed',actor='admin',details={'source_id':source_id,'discovery_run_id':run_id,'error':str(exc)[:1500]}))
            raise
        finally:
            done.set();thread.join(timeout=2)

    def _review_record(self, s, job_id, expected_version):
        job=s.get(Job,job_id)
        proposal=s.scalar(select(Proposal).where(Proposal.job_id==job_id))
        if not job or not proposal or proposal.version!=expected_version:
            raise Conflict('Record changed or is missing; reload before acting')
        doctor=s.scalar(select(Doctor).where(Doctor.proposal_id==proposal.id))
        return job,proposal,doctor

    def _assert_unmerged(self,s,doctor,proposal):
        if not doctor:
            raise Conflict('An approved doctor record is required')
        count=s.scalar(select(func.count()).select_from(DoctorSource).where(DoctorSource.doctor_id==doctor.id))
        other=s.scalar(select(DoctorSource.id).where(DoctorSource.proposal_id==proposal.id,DoctorSource.doctor_id!=doctor.id))
        if count>1 or other:
            raise Conflict('Merged records require withdrawal and source-level reconciliation before correction or refresh')

    def withdraw(self,job_id,expected_version,reviewer,note):
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            job,p,d=self._review_record(s,job_id,expected_version)
            if job.state not in {'approved','merged','review'}:
                raise Conflict('Only approved, merged or review records can be withdrawn')
            if d:d.active=False
            # Withdrawing any contributing evidence quarantines merged targets too.
            for linked in s.scalars(select(DoctorSource).where(DoctorSource.proposal_id==p.id)):
                s.get(Doctor,linked.doctor_id).needs_review=True
            job.state='withdrawn';job.updated_at=utcnow();p.version+=1
            s.add(Audit(job_id=job_id,action='withdraw',actor=reviewer,details={'note':note,'version':p.version}))
            return job.state

    def correct(self,job_id,expected_version,reviewer,note):
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            job,p,d=self._review_record(s,job_id,expected_version)
            if job.state not in {'approved','withdrawn'}:
                raise Conflict('Only approved or withdrawn records can be reopened for correction')
            self._assert_unmerged(s,d,p)
            self._assert_no_pending_refresh(s,job.id)
            original=s.scalar(select(Snapshot).where(Snapshot.job_id==job.id))
            changed=s.scalar(select(Snapshot.id).join(Job,Job.id==Snapshot.job_id).where(Job.refresh_of_job_id==job.id,Snapshot.content_hash!=original.content_hash).limit(1))
            if changed:
                raise Conflict('A newer changed source exists; review that source or withdraw, rather than reapprove stale evidence')
            d.active=False;d.needs_review=True;job.state='review';job.updated_at=utcnow();p.version+=1
            s.add(Audit(job_id=job_id,action='correct',actor=reviewer,details={'note':note,'previous_data':p.data,'previous_review':p.review_note,'version':p.version}))
            return job.state

    def bulk_approve(self,items,reviewer,note,human_acknowledged):
        if not human_acknowledged or not 1<=len(items)<=25 or len({x['id'] for x in items})!=len(items):
            raise Conflict('Explicit human acknowledgement and 1..25 distinct reviewed proposals required')
        results=[]
        for item in items:
            try:
                state=self.review(item['id'],item['expected_version'],reviewer,note,True,False)
                results.append({'id':item['id'],'state':state})
            except Conflict as exc:
                results.append({'id':item['id'],'error':str(exc)})
        return results

    def merge(self,job_id,target_job_id,expected_version,target_expected_version,reviewer,note):
        if job_id==target_job_id:raise Conflict('Choose two different approved records')
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            job,p,d=self._review_record(s,job_id,expected_version)
            target,tp,td=self._review_record(s,target_job_id,target_expected_version)
            if job.state!='approved' or target.state!='approved' or not d or not td or not d.active or not td.active or d.needs_review or td.needs_review:
                raise Conflict('Both merge records must be currently approved and active')
            self._assert_no_pending_refresh(s,job.id)
            self._assert_no_pending_refresh(s,target.id)
            if d.synthetic!=td.synthetic:raise Conflict('Synthetic and real records cannot be merged')
            if d.normalized_name!=td.normalized_name:
                raise Conflict('Names differ; correct supported identity first rather than guessing a merge')
            for link in s.scalars(select(DoctorSource).where(DoctorSource.doctor_id==d.id)):
                if not s.scalar(select(DoctorSource.id).where(DoctorSource.doctor_id==td.id,DoctorSource.proposal_id==link.proposal_id)):
                    s.add(DoctorSource(doctor_id=td.id,proposal_id=link.proposal_id))
            for model in (Affiliation,Contact,Registration):
                s.execute(update(model).where(model.doctor_id==d.id).values(doctor_id=td.id))
            d.active=False;job.state='merged';p.version+=1;tp.version+=1
            s.add(Audit(job_id=job.id,action='merge',actor=reviewer,details={'target_job_id':target.id,'target_doctor_id':td.id,'note':note,'identity_policy':'target identity retained; all contributing evidence retained'}))
            return {'doctor_id':td.id,'target_job_id':target.id,'merged_job_id':job.id}

    def refresh_job(self,job_id,expected_version,reviewer,note,acknowledge_possible_additional_model_charge=False):
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            job,p,d=self._review_record(s,job_id,expected_version)
            if job.state!='approved':raise Conflict('Refresh requires an approved source record')
            self._assert_unmerged(s,d,p)
            if self.settings.llm_enabled and not acknowledge_possible_additional_model_charge:
                raise Conflict('Refresh may incur a new model charge; explicitly acknowledge it')
            pending=s.scalar(select(Job).where(Job.url==job.url,Job.state.in_(['queued','leased','retry','review'])))
            if pending:raise Conflict(f'An unfinished refresh already exists: job {pending.id}')
            result=s.execute(update(Control).where(Control.id==1,Control.jobs_created<self.settings.max_jobs).values(jobs_created=Control.jobs_created+1))
            if result.rowcount!=1:raise BudgetExceeded('Run job budget exhausted')
            generation=(s.scalar(select(func.max(Job.generation)).where(Job.url==job.url)) or 0)+1
            fresh=Job(url=job.url,host=job.host,source_id=job.source_id,generation=generation,refresh_of_job_id=job.id,run_number=s.get(Control,1).run_number)
            s.add(fresh);s.flush()
            s.add(Audit(job_id=fresh.id,action='refresh_enqueued',actor=reviewer,details={'previous_job_id':job.id,'note':note,'additional_charge_acknowledged':acknowledge_possible_additional_model_charge}))
            return {'id':fresh.id,'generation':generation,'state':'queued','refresh_of_job_id':job.id}

    def reset_budgets(self,reviewer,note):
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            c=s.get(Control,1)
            expired=s.scalars(select(DiscoveryRun).where(DiscoveryRun.state=='running',DiscoveryRun.lease_expires_at<=utcnow())).all()
            for run in expired:
                run.state='failed';s.add(Audit(action='discovery_expired',actor=reviewer,details={'discovery_run_id':run.id}))
            if s.scalar(select(DiscoveryRun.id).where(DiscoveryRun.state=='running',DiscoveryRun.lease_expires_at>utcnow()).limit(1)):
                raise Conflict('Wait for active discovery runs to stop before resetting budgets')
            if not c.paused or s.scalar(select(Job.id).where(Job.state.in_(['queued','retry','leased'])).limit(1)):
                raise Conflict('Pause research and drain all queued, retry and leased work before starting a new budget run')
            previous={'run_number':c.run_number,'requests_used':c.requests_used,'jobs_created':c.jobs_created,'llm_calls_used':c.llm_calls_used,'note':note}
            c.run_number+=1;c.requests_used=c.jobs_created=c.llm_calls_used=0
            s.add(Audit(action='reset_budgets',actor=reviewer,details=previous))
            return {'run_number':c.run_number,'paused':True,'previous':previous}

    def record_llm_usage(self,job_id,owner,usage):
        with self.db.session.begin() as s:
            job=s.get(Job,job_id)
            if not job or job.lease_owner!=owner or not job.llm_call_reserved:return False
            if s.scalar(select(LLMUsage.id).where(LLMUsage.job_id==job.id,LLMUsage.attempt==job.llm_call_sequence)):return False
            s.add(LLMUsage(job_id=job.id,attempt=job.llm_call_sequence,run_number=job.run_number,**usage))
            return True

    def metrics(self):
        with self.db.session() as s:
            c=s.get(Control,1)
            counts=dict(s.execute(select(Job.state,func.count()).group_by(Job.state)).all())
            usage=s.execute(select(func.sum(LLMUsage.input_tokens),func.sum(LLMUsage.output_tokens),func.sum(LLMUsage.estimated_cost_usd),func.count(LLMUsage.id)).where(LLMUsage.run_number==c.run_number)).one()
            unknown=s.scalar(select(func.count()).select_from(LLMUsage).where(LLMUsage.run_number==c.run_number,LLMUsage.estimated_cost_usd.is_(None)))
            return {'run_number':c.run_number,'counts':counts,'requests_used':c.requests_used,'jobs_created':c.jobs_created,
                    'llm_calls_reserved':c.llm_calls_used,'llm_responses_recorded':usage[3],
                    'llm_calls_without_usage':max(0,c.llm_calls_used-usage[3]),'input_tokens':usage[0],
                    'output_tokens':usage[1],'estimated_cost_usd':usage[2],'cost_unknown_responses':unknown,
                    'cost_is_partial':bool(unknown or c.llm_calls_used>usage[3]),
                    'approved_exportable':s.scalar(select(func.count()).select_from(Doctor).where(Doctor.active.is_(True),Doctor.needs_review.is_(False))),
                    'active_discovery_runs':s.scalar(select(func.count()).select_from(DiscoveryRun).where(DiscoveryRun.state=='running',DiscoveryRun.lease_expires_at>utcnow())),
                    'pending_review':counts.get('review',0),'failed':counts.get('failed',0)}

    def pace_host(self,hostname,interval):
        import time
        while True:
            with self.db.session.begin() as s:
                # Admit actual requests under a shared lock, not future slots.
                # Sleepers recheck if another worker learns a longer robots delay.
                s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
                if s.get(Control,1).paused:raise WorkPaused('Research is paused')
                now=utcnow(); row=s.get(HostPacing,hostname)
                interval=max(interval,row.interval_seconds) if row else interval
                due=max(now,row.last_request_at+timedelta(seconds=interval)) if row else now
                if row:row.interval_seconds=interval
                if due<=now:
                    if row:
                        row.last_request_at=now;row.next_request_at=now+timedelta(seconds=interval)
                    else:s.add(HostPacing(hostname=hostname,last_request_at=now,next_request_at=now+timedelta(seconds=interval),interval_seconds=interval))
                    return
                wait=max(0,(due-now).total_seconds())
            time.sleep(wait)

    def _unchanged_eligible(self,s,job,content_hash):
        if not job or not job.refresh_of_job_id:return False
        previous=s.scalar(select(Snapshot).where(Snapshot.job_id==job.refresh_of_job_id))
        old=s.get(Job,job.refresh_of_job_id)
        proposal=s.scalar(select(Proposal).where(Proposal.job_id==job.refresh_of_job_id))
        doctor=s.scalar(select(Doctor).where(Doctor.proposal_id==proposal.id)) if proposal else None
        return bool(previous and previous.content_hash==content_hash and old.state=='approved'
                    and doctor and doctor.active and not doctor.needs_review)

    def is_unchanged_refresh(self,job_id,content_hash):
        with self.db.session() as s:
            return self._unchanged_eligible(s,s.get(Job,job_id),content_hash)

    def _assert_no_pending_refresh(self,s,job_id):
        if s.scalar(select(Job.id).where(Job.refresh_of_job_id==job_id,Job.state.in_(['queued','retry','leased','review'])).limit(1)):
            raise Conflict('Pending refresh must be reviewed or withdrawn before correcting or merging its original record')

    def unmerge(self,job_id,target_job_id,expected_version,target_expected_version,reviewer,note):
        """Split a previously merged contributor; both groups return to withdrawal.

        No source is republished. Split nested groups until individual sources can
        be corrected, and reapprove every resulting individual explicitly.
        """
        if job_id==target_job_id:raise Conflict('Choose the contributor and its distinct merge target')
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            job,p,d=self._review_record(s,job_id,expected_version)
            target,tp,td=self._review_record(s,target_job_id,target_expected_version)
            if not d or not td or job.state not in {'merged','withdrawn'} or target.state not in {'approved','withdrawn'}:
                raise Conflict('Select a merged/withdrawn contributor and its approved/withdrawn target')
            if s.scalar(select(DoctorSource.id).where(DoctorSource.proposal_id==p.id,DoctorSource.doctor_id.not_in([d.id,td.id])).limit(1)):
                raise Conflict('Split the intermediate outer merged group first')
            ids=list(s.scalars(select(DoctorSource.proposal_id).where(DoctorSource.doctor_id==d.id)))
            target_ids=set(s.scalars(select(DoctorSource.proposal_id).where(DoctorSource.doctor_id==td.id)))
            if not ids or not set(ids).issubset(target_ids) or tp.id in ids:
                raise Conflict('This record is not a detachable contributor to that target')
            # The target must be the top-level group, not itself merged elsewhere.
            if s.scalar(select(DoctorSource.id).where(DoctorSource.proposal_id==tp.id,DoctorSource.doctor_id!=td.id)):
                raise Conflict('Split the outer merged group first')
            for model in (Affiliation,Contact,Registration):
                s.execute(update(model).where(model.doctor_id==td.id,model.source_proposal_id.in_(ids)).values(doctor_id=d.id))
            s.execute(delete(DoctorSource).where(DoctorSource.doctor_id==td.id,DoctorSource.proposal_id.in_(ids)))
            d.active=td.active=False;d.needs_review=td.needs_review=True
            job.state=target.state='withdrawn';p.version+=1;tp.version+=1
            s.add(Audit(job_id=job.id,action='unmerge',actor=reviewer,details={'target_job_id':target.id,'note':note,'source_proposal_ids':ids,'both_groups_withdrawn':True}))
            return {'source_job_id':job.id,'target_job_id':target.id,'state':'withdrawn','requires_reapproval':True}

    def capture_refresh_snapshot(self,job_id,owner,fetched):
        """Retain changed source evidence and quarantine before model/extraction work.

        A later model failure must not leave an observed changed source exportable.
        Failed captured refreshes are immutable; retry through a new refresh job.
        """
        with self.db.session.begin() as s:
            s.execute(update(Control).where(Control.id==1).values(updated_at=utcnow()))
            job=s.get(Job,job_id)
            if not job or job.state!='leased' or job.lease_owner!=owner or job.lease_expires_at<=utcnow():
                raise Conflict('Lease lost before refresh snapshot capture')
            if not job.refresh_of_job_id:return
            if job.source_id:
                source=s.get(Source,job.source_id)
                if not source or not source.active or source.hostname!=urlsplit(fetched.url).hostname:
                    raise Conflict('Fetched refresh no longer belongs to its approved official source')
            if s.scalar(select(Snapshot.id).where(Snapshot.job_id==job_id)):
                raise Conflict('This refresh already has immutable source evidence')
            previous=s.scalar(select(Snapshot).where(Snapshot.job_id==job.refresh_of_job_id))
            if not previous:raise Conflict('Original snapshot is missing')
            snapshot=Snapshot(job_id=job.id,url=fetched.url,text=fetched.text,content_hash=fetched.content_hash,retrieved_at=fetched.retrieved_at,synthetic=fetched.synthetic,source_id=job.source_id,source_type='synthetic' if fetched.synthetic else ('official' if job.source_id else 'unclassified'))
            s.add(snapshot)
            if previous.content_hash!=fetched.content_hash:
                proposal=s.scalar(select(Proposal).where(Proposal.job_id==job.refresh_of_job_id))
                if proposal:
                    targets=s.scalars(select(DoctorSource.doctor_id).where(DoctorSource.proposal_id==proposal.id)).all()
                    if targets:s.execute(update(Doctor).where(Doctor.id.in_(targets)).values(needs_review=True))
                s.add(Audit(job_id=job.id,action='refresh_change_observed',actor=owner,details={'previous_job_id':job.refresh_of_job_id,'previous_hash':previous.content_hash,'new_hash':fetched.content_hash,'quarantined_before_extraction':True}))
