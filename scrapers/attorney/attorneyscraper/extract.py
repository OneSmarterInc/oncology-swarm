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
    r"(?<!\d)(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-]?([2-9]\d{2})[ .-]?(\d{4})(?:\s*(?:ext\.?|x|extension)\s*(\d{1,6}))?(?!\d)",
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


NAME_SUFFIX = re.compile(
    r",?\s+(?:P\.?C\.?|P\.?A\.?|L\.?L\.?P\.?|P\.?L\.?L\.?C\.?|L\.?L\.?C\.?|L\.?P\.?A\.?|A\.?P\.?C\.?|"
    r"C\.?P\.?A\.?|M\.?B\.?A\.?|L\.?L\.?M\.?|M\.?P\.?H\.?|M\.?H\.?A\.?|M\.?S\.?N\.?|B\.?S\.?N\.?|"
    r"Ph\.?D\.?|M\.?D\.?|CIPP(?:/[A-Z]+)?|CHC|CHPC|FACHE)(?=[\s,.]|$).*$",
)

# Page purpose is stronger evidence than title capitalization.
NON_PERSON_PATH = re.compile(
    r"/(?:services|products-services|practice-areas|practices|locations|offices|news|news-analysis|insights|publications|events|about|about-our-firm)(?:/|$)|"
    r"(?:privacy|legal-notice|service-terms|fraud-alert|information-request|meeting-request|data-protection-policy)",
    re.I,
)
PEOPLE_PATH = re.compile(
    r"/(?:people|professionals|attorneys|lawyers|team|our-team|bios?|profiles?)(?:/|$)",
    re.I,
)
ROLE = re.compile(
    r"\b(?:attorney|lawyer|counsel|partner|associate|shareholder|principal)\b", re.I
)


def name_in_url(name, url):
    slug = re.sub(r"[^a-z]", "", unquote(urlsplit(url).path).casefold())
    words = name.split()
    return bool(words) and all(
        re.sub(r"[^a-z]", "", part.casefold()) in slug for part in (words[0], words[-1])
    )


def link_priority(url, label=""):
    path = urlsplit(url).path
    if NON_PERSON_PATH.search(path):
        return 5 if re.search(r"health|medical|nurse", path, re.I) else 0
    if PEOPLE_PATH.search(path):
        return 100 if len(path.rstrip("/").split("/")) <= 2 else 90
    name = clean_name(label)
    if name and name_in_url(name, url):
        return 80
    if re.search(
        r"attorneys|professionals|our team|our people|next|page=|page/",
        label + " " + url,
        re.I,
    ):
        return 60
    return 1


def clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?)\s+", "", value, flags=re.I)
    value = re.sub(
        r",?\s+(?:Esq\.?|J\.?D\.?|R\.?N\.?).*$", "", value, flags=re.I
    ).strip()
    # Credentials and firm-entity suffixes glued after the name: "Jane Smith PLLC", "John Doe, P.C.",
    # "Mary Brown CPA, MBA". Everything from the first such token on is not part of the name.
    value = re.sub(NAME_SUFFIX, "", value).strip(" ,")
    value = re.sub(r",\s+(?=(?:Jr|Sr|II|III|IV)\.?$)", " ", value)  # "Jane Smith, Jr." stays one person
    if value.casefold() in {
        "affordable housing",
        "fraud alert",
        "intellectual property",
    }:
        return ""
    parts = value.split()
    if not 2 <= len(parts) <= 6 or len(value) > 90 or BADNAME.search(parts[0]):
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
    # Explicit international prefixes only; never guess a country for local numbers.
    international = set()

    def remove_international(match):
        raw = match.group()
        digits = re.sub(r"\D", "", raw)
        if not digits.startswith("1") and 8 <= len(digits) <= 15:
            international.add("+" + digits)
            return " "
        return raw

    text = re.sub(
        r"\+[2-9]\d{0,2}(?:[ .()-]*\d){7,13}(?!\d)", remove_international, text
    )
    return international | {
        f"+1 {m[1]}-{m[2]}-{m[3]}" + (f" ext {m[4]}" if m[4] else "")
        for m in PHONE.finditer(text)
    }


