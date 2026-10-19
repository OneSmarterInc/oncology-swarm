"""Page-level orchestrator: classify a page and run every extraction layer on it."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from . import listing, oncology, structured, templates
from .annotate import annotate
from .dom import links, main_content, parse
from .fetch import normalize_url
from .models import LIST_FIELDS, DoctorRecord, Page, PageResult
from .profile import (
    _assign, find_name_in_text, finalize, labels_and_sections, parse_qualifications, patterns,
    plain_person_name, primary_name, qualification_line,
)
from .vocab import DESIGNATION_RE, PROFILE_URL_HINTS


def _canon_name(raw: str) -> str | None:
    return find_name_in_text(raw) or plain_person_name(raw) or (
        find_name_in_text("Dr. " + raw) if raw and raw[0].isupper() else None)


def _surname(name: str) -> str:
    return name.split()[-1].strip(".").lower()


def apply_candidate(rec: DoctorRecord, cand: structured.Candidate, url: str) -> None:
    for f, v in cand.values.items():
        if f == "name":
            continue
        if f == "photo_url":
            val = v[0] if isinstance(v, list) else v
            if isinstance(val, str) and not val.startswith("data:"):
                rec.set(f, urljoin(url, val), cand.layer, cand.snippet, url)
            continue
        if f in LIST_FIELDS:
            items = v if isinstance(v, list) else [v]
            if f == "qualifications":
                parsed: list[str] = []
                for it in items:
                    parsed.extend(parse_qualifications(it) or [it])
                rec.set(f, parsed, cand.layer, cand.snippet, url)
            elif f == "languages":
                rec.set(f, [x.strip() for it in items for x in re.split(r"[,;/]", it) if x.strip()], cand.layer, cand.snippet, url)
            else:
                rec.set(f, items, cand.layer, cand.snippet, url)
            continue
        val = ", ".join(v) if isinstance(v, list) else v
        if f in ("about",):
            rec.set(f, val[:1500], cand.layer, cand.snippet, url)
        else:
            _assign(rec, f, val, cand.layer, cand.snippet, url)
    if cand.url:
        rec.profile_url = rec.profile_url or normalize_url(urljoin(url, cand.url))


def candidate_record(cand: structured.Candidate, url: str) -> DoctorRecord | None:
    name = _canon_name(cand.name or "")
    if not name:
        return None
    rec = DoctorRecord(source_url=url, page_type="listing")
    rec.set("name", name, cand.layer, cand.snippet, url)
    apply_candidate(rec, cand, url)
    finalize(rec, [], rec.profile_url or url)
    return rec


def extract_page(page: Page) -> PageResult:
    url = page.final_url
    if page.error or not page.html:
        return PageResult(url, "error", signals={"error": page.error})
    soup = parse(page.html)
    meta = structured.meta(soup)
    ld_docs, ld_page = structured.jsonld(soup)
    md_docs = structured.microdata(soup)
    emb_docs = structured.embedded_json(soup)
    breadcrumbs = structured.html_breadcrumbs(soup) or ld_page.get("breadcrumbs", [])
    all_links = links(soup, url)
    main = main_content(soup)
    lines = annotate(main)

    struct_docs = ld_docs + md_docs
    h1 = soup.find("h1")
    h1_text = " ".join(h1.get_text(" ", strip=True).split()) if h1 else ""
    h1_name = find_name_in_text(h1_text) if h1_text and len(h1_text) < 120 else None

    # ---- decide whether this is one doctor's profile -------------------
    single_struct = None
    if len(struct_docs) == 1:
        single_struct = struct_docs[0]
    elif h1_name:
        sur = _surname(h1_name)
        single_struct = next((c for c in struct_docs + emb_docs if c.name and sur in c.name.lower()), None)
    elif len(emb_docs) == 1 and not struct_docs:
        single_struct = emb_docs[0]

    struct_names = [n for n in [_canon_name(single_struct.name)] if n] if single_struct and single_struct.name else []
    name, name_ev, name_idx, name_src = primary_name(lines, soup, meta, url, struct_names)
    if single_struct is None and name:
        sur = _surname(name)
        first = name.split()[1].lower() if len(name.split()) > 2 else ""
        matches = [c for c in struct_docs + emb_docs if c.name and sur in c.name.lower() and first in c.name.lower()]
        if matches:
            single_struct = max(matches, key=lambda c: len(c.values))

    tpl_rec = DoctorRecord(source_url=url, profile_url=url)
    tpl_hit = templates.apply_profile(soup, url, tpl_rec)
    tpl_cards = templates.apply_listing(soup, url)

    exclude = {name} if name else set()
    card_recs = tpl_cards if tpl_cards else listing.cards(main, url, exclude)
    if not card_recs and not tpl_cards:
        card_recs = listing.repeated_cards(main, url)
    table_recs = listing.tables(main, url)
    multi = [c for c in (struct_docs + emb_docs) if c is not single_struct]

    head_text = " ".join(l.text for l in lines[name_idx: name_idx + 15])
    strong = bool(tpl_hit and tpl_rec.get("name")) or bool(h1_name) or single_struct is not None
    weak = bool(
        name and name_src in ("meta", "h1plain", "url", "heading")
        and (PROFILE_URL_HINTS.search(urlsplit(url).path) or qualification_line(head_text) or DESIGNATION_RE.search(head_text))
        and len(card_recs) <= 1 and len(table_recs) < 2
    )
    profile_evidence = strong or weak
    signals = {"h1": h1_text[:120], "name": name, "name_source": name_src, "name_evidence": name_ev[:200], "cards": len(card_recs),
               "table_rows": len(table_recs), "jsonld_doctors": len(ld_docs), "microdata_doctors": len(md_docs),
               "embedded_doctors": len(emb_docs), "template": bool(tpl_hit or tpl_cards)}

    result = PageResult(url, "other", links=[u for u, _ in all_links], link_texts=dict(all_links), signals=signals)
    result.profile_links = listing.profile_links(all_links)
    result.pagination_links = listing.pagination_links(soup, url)
    page_context = " ".join([h1_text, " ".join(breadcrumbs)])

    if profile_evidence and name:
        rec = tpl_rec if tpl_hit else DoctorRecord(source_url=url, profile_url=url)
        rec.page_type = "profile"
        rec.profile_url = normalize_url(meta.get("canonical") and urljoin(url, meta["canonical"]) or url)
        rec.set("name", tpl_rec.get("name") and _canon_name(tpl_rec.get("name")) or name,
                "template" if tpl_hit and tpl_rec.get("name") else ("h1" if h1_name else single_struct.layer if single_struct and struct_names else "patterns"),
                name_ev, url)
        if single_struct:
            apply_candidate(rec, single_struct, url)
            rec.profile_url = normalize_url(urljoin(url, meta.get("canonical") or url))
        if ld_page.get("hospital"):
            rec.set("hospital", ld_page["hospital"], "jsonld", "JSON-LD organisation", url)
        if ld_page.get("city"):
            rec.set("city", ld_page["city"], "jsonld", "JSON-LD address", url)
        labels_and_sections(rec, lines, url)
        patterns(rec, lines, name_idx, main, url, meta, breadcrumbs)
        finalize(rec, breadcrumbs, url)
        oncology.score(rec, " ".join(breadcrumbs))
        result.page_type = "profile"
        result.doctors.append(rec)

    listing_recs: list[DoctorRecord] = table_recs + list(card_recs)
    for cand in multi:
        r = candidate_record(cand, url)
        if r:
            listing_recs.append(r)
    seen = {d.get("name", "").casefold() for d in result.doctors}
    for r in listing_recs:
        key = (r.get("name") or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        if r.layers.get("name") != "template":
            finalize(r, [], r.profile_url or url)
        oncology.score(r, page_context)
        result.doctors.append(r)
    if result.page_type != "profile" and len(result.doctors) >= 1:
        result.page_type = "listing"
    # Profile links found in cards come first so the crawler follows them.
    card_links = [d.profile_url for d in result.doctors if d.page_type == "listing" and d.profile_url]
    result.profile_links = list(dict.fromkeys(card_links + result.profile_links))
    return result
