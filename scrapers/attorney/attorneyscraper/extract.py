from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .classify import classify
from .models import canonical_url, person_key

EMAIL = re.compile(
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,24}", re.I
)
PHONE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-]([2-9]\d{2})[ .-](\d{4})(?:\s*(?:ext\.?|x|extension)\s*(\d{1,6}))?(?!\d)",
    re.I,
)
GENERIC = re.compile(
    r"^(info|contact|office|admin|reception|marketing|support|intake|careers|jobs|hello|inquiries|webmaster|privacy)([._-]|@)",
    re.I,
)
BADNAME = re.compile(
    r"\b(attorneys?|lawyers?|people|team|practice|contact|about|healthcare|health|medical|services|news|insights|search|privacy|terms|firm|litigation|defense|partner|counsel|biography|overview|download|read|more|professionals|education|admissions|copyright|our|meet|learn|view|all|personal|injury|injuries|civil|rights|product|liability|birth|case|results|awards|testimonials|access|denied|forbidden|captcha|javascript|cookies|verification|error|moment)\b",
    re.I,
)


def clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?)\s+", "", value, flags=re.I)
    value = re.sub(
        r",?\s+(?:Esq\.?|J\.?D\.?|R\.?N\.?).*$", "", value, flags=re.I
    ).strip()
    parts = value.split()
    if not 2 <= len(parts) <= 6 or len(value) > 90 or BADNAME.search(value):
        return ""
    if any(
        not p[0].isalpha() or any(not c.isalpha() and c not in ".'’‐-" for c in p)
        for p in parts
    ):
        return ""
    if any(
        len(p) > 2 and not p[0].isupper()
        for p in parts
        if p not in {"de", "van", "von", "la", "del"}
    ):
        return ""
    return value


def decode_emails(text: str) -> set[str]:
    text = re.sub(r"\s*(?:\[at\]|\(at\))\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*(?:\[dot\]|\(dot\))\s*", ".", text, flags=re.I)
    return {
        m.group().lower().rstrip(".")
        for m in EMAIL.finditer(text)
        if not m.group().lower().endswith((".png", ".jpg", ".svg", ".webp"))
    }


def phone_values(text: str) -> set[str]:
    return {
        f"+1 {m[1]}-{m[2]}-{m[3]}" + (f" ext {m[4]}" if m[4] else "")
        for m in PHONE.finditer(text)
    }


def owned_email(email: str, name: str) -> bool:
    local = re.sub(r"[^a-z]", "", email.split("@")[0])
    words = [re.sub(r"[^a-z]", "", w.lower()) for w in name.split()]
    return any(len(w) >= 4 and w in local for w in (words[0], words[-1]))


def contacts(
    node, name: str, url: str, structured: dict | None = None, office=False
) -> list[dict]:
    found = {}

    def local_context(element):
        parent = element.parent
        for block in [parent, getattr(parent, "parent", None)]:
            if block is None:
                continue
            value = block.get_text(" ", strip=True)
            if len(value) <= 250 and re.search(
                r"\bfax\b|direct|assistant|paralegal|\bphone\b|\bemail\b", value, re.I
            ):
                return value
        return (
            parent.get_text(" ", strip=True)
            if parent and len(parent.get_text(" ", strip=True)) <= 250
            else str(element)
        )

    def add(kind, value, context, personal=False):
        if not value:
            return
        support = bool(re.search(r"assistant|paralegal|secretary", context, re.I))
        fax = kind == "phone" and bool(
            re.search(r"\bfax\b|^\s*F\s*[:.]", context, re.I)
        )
        if fax:
            return
        if office or (kind == "email" and GENERIC.search(value)):
            owner = "organization"
        elif support:
            owner = "support_staff"
        elif personal or (kind == "email" and owned_email(value, name)):
            owner = "individual"
        elif kind == "phone" and re.search(r"direct|\bD\s*[:.]", context, re.I):
            owner = "individual"
        else:
            owner = "unassigned"
        item = {
            "kind": kind,
            "value": value,
            "owner": owner,
            "source_url": url,
            "evidence": context[:220],
            "verification": "published_only",
        }
        key = (kind, value)
        # Specific support/organization labels override accidental broad matching.
        if key not in found or owner in {"support_staff", "organization"}:
            found[key] = item

    for a in node.select('a[href^="mailto:"],a[href^="tel:"],[data-cfemail]'):
        context = local_context(a)
        raw = unquote(a.get("href", ""))
        if a.get("data-cfemail"):
            try:
                b = bytes.fromhex(a["data-cfemail"])
                raw = "".join(chr(c ^ b[0]) for c in b[1:])
            except (ValueError, IndexError):
                continue
        for e in decode_emails(raw):
            add("email", e, context or "Published email link")
        for p in phone_values(raw.removeprefix("tel:")):
            add("phone", p, context or "Published telephone link")
    # Scan individual text blocks, retaining local labels rather than whole-page context.
    for text_node in node.find_all(string=True):
        line = str(text_node).strip()
        if not line:
            continue
        block = text_node.find_parent(["p", "li", "dd", "td"])
        context = block.get_text(" ", strip=True) if block else local_context(text_node)
        if len(context) > 500:
            context = line
        for e in decode_emails(line):
            add("email", e, context)
        for p in phone_values(line):
            add("phone", p, context)
    if structured:
        for kind, field in (("email", "email"), ("phone", "telephone")):
            raw = structured.get(field, "")
            if not isinstance(raw, str):
                continue
            values = decode_emails(raw) if kind == "email" else phone_values(raw)
            for value in values:
                add(kind, value, "Person structured data: " + raw, personal=True)
    return list(found.values())


