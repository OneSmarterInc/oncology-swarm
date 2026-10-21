"""Single-doctor profile extraction from labels, sections and text patterns."""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from . import emails, phones
from .annotate import Line, annotate
from .models import LIST_FIELDS, DoctorRecord, Evidence
from .vocab import (
    CITY_CANON, CITY_RE, DESIGNATION_RE, EMAIL_RE, EXPERIENCE_RE, FEE_RE, FEE_RE2, HOSPITAL_WORDS, LANG_RE,
    NAME_RE, NAME_STOP, OPD_RE, PHONE_RE, QUAL_RE, QUAL_STRONG, REG_RE, SUB_SPECIALTIES, clean_name,
    label_field, label_key, normalize_quals,
)

SPLIT_RE = re.compile(r"\s*(?:[,;|•·]|\s/\s|\n)\s*")


# --------------------------------------------------------------------------
# value parsers
def parse_experience(text: str) -> str | None:
    m = EXPERIENCE_RE.search(text)
    if m:
        years = next(g for g in m.groups() if g)
        if 0 < int(years) < 70:
            return years
    m = re.fullmatch(r"\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)?\.?\s*", text, re.I)
    if m and 0 < int(m.group(1)) < 70:
        return m.group(1)
    return None


def parse_qualifications(text: str) -> list[str]:
    """Return qualification phrases like 'MD (Radiotherapy)' in page order."""
    text = normalize_quals(text)
    out: list[str] = []
    for m in QUAL_RE.finditer(text):
        token = m.group(1)
        tail = text[m.end():m.end() + 80]
        detail = re.match(r"\s*(\([^)]{2,70}\)|-\s*[A-Z][A-Za-z &]{2,40}(?=,|;|$))", tail)
        out.append(token + (" " + detail.group(1).strip() if detail else ""))
    # Keep ambiguous short tokens only alongside a strong qualification.
    strong = any(q.split()[0] in QUAL_STRONG for q in out)
    if not strong:
        out = [q for q in out if q.split()[0] not in {"MS", "MD", "DA", "MBA", "MSc", "MPH", "MHA", "CCT", "ESMO", "ECMO"}]
    seen: list[str] = []
    for q in out:
        if q not in seen:
            seen.append(q)
    return seen


def qualification_line(text: str) -> bool:
    quals = parse_qualifications(text)
    return len(quals) >= 2 or any(q.split()[0] in QUAL_STRONG for q in quals)


def sub_specialties(text: str) -> list[str]:
    low = text.lower()
    found = []
    for canon, phrases in SUB_SPECIALTIES.items():
        if any(re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low) for p in phrases):
            found.append(canon)
    return found


def split_designation(text: str) -> tuple[str | None, str | None]:
    """'Senior Consultant - Medical Oncology' -> ('Senior Consultant', 'Medical Oncology')."""
    m = DESIGNATION_RE.search(text)
    if not m:
        return None, None
    full = m.group(0).strip(" -–,:")
    q = QUAL_RE.search(normalize_quals(full))
    if q and q.start() > 3:
        full = full[: q.start()].strip(" -–,:")
    parts = re.split(r"\s*(?:[-–:,|]|\bof\b|\bin\b)\s*", full, maxsplit=1)
    role = parts[0].strip()
    rest = parts[1].strip(" -–,") if len(parts) > 1 else None
    if rest and len(rest) < 3:
        rest = None
    return full if len(full) <= 120 else role, rest


def city_from(text: str) -> str | None:
    m = CITY_RE.search(text or "")
    if not m:
        return None
    return CITY_CANON.get(m.group(1), m.group(1))


def split_list(text: str) -> list[str]:
    parts = [p.strip(" .-–") for p in SPLIT_RE.split(text) if p.strip(" .-–")]
    return [p for p in parts if 1 < len(p) <= 200]


def name_from_url(url: str) -> str | None:
    seg = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    seg = re.sub(r"\.(?:html?|php|aspx?)$", "", seg)
    seg = re.sub(r"[-_]\d+$", "", seg)
    m = re.match(r"^(?:dr|doctor|prof)[-_.]+([a-z][a-z\-_.]+)$", seg, re.I)
    if not m:
        return None
    words = [w for w in re.split(r"[-_.]+", m.group(1)) if w]
    if not 1 <= len(words) <= 5 or any(w.lower() in {"oncology", "cancer", "hospital"} for w in words):
        return None
    return "Dr. " + " ".join(w.capitalize() for w in words)


