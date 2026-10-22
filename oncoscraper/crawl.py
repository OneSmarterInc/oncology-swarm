"""Same-site crawler: sitemap + link discovery, prioritised toward doctor and oncology pages."""
from __future__ import annotations

import gzip
import heapq
import itertools
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .config import ScrapeConfig
from .contacts import demote_shared, demote_sitewide
from .dom import same_site
from .extract import extract_page
from .fetch import Fetcher, normalize_url
from . import oncology
from .models import DoctorRecord, PageResult
from .profile import clean_fields
from .vocab import LINK_TEXT_HINTS, LISTING_URL_HINTS, ONCO_URL_HINTS, PAGINATION_RE, PROFILE_URL_HINTS, SKIP_URL

log = logging.getLogger(__name__)


def name_key(name: str) -> str:
    n = re.sub(r"^(?:dr|prof|professor|doctor)\.?\s+", "", (name or "").lower())
    n = re.sub(r"\((?:dr|prof|col|brig|maj|lt|gen)[^)]*\)\s*", "", n)
    n = re.sub(r"\b(?:brig|col|lt|maj|gen|capt)\.?\s+", "", n)
    return re.sub(r"[^a-z]", "", n)


def loose_key(name: str) -> str | None:
    """Name without initials: 'Dr. Ch. Mohana Vamsy' and 'Dr. Mohana Vamsy' -> 'mohanavamsy'.
    Needs two full words left, so 'Dr. A. Kumar' and 'Dr. B. Kumar' never meet."""
    n = re.sub(r"^(?:dr|prof|professor|doctor)\.?\s+", "", (name or "").lower())
    words = [w for w in re.split(r"[\s.]+", n) if len(re.sub(r"[^a-z]", "", w)) > 2]
    return "".join(re.sub(r"[^a-z]", "", w) for w in words) if len(words) >= 2 else None


def _seen_key(url: str) -> str:
    """www.site.in/x and site.in/x are the same page; crawl it once."""
    return re.sub(r"^(https?://)www\.", r"\1", url, flags=re.I)


def has_initials(name: str) -> bool:
    n = re.sub(r"^(?:dr|prof|professor|doctor)\.?\s+", "", (name or "").lower())
    return any(len(re.sub(r"[^a-z]", "", w)) <= 2 for w in re.split(r"[\s.]+", n) if re.sub(r"[^a-z]", "", w))


def url_key(url: str | None) -> str | None:
    if not url:
        return None
    p = urlsplit(url)
    host = (p.hostname or "").removeprefix("www.")
    return f"{host}{p.path.rstrip('/').lower()}" + (f"?{p.query}" if p.query else "")


class Merger:
    """Collects records and merges duplicates of the same doctor."""

    def __init__(self):
        self.records: list[DoctorRecord] = []
        self._by_url: dict[str, DoctorRecord] = {}
        self._by_name: dict[str, list[DoctorRecord]] = {}
        self._by_loose: dict[str, list[DoctorRecord]] = {}

    def add(self, rec: DoctorRecord) -> DoctorRecord:
        key_u = url_key(rec.profile_url)
        key_n = name_key(rec.get("name", ""))
        target = self._by_url.get(key_u) if key_u else None
        if target is None and key_n:
            loose = loose_key(rec.get("name", ""))
            exact = self._by_name.get(key_n, [])
            candidates = [(c, True) for c in exact] + [(c, False) for c in self._by_loose.get(loose, []) if c not in exact]
            for cand, is_exact in candidates:
                h1, h2 = (cand.get("hospital") or "").lower(), (rec.get("hospital") or "").lower()
                same_hosp = not h1 or not h2 or h1[:12] == h2[:12] or h1 in h2 or h2 in h1
                s1, s2 = cand.source_hospital, rec.source_hospital
                same_hosp = same_hosp and (not s1 or not s2 or s1 == s2)
                u1, u2 = url_key(cand.profile_url), key_u
                c1, c2 = cand.get("city"), rec.get("city")
                # A link to the doctor's own off-site website is not a second profile on this site.
                urls_ok = not u1 or not u2 or u1 == u2 or u1.split("/", 1)[0] != u2.split("/", 1)[0]
                if is_exact:
                    # Same full name on one site = same doctor, even on two pages of the site
                    # (MOC '/our-team/x' and '/north-india/doctor/x'), when both cities are known and equal.
                    ok = urls_ok or (len(key_n) >= 8 and c1 and c2 and c1 == c2)
                else:
                    # Only initials differ ('Ch. Mohana Vamsy' / 'Mohana Vamsy'): one side must have no initials,
                    # so 'K. Srinivas Rao' and 'P. Srinivas Rao' stay two doctors.
                    ok = urls_ok and (not has_initials(cand.get("name", "")) or not has_initials(rec.get("name", "")))
                if same_hosp and ok:
                    target = cand
                    break
        if target is None:
            self.records.append(rec)
            target = rec
        elif target is not rec:
            # A profile-page record should become the base.
            if rec.page_type == "profile" and target.page_type != "profile":
                rec.absorb(target, fill_only=True)
                rec_found = target.found_on + [target.source_url]
                target.values, target.layers, target.evidence = rec.values, rec.layers, rec.evidence
                target.source_url, target.page_type = rec.source_url, "profile"
                target.profile_url = rec.profile_url or target.profile_url
                target.oncology_score = max(rec.oncology_score, target.oncology_score)
                target.oncology_terms = list(dict.fromkeys(rec.oncology_terms + target.oncology_terms))
                target.found_on = [u for u in dict.fromkeys(rec_found) if u and u != target.source_url]
            else:
                target.absorb(rec, fill_only=target.page_type == "profile" and rec.page_type != "profile")
        for k in {url_key(target.profile_url), key_u} - {None}:
            self._by_url[k] = target
        if key_n:
            bucket = self._by_name.setdefault(key_n, [])
            if target not in bucket:
                bucket.append(target)
        lk = loose_key(rec.get("name", ""))
        if lk:
            bucket = self._by_loose.setdefault(lk, [])
            if target not in bucket:
                bucket.append(target)
        return target


