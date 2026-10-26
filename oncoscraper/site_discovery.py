"""Find hospitals and their doctor pages without asking a model to judge pages.

Search only suggests where to look. Every question that decides a result is
answered by crawling the site itself with the same extractor that builds the
final directory:

    Is this a hospital of the requested kind?   its own pages say so
    Is it in the requested place?               its own pages (or its doctors) say so
    Which page lists its oncologists?           the page our extractor reads the
                                                most oncologists from

So a hospital is returned because its doctors were actually read from its own
pages, not because a model agreed with a sentence. Doctor portals (Practo,
Justdial...) are never a source: at most they suggest a hospital NAME, which is
then resolved to an official site and verified there.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .profile import city_from, sub_specialties
from .search_candidates import exclusion_reason
from .vocab import CITY_CANON, HOSPITAL_WORDS

# "Rajiv Gandhi Cancer Institute", "Tata Memorial Hospital", "Max Healthcare"
NAME_IN_TITLE = re.compile(
    r"\b((?:[A-Z][\w.&'-]*\s+){0,6}?"
    r"(?:Hospitals?|Healthcare|Health\s+City|Medicity|Cancer\s+(?:Centre|Center|Institute|Hospital)|"
    r"Institute|Medical\s+(?:Centre|Center|College|Institute|Sciences))"
    r"(?:\s+of\s+(?:[A-Z][\w.&'-]*\s*){1,4})?)")
TYPE_WORDS = {
    "cancer": r"cancer|oncolog|tumou?r",
    "oncology": r"cancer|oncolog|tumou?r",
    "multispeciality": r"hospital",
    "multi-speciality": r"hospital",
    "super-speciality": r"hospital",
    "government": r"government|govt|aiims|state|central",
    "private": r"hospital",
}
COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
               "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "fifty": 50}
HOSPITAL_NOUN = r"(?:hospitals?|cancer\s+cent(?:re|er)s?|cancer\s+institutes?|institutes?|medical\s+cent(?:re|er)s?)"
ONCO_WORDS = re.compile(r"oncolog|cancer|tumou?r|haemato|hemato|chemo|radiotherap|bmt|bone marrow", re.I)

# Conditions about a DOCTOR that this reader cannot filter on by itself.
PERSON_CONDITIONS = re.compile(
    r"\bwith (?:an? )?(?:email|e-mail|phone|mobile|contact)|only (?:those|doctors|oncologists)|"
    r"\byears? of experience\b|\bexperience of\b|\bwho (?:have|has|work|speak)|\bspeaks?\b|"
    r"\b(?:female|male|woman|women|lady)\b|\bfee\b|\bconsultation\b", re.I)


@dataclass
class Request:
    """What the user asked for, read from the prompt without a model."""
    count: int = 10
    kind: str = ""
    location: str = ""
    target: str = "oncologists"
    names: list[str] = field(default_factory=list)
    specialties: list[str] = field(default_factory=list)
    unfiltered_conditions: bool = False

    def as_dict(self):
        return {"count": self.count, "kind": self.kind, "location": self.location, "target": self.target,
                "names": list(self.names), "specialties": list(self.specialties),
                "unfiltered_conditions": self.unfiltered_conditions}

    def summary(self):
        what = " ".join(w for w in [self.kind, "hospitals"] if w)
        where = f" in {self.location}" if self.location else ""
        named = f" named: {', '.join(self.names)}" if self.names else ""
        spec = f", {', '.join(self.specialties)} only" if self.specialties else ""
        return f"{self.count} {what}{where}{named}{spec} -> official {self.target} pages"


def read_request(prompt):
    """Read count, hospital type, city, target, specialties and named hospitals from plain words.

    Deliberately literal: it never invents a city or a type. Anything it cannot
    read stays empty and simply widens the search instead of narrowing it wrongly.
    """
    text = " ".join(str(prompt or "").split())
    low = text.casefold()
    names = []
    for match in NAME_IN_TITLE.finditer(text):
        name = " ".join(match.group(1).split()).strip(" ,-|")
        # "cancer hospitals" is a type, not the name of a hospital.
        if (8 <= len(name) <= 90 and not re.match(r"^(?:the\s+)?(?:\w+\s+)?(?:hospitals?|institutes?)$", name, re.I)
                and not re.match(r"^(?:\w+\s+)?cancer\s+(?:centres?|centers?|institutes?|hospitals?)$", name, re.I)
                and name.casefold() not in {n.casefold() for n in names}):
            names.append(name)
    kind = next((k for k in TYPE_WORDS if re.search(rf"\b{k}\b", low)), "")
    if kind == "oncology":
        kind = "cancer"
    city = city_from(text) or ""
    if city and names and city.casefold() in " ".join(names).casefold():
        city = ""  # the city appeared only inside a hospital's own name
    count_match = re.search(rf"\b(\d{{1,3}})\s+(?:[\w-]+\s+){{0,2}}?{HOSPITAL_NOUN}", low)
    word_match = re.search(r"\b(" + "|".join(COUNT_WORDS) + rf")\s+(?:[\w-]+\s+){{0,2}}?{HOSPITAL_NOUN}", low)
    if count_match:
        count = int(count_match.group(1))
    elif word_match:
        count = COUNT_WORDS[word_match.group(1)]
    else:
        count = len(names) or 10
    # 'radiation oncologists' -> Radiation Oncology. Generic 'oncologists' is no filter.
    spec_text = re.sub(r"oncologists?\b", "oncology", low)
    specialties = sub_specialties(spec_text)
    wants_all = bool(re.search(r"\ball (?:the )?doctors\b|\bevery doctor\b|\ball specialit", low))
    target = "doctors" if wants_all or (re.search(r"\bdoctors?\b", low) and not ONCO_WORDS.search(low)) else "oncologists"
    return Request(count=max(1, min(count, 100)), kind=kind, location=city, target=target, names=names,
                   specialties=specialties, unfiltered_conditions=bool(PERSON_CONDITIONS.search(text)))


@dataclass
class Hospital:
    """One verified hospital: its own name, its own doctor pages."""
    name: str
    host: str
    homepage: str
    city: str = ""
    doctor_urls: list[str] = field(default_factory=list)
    doctors_found: int = 0
    oncologists_found: int = 0
    pages_read: int = 0
    evidence: dict = field(default_factory=dict)

    def as_dict(self):
        return {"name": self.name, "host": self.host, "homepage": self.homepage, "city": self.city,
                "doctor_urls": self.doctor_urls, "doctors_found": self.doctors_found,
                "oncologists_found": self.oncologists_found, "pages_read": self.pages_read,
                "evidence": self.evidence}


@dataclass
class Rejected:
    host: str
    reason: str
    name: str = ""
    city: str = ""
    pages_read: int = 0


def site_queries(kind="", location="", target="oncologists", extra=()):
    """Plain searches a person would type."""
    what = " ".join(w for w in [kind.strip(), "hospitals"] if w).strip()
    where = location.strip()
    who = "oncologists" if target == "oncologists" else "doctors"
    base = [f"{what} in {where}" if where else what,
            f"{what} {where} official website".strip(),
            f"best {who} {where} hospital".strip(),
            f"{what} {where} find a doctor {'oncology' if who == 'oncologists' else ''}".strip(),
            f"{what} {where} our doctors".strip(),
            f"list of {what} in {where}" if where else f"list of {what}"]
    return list(dict.fromkeys([" ".join(q.split()) for q in [*extra, *base] if q and len(q) < 200]))


def names_from_results(results, limit=25):
    """Hospital names suggested by result titles. A hint for the next search only."""
    names = []
    for item in results:
        title = str(item.get("title") or "")
        for match in NAME_IN_TITLE.finditer(title):
            name = " ".join(match.group(1).split()).strip(" ,-|")
            if 8 <= len(name) <= 90 and name.casefold() not in {n.casefold() for n in names}:
                names.append(name)
    return names[:limit]


def candidate_hosts(results, allow=lambda url: True):
    """Likely official sites, best first, one entry per host."""
    seen = {}
    for item in results:
        url = str(item.get("url") or "")
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        host = (parts.hostname or "").lower().removeprefix("www.")
        if not host or parts.scheme not in ("http", "https") or exclusion_reason(url) or not allow(url):
            continue
        text = " ".join(str(item.get(k) or "") for k in ("title", "content"))
        score = (3 * bool(re.search(r"hospital|cancer cent|institute|healthcare|medicity", text, re.I))
                 + 2 * bool(re.search(r"hospital|cancer|health|care|medic|onco", host, re.I))
                 + 2 * bool(re.search(r"oncolog|doctor|consultant|specialist", url + " " + text, re.I)))
        best = seen.get(host)
        if best is None or score > best[0]:
            seen[host] = (score, f"{parts.scheme}://{parts.netloc}/")
    scored = sorted(((score, host, home) for host, (score, home) in seen.items()), key=lambda r: (-r[0], r[1]))
    return [(host, homepage) for _, host, homepage in scored]


def _canon_city(text):
    city = city_from(text) or text.strip()
    return CITY_CANON.get(city, city)


def read_identity(page):
    """The site's own name and city, from its home page: title, banner, address."""
    from .dom import parse
    from .structured import meta as page_meta
    soup = parse(page.html)
    info = page_meta(soup)
    heading = soup.find("h1")
    candidates = [info.get("og:site_name") or "", heading.get_text(" ", strip=True) if heading else "",
                  *re.split(r"\s*[|–—]\s*|\s+[-]\s+", info.get("title") or "")]
    name = ""
    for option in candidates:
        option = " ".join(str(option).split()).strip(" ,-|")
        if 4 <= len(option) <= 90 and (HOSPITAL_WORDS.search(option) or re.search(r"cancer|onco", option, re.I)):
            name = option
            break
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    text = " ".join(soup.stripped_strings)[:20000]
    return name, (city_from(text) or ""), text