# --------------------------------------------------------------------------
def add_contacts(rec: DoctorRecord, field: str, items: list[str], layer: str, snippet: str, url: str) -> None:
    """Union contact values ('a; b') instead of replacing them, keeping first-seen order."""
    items = [i for i in items if i]
    if not items:
        return
    existing = [x for x in (rec.get(field) or "").split("; ") if x]
    merged = list(dict.fromkeys(existing + items))
    if merged == existing:
        return
    rec.values[field] = "; ".join(merged)
    rec.layers.setdefault(field, layer)
    for item in items:
        if item not in existing:
            rec.evidence.append(Evidence(field, item, layer, (snippet or item)[:500], url))


def assign_emails(rec: DoctorRecord, addrs: list[str], layer: str, snippet: str, url: str,
                  single_doctor: bool = True) -> None:
    """Doctor's own address -> email; desk/department addresses -> contact_email."""
    if not addrs:
        return
    own, other = emails.classify(addrs, rec.get("name"), single_doctor)
    add_contacts(rec, "email", own, layer, snippet, url)
    add_contacts(rec, "contact_email", [o for o in other if o not in (rec.get("email") or "")], layer, snippet, url)


def assign_phones(rec: DoctorRecord, found: list[tuple[str, str]], layer: str, snippet: str, url: str,
                  single_doctor: bool = True) -> None:
    """Doctor's own number -> phone; hospital/desk numbers -> contact_phone."""
    if not found:
        return
    own, desk = phones.classify(found, single_doctor)
    add_contacts(rec, "phone", own, layer, snippet, url)
    add_contacts(rec, "contact_phone", [d for d in desk if d not in (rec.get("phone") or "")], layer, snippet, url)


def find_name_in_text(text: str) -> str | None:
    m = NAME_RE.search(text)
    if not m:
        return None
    name = clean_name(m.group(1))
    if not name:
        return None
    prefix = "Prof. " if re.match(r"\s*Prof", m.group(0), re.I) and not re.search(r"\bDr\b", m.group(0)) else "Dr. "
    return prefix + name


def plain_person_name(text: str) -> str | None:
    """A heading like 'Meenu Walia' with no honorific."""
    text = re.sub(r"\s*[|–-].*$", "", text).strip()
    words = text.split()
    if not 2 <= len(words) <= 5 or len(text) > 50:
        return None
    if any(w.strip(".,") in NAME_STOP for w in words):
        return None
    if not all(re.match(r"^[A-Z][a-zA-Z'.\-]*$", w) for w in words):
        return None
    return "Dr. " + text


def primary_name(lines: list[Line], soup: BeautifulSoup, meta: dict, url: str, structured_names: list[str]) -> tuple[str | None, str, int, str]:
    """Pick the profile's doctor name. Returns (name, evidence, name line index, source)."""
    for i, line in enumerate(lines):
        if line.kind == "h" and line.level == 1:
            n = find_name_in_text(line.text)
            if n and len(line.text) < 120:
                return n, line.text, i, "h1"
    h1 = soup.find("h1")
    if structured_names:
        return structured_names[0], "structured data", _index_of(lines, structured_names[0]), "struct"
    if h1 is None:
        for i, line in enumerate(lines[:40]):
            if line.kind == "h" and line.level <= 3:
                n = find_name_in_text(line.text)
                if n and len(line.text) < 90:
                    return n, line.text, i, "heading"
    for key in ("og:title", "twitter:title", "title"):
        if meta.get(key):
            n = find_name_in_text(meta[key])
            if n:
                return n, meta[key], _index_of(lines, n), "meta"
    if h1:
        n = plain_person_name(h1.get_text(" ", strip=True))
        if n:
            idx = next((i for i, l in enumerate(lines) if l.kind == "h" and l.level == 1), 0)
            context = " ".join(l.text for l in lines[idx: idx + 8])
            if qualification_line(context) or DESIGNATION_RE.search(context):
                return n, h1.get_text(" ", strip=True), idx, "h1plain"
    n = name_from_url(url)
    if n:
        return n, url, _index_of(lines, n), "url"
    return None, "", 0, ""


def _surname(name: str) -> str:
    return name.split()[-1].strip(".").lower()