@dataclass
class PageLog:
    url: str
    status: int
    page_type: str
    doctors: int
    depth: int
    note: str = ""


@dataclass
class CrawlResult:
    start_urls: list[str]
    doctors: list[DoctorRecord] = field(default_factory=list)
    pages: list[PageLog] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def oncologists(self) -> list[DoctorRecord]:
        return [d for d in self.doctors if d.is_oncology]


class Crawler:
    def __init__(self, cfg: ScrapeConfig, progress: Callable[[dict], None] | None = None,
                 stop_event: threading.Event | None = None, pause_event: threading.Event | None = None):
        self.cfg = cfg.validate()
        self.pause_event = pause_event or threading.Event()  # set = paused
        self.fetcher = Fetcher(cfg)
        self.progress = progress or (lambda e: None)
        self.stop_event = stop_event or threading.Event()
        self._frontier: list[tuple[float, int, str, int, bool]] = []
        self._seq = itertools.count()
        self._queued: set[str] = set()
        self._visited: set[str] = set()
        self.merger = Merger()
        self._onco_profile_urls: set[str] = set()
        self.value_pages: dict[str, set[str]] = {}
        self.result_pages: list = []  # phone/email -> pages that show it
        self.blocked: list[str] = []

    # -- frontier -----------------------------------------------------------
    def _score(self, url: str, depth: int, from_onco: bool, hint: str = "") -> float | None:
        p = urlsplit(url)
        path = p.path + ("?" + p.query if p.query else "")
        if SKIP_URL.search(path) or SKIP_URL.search(url):
            return None
        s = 10.0
        if PROFILE_URL_HINTS.search(p.path):
            s += 50
        if LISTING_URL_HINTS.search(path):
            s += 35
        if ONCO_URL_HINTS.search(path):
            s += 30
        if PAGINATION_RE.search(path):
            s += 25
        if from_onco:
            s += 15
        if hint == "onco_profile":
            s += 80
        elif hint == "profile":
            s += 20
        elif hint == "pagination":
            s += 40
        for pat in self.cfg.extra_profile_patterns:
            if pat and pat in p.path:
                s += 50
        return s - 4 * depth

    def _push(self, url: str, depth: int, from_onco: bool = False, hint: str = "", start: str = "") -> None:
        url = normalize_url(url)
        if _seen_key(url) in self._queued or depth > self.cfg.max_depth:
            return
        if start and not same_site(url, start, self.cfg.include_subdomains):
            return
        score = self._score(url, depth, from_onco, hint)
        if score is None:
            return
        self._queued.add(_seen_key(url))
        try:
            if not self.fetcher.robots_for(url).allowed(url):
                self.blocked.append(url)
                return
        except Exception:  # robots fetch problems are handled again at fetch time
            pass
        heapq.heappush(self._frontier, (-score, next(self._seq), url, depth, from_onco))

    # -- sitemaps -----------------------------------------------------------
    def _sitemap_urls(self, start: str) -> list[str]:
        origin = f"{urlsplit(start).scheme}://{urlsplit(start).netloc}"
        robots = self.fetcher.robots_for(start)
        todo = list(dict.fromkeys(robots.sitemaps + [origin + "/sitemap.xml"]))
        found: list[str] = []
        fetched = 0
        while todo and fetched < 12 and len(found) < 50000:
            sm = todo.pop(0)
            fetched += 1
            try:
                self.fetcher.check_allowed(sm)
                resp = self.fetcher._raw_get(sm)  # noqa: SLF001
            except Exception as exc:
                log.info("sitemap %s skipped: %s", sm, exc)
                continue
            if resp.status_code != 200:
                continue
            body = resp.content
            if sm.endswith(".gz") or body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    continue
            try:
                root = ElementTree.fromstring(body)
            except ElementTree.ParseError:
                continue
            locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
            if root.tag.endswith("sitemapindex"):
                # Doctor sitemaps first.
                locs.sort(key=lambda u: 0 if re.search(r"doctor|physician|consultant|team|faculty|people|profile", u, re.I) else 1)
                todo.extend(locs[:40])
            else:
                found.extend(locs)
        self.progress({"event": "sitemap", "urls": len(found)})
        return found

    # -- main loop ------------------------------------------------------------
    def run(self, start_urls: list[str]) -> CrawlResult:
        starts = [normalize_url(u if re.match(r"https?://", u) else "https://" + u) for u in start_urls]
        result = CrawlResult(starts)
        self.result_pages = result.pages  # live page log, read by the UI for partial downloads
        t0 = time.time()
        for s in starts:
            self._queued.discard(_seen_key(s))
            self._push(s, 0, hint="pagination")
            if self.cfg.use_sitemaps:
                for u in self._sitemap_urls(s):
                    if same_site(u, s, self.cfg.include_subdomains):
                        path = urlsplit(u).path
                        if PROFILE_URL_HINTS.search(path) or LISTING_URL_HINTS.search(path) or any(p in path for p in self.cfg.extra_profile_patterns if p):
                            self._push(u, 1, from_onco=bool(ONCO_URL_HINTS.search(path)), start=s)
        fetched = 0
        counts = {"profile": 0, "listing": 0, "other": 0, "error": 0}
        try:
            while self._frontier and fetched < self.cfg.max_pages and not self.stop_event.is_set():
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    time.sleep(0.3)  # paused: keep everything found so far and wait for Resume
                if self.stop_event.is_set():
                    break
                _, _, url, depth, from_onco = heapq.heappop(self._frontier)
                if _seen_key(url) in self._visited:
                    continue
                self._visited.add(_seen_key(url))
                page = self.fetcher.get(url)
                fetched += 1
                if page.final_url != url:
                    self._visited.add(_seen_key(page.final_url))
                pr: PageResult = extract_page(page)
                for v in pr.signals.get("page_contacts", []):
                    self.value_pages.setdefault(v, set()).add(page.final_url)
                counts[pr.page_type] = counts.get(pr.page_type, 0) + 1
                note = page.error or ("rendered" if page.rendered else "")
                result.pages.append(PageLog(page.final_url, page.status, pr.page_type, len(pr.doctors), depth, note))
                start = next((s for s in starts if same_site(page.final_url, s, self.cfg.include_subdomains)), starts[0])
                page_onco = bool(ONCO_URL_HINTS.search(urlsplit(page.final_url).path)) or any(d.is_oncology for d in pr.doctors) or from_onco
                for rec in pr.doctors:
                    merged = self.merger.add(rec)
                    if merged.is_oncology and merged.profile_url:
                        self._onco_profile_urls.add(normalize_url(merged.profile_url))
                # Follow links.
                if pr.page_type != "error":
                    for d in pr.doctors:
                        if d.page_type == "listing" and d.profile_url:
                            self._push(d.profile_url, depth + 1, page_onco, "onco_profile" if d.is_oncology else "profile", start)
                    for u in pr.pagination_links:
                        self._push(u, depth, page_onco, "pagination", start)
                    for u in pr.profile_links:
                        self._push(u, depth + 1, page_onco, "profile", start)
                    if pr.page_type != "profile":
                        for u in pr.links:
                            path = urlsplit(u).path
                            text = pr.link_texts.get(u, "")
                            if (LISTING_URL_HINTS.search(path) or ONCO_URL_HINTS.search(path) or PROFILE_URL_HINTS.search(path)
                                    or LINK_TEXT_HINTS.search(text) or depth == 0):
                                self._push(u, depth + 1, page_onco or bool(ONCO_URL_HINTS.search(text)), "", start)
                self.progress({"event": "page", "url": page.final_url, "type": pr.page_type, "status": page.status,
                               "doctors_on_page": len(pr.doctors), "fetched": fetched, "queued": len(self._frontier),
                               "doctors_total": len(self.merger.records),
                               "oncologists": sum(1 for d in self.merger.records if d.is_oncology),
                               "error": page.error})
        except KeyboardInterrupt:
            self.stop_event.set()
        finally:
            self.fetcher.close()
        result.doctors = self.merger.records
        for d in result.doctors:  # merged records: check the final title once more
            clean_fields(d)
            oncology.veto(d)
        demote_sitewide(result.doctors, self.value_pages)
        demote_shared(result.doctors)
        result.stats = {
            "pages_fetched": fetched, "requests": self.fetcher.requests_made, "page_types": counts,
            "doctors_found": len(result.doctors), "oncologists": len(result.oncologists),
            "profiles_fetched": sum(1 for d in result.doctors if d.page_type == "profile"),
            "seconds": round(time.time() - t0, 1), "stopped_early": self.stop_event.is_set(),
            "frontier_left": len(self._frontier), "blocked_by_robots": len(self.blocked),
        }
        self.progress({"event": "done", **result.stats})
        return result


def scrape_page(url: str, cfg: ScrapeConfig | None = None) -> PageResult:
    """Fetch and extract a single URL without crawling."""
    cfg = (cfg or ScrapeConfig()).validate()
    fetcher = Fetcher(cfg)
    try:
        return extract_page(fetcher.get(url))
    finally:
        fetcher.close()