def survey_site(homepage, make_crawler, kind="", location="", target="oncologists", budget=45):
    """Crawl a little of one site and let the extractor answer which doctors are there."""
    started = time.monotonic()
    crawler = make_crawler()
    host = (urlsplit(homepage).hostname or "").removeprefix("www.")
    if budget:
        crawler.deadline = time.time() + budget
    home = crawler.fetcher.get(homepage)  # identity first, on the same paced fetcher
    if home.error or home.status != 200:
        crawler.fetcher.close()
        return None, Rejected(host, f"Home page could not be read: {home.error or home.status}"[:200])
    name, home_city, home_text = read_identity(home)
    result = crawler.run([homepage])
    spent = time.monotonic() - started
    pages_read = len(result.pages)
    if budget and spent >= budget and pages_read < 3:
        return None, Rejected(host, f"The site answered too slowly: {pages_read} page(s) in {budget} seconds",
                              name, home_city, pages_read)
    wanted = result.oncologists if target == "oncologists" else result.doctors
    hospital_names = Counter(d.get("hospital") for d in wanted if d.get("hospital"))
    if not name:
        name = next((n for n, _ in hospital_names.most_common() if HOSPITAL_WORDS.search(str(n))), "")
    doctor_cities = Counter(d.get("city") for d in wanted if d.get("city"))

    # Doctor pages: the listing pages that produced the wanted doctors, best first.
    by_page = Counter()
    wanted_urls = set()
    for d in wanted:
        for url in [d.source_url, *d.found_on]:
            if url:
                wanted_urls.add(url)
    for page in result.pages:
        if page.doctors and page.page_type == "listing" and page.url in wanted_urls:
            by_page[page.url] += page.doctors
    if not by_page:  # no listing: the profiles themselves are the pages
        for d in wanted:
            if d.profile_url:
                by_page[d.profile_url] += 1
    doctor_urls = [url for url, _ in by_page.most_common(4)]
    city = home_city or next(iter(doctor_cities), "")
    hospital = Hospital(name=str(name).strip(), host=host, homepage=homepage, city=city,
                        doctor_urls=doctor_urls, doctors_found=len(result.doctors),
                        oncologists_found=len(result.oncologists), pages_read=pages_read)

    blob = " | ".join(filter(None, [str(name), home_text, *(str(d.get("specialty") or "") for d in result.doctors),
                                    *(str(d.get("department") or "") for d in result.doctors)]))
    if not name or not (HOSPITAL_WORDS.search(str(name)) or re.search(r"cancer|onco", str(name), re.I)):
        return None, Rejected(host, "No hospital name found on the site", name, city, pages_read)
    if not doctor_urls:
        who = "oncologists" if target == "oncologists" else "doctors"
        return None, Rejected(host, f"No page on this site listed {who} that the extractor could read",
                              name, city, pages_read)
    if location:
        want = _canon_city(location)
        stated = {CITY_CANON.get(c, c).casefold() for c in doctor_cities}
        found = CITY_CANON.get(city, city)
        in_text = re.search(rf"\b{re.escape(want)}\b", home_text, re.I) or re.search(
            rf"\b{re.escape(location.strip())}\b", home_text, re.I)
        if want.casefold() in stated or in_text:
            # Hospital chains list many cities; the site naming this one is enough to look closer.
            hospital.evidence["location"] = f"{want} read from this site"
        elif found:
            return None, Rejected(host, f"Site says {found}, not {want}", name, city, pages_read)
        else:
            return None, Rejected(host, f"Site never states {want}", name, city, pages_read)
    if kind:
        pattern = TYPE_WORDS.get(kind.strip().casefold().split()[0], re.escape(kind.strip()))
        if not re.search(pattern, blob, re.I):
            return None, Rejected(host, f"Nothing on this site shows it is a {kind} hospital", name, city, pages_read)
        hospital.evidence["institution_type"] = f"{kind} confirmed in the site's own pages"
    count = hospital.oncologists_found if target == "oncologists" else hospital.doctors_found
    hospital.evidence["doctors"] = f"{count} {target} read from {len(doctor_urls)} page(s)"
    return hospital, None