def _index_of(lines: list[Line], name: str) -> int:
    sur = _surname(name)
    for i, l in enumerate(lines):
        if sur in l.text.lower() and len(l.text) < 120:
            return i
    return 0


# --------------------------------------------------------------------------
def labels_and_sections(rec: DoctorRecord, lines: list[Line], url: str) -> None:
    """Inline 'Label: value' lines and heading/label blocks followed by content."""
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        # Inline 'Label: value'
        if line.kind in ("p", "li", "label") and ":" in line.text[:45]:
            label, value = line.text.split(":", 1)
            f = label_field(label)
            if f and value.strip():
                _assign(rec, f, value.strip(), "labels", line.text, url)
                i += 1
                continue
        # Heading or label element followed by content
        if line.kind in ("h", "label") and len(line.text) <= 60:
            f = label_field(line.text)
            if f == "name":
                f = None
            if f:
                block: list[Line] = []
                j = i + 1
                if line.kind == "label" and j < n and lines[j].kind != "li":
                    # dt/dd, label/value pairs: the value is the next line only.
                    if lines[j].kind not in ("h", "label"):
                        _assign_block(rec, f, [lines[j]], line, url)
                        i = j + 1
                    else:
                        i = j
                    continue
                while j < n:
                    nxt = lines[j]
                    if nxt.kind == "h" and (nxt.level <= line.level or label_field(nxt.text)):
                        break
                    if nxt.kind == "label" and label_field(nxt.text):
                        break
                    block.append(nxt)
                    j += 1
                    if len(block) >= 40:
                        break
                if block:
                    _assign_block(rec, f, block, line, url)
                i = j
                continue
        i += 1


def _assign_block(rec: DoctorRecord, f: str, block: list[Line], head: Line, url: str) -> None:
    layer = "sections" if head.kind == "h" else "labels"
    snippet = head.text + ": " + " | ".join(b.text for b in block[:6])
    if f in LIST_FIELDS:
        items: list[str] = []
        li = [b.text for b in block if b.kind == "li"]
        source = li or [b.text for b in block]
        for t in source:
            if f == "qualifications":
                items.extend(parse_qualifications(t) or ([t] if len(t) < 120 else []))
            elif f == "languages":
                items.extend(m.group(1).title() for m in LANG_RE.finditer(t))
            elif f == "opd_timings":
                found = [m.group(0) for m in OPD_RE.finditer(t)]
                items.extend(found or ([t] if len(t) < 120 else []))
            elif li:
                items.append(t)
            else:
                items.extend(split_list(t) if len(t) < 300 else [t[:300]])
        rec.set(f, items, layer, snippet, url)
    elif f == "about":
        text = " ".join(b.text for b in block if b.kind in ("p", "li"))[:1500]
        rec.set("about", text, layer, snippet, url)
    else:
        _assign(rec, f, block[0].text, layer, snippet, url)


def _assign(rec: DoctorRecord, f: str, value: str, layer: str, snippet: str, url: str) -> None:
    value = value.strip()
    if f == "experience_years":
        v = parse_experience(value) or parse_experience(value + " years experience")
        if v:
            rec.set(f, v, layer, snippet, url)
    elif f == "qualifications":
        rec.set(f, parse_qualifications(value) or [value[:150]], layer, snippet, url)
    elif f == "languages":
        langs = [m.group(1).title() for m in LANG_RE.finditer(value)]
        rec.set(f, langs or split_list(value), layer, snippet, url)
    elif f == "consultation_fee":
        m = re.search(r"[\d,]{2,7}", value)
        if m:
            rec.set(f, m.group(0).replace(",", ""), layer, snippet, url)
    elif f in ("email", "contact_email"):
        assign_emails(rec, emails.find(value), layer, snippet, url)
    elif f in ("phone", "contact_phone"):
        found = [(n, (snippet or "") + " " + ctx) for n, ctx in phones.find(value)]
        if f == "contact_phone":
            add_contacts(rec, "contact_phone", [n for n, _ in found], layer, snippet, url)
        else:
            assign_phones(rec, found, layer, snippet, url)
    elif f == "registration":
        m = REG_RE.search(value) or re.search(r"[A-Z]{0,8}[-/ ]?\d{3,7}(?:[-/ ]\d{1,6})?", value)
        if m:
            rec.set(f, (m.group(1) if m.re is REG_RE else m.group(0)).strip(), layer, snippet, url)
    elif f == "designation":
        role, rest = split_designation(value)
        rec.set(f, role or value[:120], layer, snippet, url)
        if rest:
            rec.set("specialty", rest, layer, snippet, url)
    elif f == "hospital":
        rec.set(f, value[:200], layer, snippet, url)
        c = city_from(value)
        if c:
            rec.set("city", c, layer, snippet, url)
    elif f in LIST_FIELDS:
        rec.set(f, split_list(value), layer, snippet, url)
    else:
        rec.set(f, value[:300], layer, snippet, url)