def owned_email(email: str, name: str) -> bool:
    if len(name.split()) < 2:
        return False
    local = re.sub(r"[^a-z]", "", email.split("@")[0])
    words = [re.sub(r"[^a-z]", "", w.lower()) for w in name.split()]
    first, last = words[0], words[-1]
    return local in {
        first + last,
        first[:1] + last,
        last + first,
        last + first[:1],
        first,
        "".join(words),
        "".join(w[:1] for w in words[:-1]) + last,
    }


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
        named_call = False
        if name:
            parent = a
            for _ in range(6):
                if parent is None:
                    break
                label = (
                    parent.get("aria-label", "")
                    + " "
                    + parent.get_text(" ", strip=True)
                )
                emails = decode_emails(label)
                tied_email = bool(emails) and all(
                    owned_email(email, name) for email in emails
                )
                if (
                    len(label) < 500
                    and not re.search(
                        r"\b(?:office|fax|assistant|reception|paralegal)\b", label, re.I
                    )
                    and (
                        re.search(
                            r"\bCall\s+" + re.escape(name.split()[0]) + r"\b",
                            label,
                            re.I,
                        )
                        or tied_email
                    )
                ):
                    named_call = True
                    break
                parent = parent.parent
        raw = unquote(a.get("href", ""))
        if raw.lower().startswith("mailto:"):
            raw = raw.split("?", 1)[0]  # CC/BCC/body are not the recipient's address.
        if a.get("data-cfemail"):
            try:
                b = bytes.fromhex(a["data-cfemail"])
                raw = "".join(chr(c ^ b[0]) for c in b[1:])
            except (ValueError, IndexError):
                continue
        for e in decode_emails(raw):
            add("email", e, context or "Published email link")
        for p in phone_values(raw.removeprefix("tel:")):
            add("phone", p, context or "Published telephone link", personal=named_call)
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
            if isinstance(types, list) and "Person" in types:
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
    # Discovery is independent of biography cleanup, including menu links.
    discovered_links = {}
    for anchor in soup.select("a[href]"):
        try:
            target = canonical_url(urljoin(url, anchor["href"]))
        except ValueError:
            continue
        label = anchor.get_text(" ", strip=True)
        priority = link_priority(target, label)
        discovered_links[target] = max(priority, discovered_links.get(target, 0))
    page_contacts = []
    office_values, fax_values = set(), set()
    for area in soup.select("header,footer,[role=contentinfo]"):
        if area.select_one("h1") or (
            area.name == "header" and PEOPLE_PATH.search(urlsplit(url).path)
        ):
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
                if isinstance(types, list) and any(
                    t in {"Organization", "LegalService", "LocalBusiness"}
                    for t in types
                    if isinstance(t, str)
                ):
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
        "script,style,nav,footer,header,form,[role=navigation],.related-people,.related-attorneys,.related-content,.related-posts,.news-feed,.publications,.firm-news"
    ):
        if (
            node.name == "header"
            and node.select_one("h1")
            and clean_name(node.select_one("h1").get_text(" ", strip=True))
        ):
            continue
        node.decompose()
    main = soup.select_one("main, [role=main]") or soup
    # Contact sidebars belong to the profile, but are not used as biography text.
    sidebars = [a for a in soup.select("aside") if a not in main.descendants]
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

    def url_matches(name):
        return name_in_url(name, url)

    if not heads:
        heads = [
            (h, clean_name(h.get_text(" ", strip=True)))
            for h in main.select("h2,h3")
            if clean_name(h.get_text(" ", strip=True))
            and url_matches(clean_name(h.get_text(" ", strip=True)))
        ]
    profile_name = (
        heads[0][1]
        if heads
        and not NON_PERSON_PATH.search(urlsplit(url).path)
        and (
            url_matches(heads[0][1])
            or any(
                str(p.get("name") or "").casefold() == heads[0][1].casefold()
                for p in structured
            )
            or (
                len(linked_names - {heads[0][1]}) < 3
                and ROLE.search(main.get_text(" ", strip=True))
            )
        )
        else ""
    )
    candidates = []
    if profile_name:
        person = next(
            (
                p
                for p in structured
                if str(p.get("name") or "").casefold() == profile_name.casefold()
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
            if NON_PERSON_PATH.search(urlsplit(target).path):
                continue
            if not (
                name_in_url(name, target)
                or PEOPLE_PATH.search(urlsplit(target).path)
                or node.get("itemtype", "").endswith("/Person")
            ):
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

    def belongs_to_other_person(block, name):
        heading = block.select_one("h1,h2,h3,h4")
        other = clean_name(heading.get_text(" ", strip=True)) if heading else ""
        if not other or other.casefold() == name.casefold():
            return False
        if any(
            str(p.get("name") or "").casefold() == other.casefold() for p in structured
        ):
            return True
        for anchor in block.select("a[href]"):
            href = unquote(anchor["href"])
            if href.startswith("mailto:") and any(
                owned_email(email, other) for email in decode_emails(href)
            ):
                return True
            path = re.sub(r"[^a-z]", "", urlsplit(urljoin(url, href)).path.casefold())
            if all(
                re.sub(r"[^a-z]", "", part.casefold()) in path
                for part in (other.split()[0], other.split()[-1])
            ):
                return True
        return False

    records = []
    seen = set()
    for name, node, target, person, is_profile in candidates:
        try:
            target = canonical_url(target)
        except ValueError:
            continue
        if NON_PERSON_PATH.search(urlsplit(target).path):
            continue
        key = person_key(name, target)
        if not person:
            person = next(
                (
                    p
                    for p in structured
                    if str(p.get("name") or "").casefold() == name.casefold()
                    and isinstance(p.get("url", target), str)
                    and urljoin(url, p.get("url", target)) == target
                ),
                {},
            )
        if key in seen:
            continue
        seen.add(key)
        # Remove other named people before classification/contact attribution.
        node = BeautifulSoup(str(node), "html.parser")
        for block in list(
            node.select(
                "section,article,aside,div,.person-card,.attorney-card,.team-member"
            )
        ):
            if block.parent is None:
                continue
            if belongs_to_other_person(block, name):
                block.decompose()
        text = node.get_text(" ", strip=True)
        title = str(person.get("jobTitle", ""))
        if not title:
            m = re.search(
                r"\b(?:legal nurse consultant|legal assistant|paralegal|managing partner|senior counsel|general counsel|partner|associate|shareholder|attorney|lawyer|counsel)\b",
                text,
                re.I,
            )
            title = m.group() if m else ""
        result = classify(text + " " + title, title)
        contact_node = BeautifulSoup(str(node), "html.parser")
        if is_profile:
            for aside in sidebars:
                if not belongs_to_other_person(aside, name):
                    contact_node.append(BeautifulSoup(str(aside), "html.parser"))
        extracted_contacts = contacts(contact_node, name, url, person)
        # A labeled contact block on a single-person profile establishes ownership.
        # Whole biography text is not a contact block (it can mention other people).
        if is_profile:
            for block in contact_node.select(
                "header,.contact-info,.contact-details,.profile-contact,.person-contact,[itemprop=telephone]"
            ):
                block_text = block.get_text(" ", strip=True)
                if len(block_text) > 700 or belongs_to_other_person(block, name):
                    continue
                if re.search(
                    r"\b(?:office|switchboard|reception|assistant|paralegal|fax)\b",
                    block_text,
                    re.I,
                ):
                    continue
                block_phones = phone_values(block_text)
                for anchor in block.select('a[href^="tel:"]'):
                    block_phones.update(phone_values(anchor["href"]))
                if len(block_phones) == 1:
                    for contact in extracted_contacts:
                        if (
                            contact["kind"] == "phone"
                            and contact["value"] in block_phones
                            and contact["owner"] == "unassigned"
                        ):
                            contact["owner"] = "individual"
                            contact["ownership_note"] = (
                                "Published in the named profile contact block"
                            )
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
            "contacts": extracted_contacts,
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
    links = [
        {"url": target, "priority": score} for target, score in discovered_links.items()
    ]
    return {
        "records": records,
        "links": links,
        "page_contacts": page_contacts,
        "text_length": len(main.get_text(" ", strip=True)),
    }
