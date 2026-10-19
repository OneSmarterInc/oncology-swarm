"""A durable DB coordinator feeding 2–4 bounded threads; no model writes to DB."""
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
import uuid
from dataclasses import asdict
from urllib.parse import urlsplit
from app.fetcher import SafeFetcher, FetchError
from app.extractor import extract
from app.service import BudgetExceeded, WorkPaused

log = logging.getLogger(__name__)

class Coordinator:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings
        self.stop_event = threading.Event()
        self.fetcher = SafeFetcher(self.settings, service.reserve_request, pace_host=service.pace_host)
        self.instance = uuid.uuid4().hex
        self.llm = None
        if self.settings.llm_enabled:
            from app.llm_extractor import LLMExtractor
            self.llm = LLMExtractor(self.settings)

    def _process(self, job_id, url, owner):
        done = threading.Event()
        def pulse():
            while not done.wait(max(5, self.settings.lease_seconds / 3)):
                try:
                    if not self.service.heartbeat(job_id, owner):
                        return
                except Exception:
                    log.exception('Heartbeat failed for job %s; lease fencing protects commits', job_id)
        heartbeat = threading.Thread(target=pulse, daemon=True)
        heartbeat.start()
        try:
            result = self.fetcher.fetch(url, same_host=urlsplit(url).hostname)
            if self.service.is_unchanged_refresh(job_id, result.content_hash):
                self.service.finish(job_id, owner, result, None)
                return
            self.service.capture_refresh_snapshot(job_id, owner, result)
            if self.llm:
                self.service.reserve_llm_call(job_id, owner)
                try:
                    extraction = self.llm.extract_with_usage(result)
                    self.service.record_llm_usage(job_id, owner, asdict(extraction.usage))
                    proposal = extraction.proposal
                except Exception as exc:
                    if getattr(exc, 'usage', None) is not None:
                        self.service.record_llm_usage(job_id, owner, asdict(exc.usage))
                    raise
            else:
                proposal = extract(result)
            self.service.finish(job_id, owner, result, proposal)
        except WorkPaused as exc:
            self.service.fail(job_id, owner, exc, retryable=True)
        except (FetchError, BudgetExceeded) as exc:
            self.service.fail(job_id, owner, exc, retryable=getattr(exc, 'retryable', False))
        except Exception as exc:
            log.exception('Job %s encountered an unexpected error', job_id)
            self.service.fail(job_id, owner, f'{type(exc).__name__}: {exc}', retryable=False)
        finally:
            done.set()
            heartbeat.join(timeout=2)

    def run(self, once=False):
        # DB-backed pacing coordinates requests across API discovery and PostgreSQL workers.
        futures = set()
        with ThreadPoolExecutor(max_workers=self.settings.worker_count, thread_name_prefix='research') as pool:
            while not self.stop_event.is_set():
                completed = {f for f in futures if f.done()}
                for f in completed:
                    try: f.result()
                    except Exception: log.exception('Worker task failed outside normal job handling')
                futures -= completed
                claimed = False
                while len(futures) < self.settings.worker_count and not self.stop_event.is_set():
                    owner = self.instance + ':' + uuid.uuid4().hex[:12]
                    job = self.service.claim(owner)
                    if not job: break
                    claimed = True
                    futures.add(pool.submit(self._process, job[0], job[1], owner))
                if once and not futures and not claimed:
                    return
                self.stop_event.wait(0.2 if once else 1.0)
        # Executor drains in-flight work before graceful shutdown; no new jobs are claimed.