# --------------------------------------------------------------------------
def patterns(rec: DoctorRecord, lines: list[Line], name_idx: int, main: Tag, url: str, meta: dict, breadcrumbs: list[str]) -> None:
    """Fallback text patterns, focused on the header block just under the name."""
    head = lines[name_idx + 1: name_idx + 14]
    head_text = "\n".join(l.text for l in head)
    full_text = "\n".join(l.text for l in lines)

    # Designation / specialty from the header block
    for l in head:
        if len(l.text) > 160:
            continue
        if "designation" not in rec.values:
            role, rest = split_designation(l.text)
            if role:
                rec.set("designation", role, "patterns", l.text, url)
                if rest:
                    rec.set("specialty", rest, "patterns", l.text, url)
                continue
        if "specialty" not in rec.values:
            subs = sub_specialties(l.text)
            if subs or re.search(r"\b(?:onco\w*|\w+ology|department of [a-z ]+)\b", l.text, re.I):
                if not qualification_line(l.text) and len(l.text) < 90:
                    rec.set("specialty", l.text, "patterns", l.text, url)
                    continue
        if "qualifications" not in rec.values and qualification_line(l.text):
            rec.set("qualifications", parse_qualifications(l.text), "patterns", l.text, url)
            continue
        if "hospital" not in rec.values and HOSPITAL_WORDS.search(l.text) and len(l.text) < 120 and not DESIGNATION_RE.match(l.text):
            rec.set("hospital", l.text, "patterns", l.text, url)
            c = city_from(l.text)
            if c:
                rec.set("city", c, "patterns", l.text, url)

    if "qualifications" not in rec.values:
        for l in lines[: name_idx + 40]:
            if qualification_line(l.text) and len(l.text) < 250:
                rec.set("qualifications", parse_qualifications(l.text), "patterns", l.text, url)
                break

    if "experience_years" not in rec.values:
        for l in lines:
            v = parse_experience(l.text) if re.search(r"experience|exp\b|years", l.text, re.I) else None
            if v:
                rec.set("experience_years", v, "patterns", l.text, url)
                break

    if "consultation_fee" not in rec.values:
        m = FEE_RE.search(full_text) or FEE_RE2.search(full_text)
        if m:
            rec.set("consultation_fee", m.group(1).replace(",", ""), "patterns", m.group(0), url)

    if "registration" not in rec.values:
        m = REG_RE.search(full_text)
        if m:
            rec.set("registration", m.group(1).strip(), "patterns", m.group(0), url)

    if "opd_timings" not in rec.values:
        found = [m.group(0) for m in OPD_RE.finditer(full_text)][:10]
        if found:
            rec.set("opd_timings", found, "patterns", "; ".join(found), url)

    if "languages" not in rec.values:
        for l in lines:
            if re.search(r"language|speaks", l.text, re.I):
                langs = [m.group(1).title() for m in LANG_RE.finditer(l.text)]
                if langs:
                    rec.set("languages", langs, "patterns", l.text, url)
                    break

    # Emails: mailto links and any address written in the page body (footer already removed).
    found = emails.from_links(main) + emails.find(full_text)
    if found:
        assign_emails(rec, list(dict.fromkeys(found)), "patterns", "; ".join(found)[:300], url)
    # Phones: tel:/WhatsApp links and numbers written in the page body (header/footer already removed).
    found_phones = phones.from_links(main) + phones.find(full_text)
    if found_phones:
        seen_nums: set[str] = set()
        unique = [(n, c) for n, c in found_phones if not (n in seen_nums or seen_nums.add(n))]
        assign_phones(rec, unique, "patterns", "; ".join(n for n, _ in unique)[:300], url)

    # Hospital: breadcrumbs or site name as last resort.
    if "hospital" not in rec.values:
        for crumb in breadcrumbs:
            if HOSPITAL_WORDS.search(crumb):
                rec.set("hospital", crumb, "patterns", " > ".join(breadcrumbs), url)
                break
    if "hospital" not in rec.values and meta.get("og:site_name"):
        rec.set("hospital", meta["og:site_name"], "meta", "og:site_name", url)

    if "city" not in rec.values:
        for source in [rec.get("hospital") or "", head_text, " ".join(breadcrumbs), urlsplit(url).path.replace("-", " ").title()]:
            c = city_from(source)
            if c:
                rec.set("city", c, "patterns", source[:200], url)
                break

    if "specialty" not in rec.values:
        for crumb in reversed(breadcrumbs):
            if sub_specialties(crumb) or re.search(r"onco|ology", crumb, re.I):
                rec.set("specialty", crumb, "patterns", " > ".join(breadcrumbs), url)
                break

    # Photo: og:image, or an <img> whose alt mentions the surname.
    if "photo_url" not in rec.values:
        name = rec.get("name") or ""
        sur = _surname(name) if name else ""
        img = None
        if sur:
            img = main.find("img", alt=re.compile(re.escape(sur), re.I))
        if img and (img.get("src") or img.get("data-src")):
            rec.set("photo_url", urljoin(url, img.get("src") or img.get("data-src")), "patterns", str(img)[:200], url)
        elif meta.get("og:image"):
            rec.set("photo_url", urljoin(url, meta["og:image"]), "meta", "og:image", url)

    if "about" not in rec.values:
        sur = _surname(rec.get("name") or "x y")
        for l in lines[name_idx:]:
            if l.kind == "p" and len(l.text) > 120 and (sur in l.text.lower() or re.search(r"\b(?:he|she|dr\.?)\b", l.text, re.I)):
                rec.set("about", l.text[:1500], "patterns", l.text[:300], url)
                break
    if "about" not in rec.values and meta.get("og:description") and len(meta["og:description"]) > 60:
        rec.set("about", meta["og:description"], "meta", "og:description", url)
    elif "about" not in rec.values and meta.get("description") and len(meta["description"]) > 60:
        rec.set("about", meta["description"], "meta", "meta description", url)

    # Experience / qualifications sometimes only live in the meta description.
    desc = meta.get("og:description") or meta.get("description") or ""
    if desc:
        if "experience_years" not in rec.values:
            v = parse_experience(desc)
            if v:
                rec.set("experience_years", v, "meta", desc, url)
        if "qualifications" not in rec.values and qualification_line(desc):
            rec.set("qualifications", parse_qualifications(desc), "meta", desc, url)


