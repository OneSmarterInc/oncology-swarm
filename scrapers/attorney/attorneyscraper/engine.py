from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as ET

from .extract import extract
from .fetch import Fetcher, FetchError, Stopped
from .models import RunConfig, canonical_url, site_key

SKIP = re.compile(
    r"/(?:news|insights|events|publications|blog|careers|login|privacy|terms)(?:/|\?|$)|\.(?:pdf|jpg|png|gif|zip|mp4|docx|vcf)(?:\?|$)",
    re.I,
)


class Manager:
    """One active batch, bounded parallel hosts, durable pause/resume between pages."""

    def __init__(self, store, fetcher_factory=Fetcher):
        self.store, self.fetcher_factory = store, fetcher_factory
        self.lock = threading.RLock()
        self.thread = None
        self.stop_event = threading.Event()
        self.requested = "stopped"
        self.active_id = None

    def start(self, rid, retry=False):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError(
                    "A run is still active. Pause or stop it before starting another."
                )
            run = self.store.get(rid)
            if run["status"] in {"completed", "completed_with_issues"} and not retry:
                raise ValueError(
                    "This run is complete. Start a new run or retry its errors."
                )
            if retry:
                self.store.retry_errors(rid)
            self.stop_event = threading.Event()
            self.active_id = rid
            self.requested = "stopped"
            self.store.set_status(rid, "running")
            self.thread = threading.Thread(
                target=self._run, args=(rid,), daemon=True, name="attorney-run"
            )
            self.thread.start()

    def control(self, rid, action):
        with self.lock:
            if action == "resume":
                return self.start(rid)
            if rid != self.active_id or not self.thread or not self.thread.is_alive():
                raise ValueError("This run is not active.")
            self.requested = "paused" if action == "pause" else "stopped"
            self.store.set_status(rid, "pausing" if action == "pause" else "stopping")
            self.stop_event.set()

    def close(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.requested = "interrupted"
                self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)

    def _run(self, rid):
        try:
            config = RunConfig(**self.store.get(rid)["config"])
            groups = {}
            for src in config.sources:
                groups.setdefault(site_key(src.url), src)
            with ThreadPoolExecutor(max_workers=config.parallel_sites) as pool:
                futures = [
                    pool.submit(self._site, rid, host, src, config)
                    for host, src in groups.items()
                ]
                for future in futures:
                    future.result()
            run = self.store.get(rid)
            if self.stop_event.is_set():
                status = self.requested
            elif any(
                s["status"] in {"error", "limited"} or s["errors"] for s in run["sites"]
            ):
                status = "completed_with_issues"
            else:
                status = "completed"
            self.store.set_status(rid, status)
        except Exception as e:
            self.store.set_status(rid, "failed")
            with self.store.db() as db:
                db.execute(
                    "INSERT INTO audit(run_id,action,detail,created_at) VALUES(?,?,?,datetime('now'))",
                    (rid, "failure", f"{type(e).__name__}: {e}"[:500]),
                )

    def _site(self, rid, host, src, config):
        if self.stop_event.is_set():
            return
        self.store.set_site(rid, host, "running")
        fetcher = self.fetcher_factory(delay=config.delay, stop=self.stop_event)
        try:
            while not self.stop_event.is_set():
                summary = next(
                    s for s in self.store.get(rid)["sites"] if s["host"] == host
                )
                if summary["processed"] >= config.max_pages:
                    self.store.set_site(
                        rid,
                        host,
                        "limited" if summary["queued"] else "completed",
                        "page_limit" if summary["queued"] else "queue_exhausted",
                    )
                    return
                page = self.store.next_page(rid, host)
                if not page:
                    history = [p for p in self.store.pages(rid) if p["host"] == host]
                    reasons = {p["note"] for p in history}
                    if "depth_limit" in reasons or "frontier_limit" in reasons:
                        self.store.set_site(
                            rid,
                            host,
                            "limited",
                            "depth_limit"
                            if "depth_limit" in reasons
                            else "frontier_limit",
                        )
                    elif history and all(p["status"] == "error" for p in history):
                        self.store.set_site(rid, host, "error", "all_pages_failed")
                    else:
                        self.store.set_site(rid, host, "completed", "queue_exhausted")
                    return
                began = time.monotonic()
                try:
                    response = fetcher.get(page["url"], host)
                    links = []
                    if (
                        "xml" in response.content_type
                        or response.text.lstrip().startswith("<?xml")
                    ):
                        root = ET.fromstring(response.text)
                        for element in root.iter():
                            if element.tag.endswith("loc") and element.text:
                                try:
                                    target = canonical_url(element.text)
                                except ValueError:
                                    continue
                                if site_key(target) == host and not SKIP.search(target):
                                    links.append(
                                        {
                                            "url": target,
                                            "priority": 30
                                            if re.search(
                                                r"people|attorney|professional|health",
                                                target,
                                                re.I,
                                            )
                                            else 2,
                                        }
                                    )
                        result = {"records": []}
                    else:
                        if re.search(
                            r"<(?:title|h1)[^>]*>\s*(?:access denied|just a moment|verify you are human|attention required|403 forbidden)",
                            response.text,
                            re.I,
                        ):
                            raise FetchError(
                                "Access challenge detected; manual review required"
                            )
                        result = extract(
                            response.text,
                            response.url,
                            src.organization,
                            src.city,
                            src.state,
                        )
                        if (
                            result["text_length"] < 60
                            and not result["records"]
                            and not result["links"]
                        ):
                            raise FetchError(
                                "Empty or JavaScript-rendered page; supply a server-rendered profile URL or review manually"
                            )
                        for record in result["records"]:
                            if record["categories"] and not set(
                                record["categories"]
                            ) & set(config.categories):
                                record["status"] = "excluded"
                                record["reason"] = (
                                    "Outside the practice areas selected for this run"
                                )
                        links = result["links"]
                        if page["depth"] == 0:
                            policy = fetcher.policy(response.url, host)
                            for sitemap in policy.sitemaps[:4]:
                                links.append({"url": sitemap, "priority": 10})
                    admitted = []
                    if page["depth"] < config.max_depth:
                        for link in links:
                            try:
                                link["url"] = canonical_url(link["url"])
                            except ValueError:
                                continue
                            if site_key(link["url"]) == host and not SKIP.search(
                                link["url"]
                            ):
                                admitted.append(link)
                    # Bound frontier growth even when a large sitemap is encountered.
                    remaining = max(
                        0,
                        config.max_pages * 10
                        - summary["queued"]
                        - summary["processed"],
                    )
                    truncated = len(admitted) > remaining
                    admitted = sorted(admitted, key=lambda link: -link["priority"])[
                        :remaining
                    ]
                    note = (
                        "depth_limit"
                        if links and page["depth"] >= config.max_depth
                        else "frontier_limit"
                        if truncated
                        else ""
                    )
                    self.store.finish_page(
                        page,
                        result,
                        note=note,
                        elapsed=time.monotonic() - began,
                        links=admitted,
                    )
                except Stopped:
                    self.store.finish_page(
                        page, status="queued", note="Interrupted before page commit"
                    )
                    break
                except Exception as e:
                    self.store.finish_page(
                        page,
                        status="error",
                        note=f"{type(e).__name__}: {e}"[:500],
                        elapsed=time.monotonic() - began,
                    )
            self.store.set_site(rid, host, self.requested, "user_control")
        finally:
            fetcher.close()