def discover_hospitals(search, make_crawler, want=10, kind="", location="", target="oncologists",
                       queries=(), names=(), max_searches=12, max_sites=40, workers=6,
                       site_seconds=45, control=lambda: None, progress=lambda event: None):
    """Search, then prove each site by crawling it. Returns (hospitals, rejected, notes)."""
    notes, results, searched = [], [], []
    if names:  # named hospitals: look them up, do not browse a city list
        planned = [q for n in names for q in (f"{n} official website", f"{n} find a doctor")]
    else:
        planned = site_queries(kind, location, target, queries)

    def run_search(query):
        control()
        progress({"phase": "searching", "current": query})
        try:
            found = search.search(query)
        except Exception as exc:  # one bad query must not end discovery
            notes.append(f"Search failed for '{query}': {exc}"[:300])
            return
        searched.append(query)
        details = getattr(search, "search_details", {}).get(query, {})
        results.extend(details.get("results") or [{"url": u} for u in found])
        progress({"phase": "searching", "current": query, "searches": len(searched)})

    for query in planned[:max_searches]:
        run_search(query)
    # A name seen in any title is only a lead; the official site still has to prove it.
    for name in names_from_results(results)[: max(0, max_searches - len(searched))]:
        run_search(f"{name} official website")

    hosts = candidate_hosts(results)[:max_sites]
    progress({"phase": "checking_sites", "current": f"{len(hosts)} candidate sites"})
    hospitals, rejected, fatal = [], [], []
    lock = threading.Lock()
    pending = list(hosts)
    counts = {"checked": 0, "pages": 0}

    def report_counts(current):
        progress({"phase": "checking_sites", "current": current, "found": len(hospitals),
                  "checked": counts["checked"], "sites": len(hosts), "searches": len(searched),
                  "pages": counts["pages"]})

    def worker():
        while True:
            try:
                control()  # Pause and Stop must leave the threads, not be swallowed
                with lock:
                    if not pending or len(hospitals) >= want or fatal:
                        return
                    host, homepage = pending.pop(0)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
                with lock:
                    fatal.append(exc)
                return
            report_counts(f"Reading {host}")
            try:
                hospital, reject = survey_site(homepage, make_crawler, kind, location, target, budget=site_seconds)
            except Exception as exc:
                hospital, reject = None, Rejected(host, f"Site could not be read: {exc}"[:200])
            with lock:
                counts["checked"] += 1
                counts["pages"] += hospital.pages_read if hospital else (reject.pages_read if reject else 0)
                if hospital and len(hospitals) < want:
                    hospitals.append(hospital)
                elif reject:
                    rejected.append(reject)
            report_counts(f"Verified {hospital.name}" if hospital else
                          f"{host}: {reject.reason if reject else 'no result'}")

    threads = [threading.Thread(target=worker, daemon=True, name=f"site-{i}")
               for i in range(max(1, min(workers, len(pending) or 1)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        while thread.is_alive():
            thread.join(0.2)
    if fatal:
        raise fatal[0]
    key = (lambda h: (-h.oncologists_found, h.name)) if target == "oncologists" else (lambda h: (-h.doctors_found, h.name))
    hospitals.sort(key=key)
    if len(hospitals) < want:
        notes.append(f"Requested {want} hospitals; {len(hospitals)} had {target} pages this run could verify "
                     f"({len(hosts)} sites checked, {len(rejected)} did not qualify)")
    return hospitals[:want], rejected, notes


def sources_from_hospitals(hospitals):
    """The existing Excel/batch source rows, straight from verified pages."""
    from .sources import Source
    return [Source(row=index, hospital=h.name, urls=list(h.doctor_urls), city=h.city)
            for index, h in enumerate(hospitals, start=2)]