def finalize(rec: DoctorRecord, breadcrumbs: list[str], url: str) -> None:
    """Derive sub-specialties and clean up obvious noise."""
    spec = rec.get("specialty")
    if isinstance(spec, list):
        spec = ", ".join(spec)
    if isinstance(spec, str) and "," in spec:
        parts = [p.strip() for p in spec.split(",") if p.strip()]
        # Drop generic parts already contained in a more specific one ('Oncology', 'Radiation Oncology').
        parts = [p for p in parts if not any(p != q and p.lower() in q.lower() for q in parts)]
        spec = ", ".join(dict.fromkeys(parts))
    if spec:
        rec.values["specialty"] = spec
    texts = [str(rec.get(f) or "") for f in ("specialty", "department", "designation")]
    subs = sub_specialties(" | ".join(texts))
    if not subs:
        subs = sub_specialties(" | ".join(breadcrumbs) + " " + urlsplit(url).path.replace("-", " "))
    if not subs:
        exp = rec.get("expertise") or []
        subs = sub_specialties(" | ".join(exp)) if exp else []
    if subs:
        rec.set("sub_specialties", subs, "patterns", " | ".join(t for t in texts if t), url)
    if not rec.get("specialty") and subs:
        rec.set("specialty", subs[0], "patterns", "derived from sub-specialty", url)
    hosp = rec.get("hospital")
    if isinstance(hosp, list):
        rec.values["hospital"] = "; ".join(hosp)
    # A specialty value that is really a qualification line is moved.
    spec = rec.get("specialty") or ""
    if spec and qualification_line(spec) and not sub_specialties(spec):
        rec.set("qualifications", parse_qualifications(spec), "patterns", spec, url)
        rec.values.pop("specialty", None)
        rec.layers.pop("specialty", None)