def json_people(soup):
    def walk(obj):
        if isinstance(obj, list):
            for item in obj:
                yield from walk(item)
        elif isinstance(obj, dict):
            types = obj.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Person" in types:
                yield obj
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    yield from walk(value)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError, RecursionError):
            continue


def extract(
    html: str, url: str, organization: str = "", city: str = "", state: str = ""
) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    structured = list(json_people(soup))[:300]
    page_contacts = []
    office_values, fax_values = set(), set()
    for area in soup.select("header,footer,[role=contentinfo]"):
        if area.select_one("h1"):
            continue
        page_contacts.extend(contacts(area, "", url, office=True))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            obj = json.loads(script.string or script.get_text())
        except ValueError:
            continue
        stack = [obj]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                types = obj.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if set(types) & {"Organization", "LegalService", "LocalBusiness"}:
                    for field, kind in [("email", "email"), ("telephone", "phone")]:
                        raw = obj.get(field, "")
                        if not isinstance(raw, str):
                            continue
                        values = (
                            decode_emails(raw) if kind == "email" else phone_values(raw)
                        )
                        for value in values:
                            page_contacts.append(
                                {
                                    "kind": kind,
                                    "value": value,
                                    "owner": "organization",
                                    "source_url": url,
                                    "evidence": "Organization structured data: " + raw,
                                    "verification": "published_only",
                                }
                            )
                if isinstance(obj.get("faxNumber"), str):
                    fax_values.update(phone_values(obj["faxNumber"]))
                stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
    page_contacts = [
        c
        for c in page_contacts
        if c["kind"] == "phone"
        or c["value"].split("@")[-1].removeprefix("www.")
        == (urlsplit(url).hostname or "").removeprefix("www.")
    ]
    office_values = {(c["kind"], c["value"]) for c in page_contacts}
    for node in soup.select(
        "script,style,nav,footer,header,form,aside,[role=navigation],.related-people,.related-attorneys,.related-content,.related-posts,.news-feed,.publications,.firm-news"
    ):
        if (
            node.name == "header"
            and node.select_one("h1")
            and clean_name(node.select_one("h1").get_text(" ", strip=True))
        ):
            continue
        node.decompose()
    main = soup.select_one("main, [role=main]") or soup
    if not main.select_one("h1") and any(
        clean_name(h.get_text(" ", strip=True)) for h in soup.select("h1")
    ):
        main = soup
    heads = [(h, clean_name(h.get_text(" ", strip=True))) for h in main.select("h1")]
    heads = [(h, n) for h, n in heads if n]
    linked_names = {
        clean_name(a.get_text(" ", strip=True)) for a in main.select("a[href]")
    }
    linked_names.discard("")
    slug = re.sub(r"[^a-z]", "", urlsplit(url).path.casefold())

    def url_matches(name):
        return all(
            re.sub(r"[^a-z]", "", part.casefold()) in slug
            for part in (name.split()[0], name.split()[-1])
        )

    if not heads:
        heads = [
            (h, clean_name(h.get_text(" ", strip=True)))
            for h in main.select("h2,h3")
            if clean_name(h.get_text(" ", strip=True))
            and url_matches(clean_name(h.get_text(" ", strip=True)))
        ]
    profile_name = (
        heads[0][1]
        if heads and (url_matches(heads[0][1]) or len(linked_names - {heads[0][1]}) < 3)
        else ""
    )
    candidates = []
    if profile_name:
        person = next(
            (
                p
                for p in structured
                if p.get("name", "").casefold() == profile_name.casefold()
            ),
            {},
        )
        candidates.append((profile_name, main, url, person, True))
    else:
        for node in main.select(
            "article,.attorney-card,.person-card,.team-member,.professional-card,tr,[itemtype$='/Person']"
        ):
            heading = node.select_one("h2,h3,h4,[itemprop=name],td a,a")
            name = clean_name(heading.get_text(" ", strip=True)) if heading else ""
            if not name:
                continue
            a = heading if heading.name == "a" else heading.select_one("a[href]")
            if not a:
                a = node.select_one("a[href]")
            target = urljoin(url, a["href"]) if a else url
            if not target.startswith(("http://", "https://")):
                continue
            candidates.append((name, node, target, {}, False))
        for p in structured:
            name = clean_name(str(p.get("name", "")))
            if not name:
                continue
            # JSON-LD listing entries carry only their own text, never the site's practice list.
            node = BeautifulSoup("<div></div>", "html.parser").div
            node.string = " ".join(
                str(p.get(k, "")) for k in ("name", "jobTitle", "description")
            )
            matching_link = next(
                (
                    a
                    for a in main.select("a[href]")
                    if clean_name(a.get_text(" ", strip=True)).casefold()
                    == name.casefold()
                    and not a["href"].startswith(("#", "mailto:", "tel:"))
                ),
                None,
            )
            target = p.get("url") or (matching_link["href"] if matching_link else url)
            if not isinstance(target, str):
                target = url
            candidates.append((name, node, urljoin(url, target), p, False))
    records = []
    seen = set()
    for name, node, target, person, is_profile in candidates:
        try:
            target = canonical_url(target)
        except ValueError:
            continue
        key = person_key(name, target)
        if key in seen:
            continue
        seen.add(key)
        text = node.get_text(" ", strip=True)
        title = str(person.get("jobTitle", ""))
        if not title:
            m = re.search(
                r"\b(?:legal nurse consultant|legal assistant|paralegal|managing partner|senior counsel|general counsel|partner|associate|shareholder|attorney|lawyer|counsel)\b",
                text,
                re.I,
            )
            title = m.group() if m else ""
        result = classify(text, title)
        record = {
            "key": key,
            "name": name,
            "organization": organization,
            "title": title,
            "profile_url": target,
            "source_url": url,
            "city": city,
            "state": state,
            "location_source": "source_list" if city or state else "not_published",
            "country": "",
            "country_note": "US-targeted run; location not independently verified",
            "contacts": contacts(node, name, url, person),
            "is_profile": is_profile,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": hashlib.sha256(html.encode()).hexdigest(),
            **result,
        }
        record["contacts"] = [
            c
            for c in record["contacts"]
            if not (c["kind"] == "phone" and c["value"] in fax_values)
        ]
        for c in record["contacts"]:
            if (c["kind"], c["value"]) in office_values:
                c["owner"] = "organization"
                c["ownership_note"] = (
                    "Also published in site header/footer or organization structured data"
                )
        existing = {(c["kind"], c["value"]) for c in record["contacts"]}
        record["contacts"].extend(
            c
            for c in page_contacts
            if (c["kind"], c["value"]) not in existing and c["value"] not in fax_values
        )
        # A repeated name-only widget is not an independently supported person.
        if (
            not is_profile
            and target == canonical_url(url)
            and not result["attorney_evidence"]
            and not result["categories"]
            and not record["contacts"]
        ):
            continue
        records.append(record)
    links = []
    for a in soup.select("a[href]"):
        href = urljoin(url, a["href"])
        try:
            href = canonical_url(href)
        except ValueError:
            continue
        label = a.get_text(" ", strip=True)
        score = (
            50
            if clean_name(label)
            else 20
            if re.search(
                r"attorney|people|professional|team|health|medical|nurse|practice|page=|page/",
                href + " " + label,
                re.I,
            )
            else 1
        )
        links.append({"url": href, "priority": score})
    return {
        "records": records,
        "links": links,
        "page_contacts": page_contacts,
        "text_length": len(main.get_text(" ", strip=True)),
    }
