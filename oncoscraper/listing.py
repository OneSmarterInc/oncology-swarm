"""Many-doctor pages: card grids, faculty tables and link lists."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import Tag

from . import emails
from .dom import text_lines
from .fetch import normalize_url
from .models import DoctorRecord
from .profile import (
    assign_emails,     city_from, find_name_in_text, parse_experience, parse_qualifications, plain_person_name,
    qualification_line, split_designation, sub_specialties,
)
from .vocab import HOSPITAL_WORDS, LANG_RE, NAME_RE, PROFILE_URL_HINTS, label_field

NAME_TAGS = ("a", "h2", "h3", "h4", "h5", "h6", "strong", "b", "p", "span", "div", "td", "li", "figcaption")
LINK_TEXT_HINT = re.compile(r"view|profile|know more|read more|details|more info|meet", re.I)
BOOKING = re.compile(r"book|appointment|enquir|call|whatsapp|video|consult-online|teleconsult", re.I)


def _name_nodes(main: Tag) -> list[tuple[Tag, str]]:
    """Smallest elements whose whole text is a doctor's name (optionally with trailing degree)."""
    out: list[tuple[Tag, str]] = []
    for el in main.find_all(NAME_TAGS):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text or len(text) > 90:
            continue
        name = find_name_in_text(text)
        if not name or not re.match(r"^\s*(?:Dr|Prof|Doctor|Professor)\b", text, re.I):
            continue
        # Skip if a child already carries the same name (keep the smallest node).
        if any(isinstance(c, Tag) and find_name_in_text(c.get_text(" ", strip=True) or "") == name for c in el.find_all(NAME_TAGS)):
            continue
        out.append((el, name))
    return out


def _card_for(node: Tag, name: str, all_names: set[str]) -> Tag:
    card = node
    for _ in range(8):
        parent = card.parent
        if parent is None or parent.name in ("body", "html", "main", "[document]"):
            break
        text = parent.get_text(" ", strip=True)
        if len(text) > 1800:
            break
        others = {find_name_in_text(m.group(0)) for m in NAME_RE.finditer(text)} - {None, name}
        if others & all_names:
            break
        card = parent
    return card


def _profile_link(card: Tag, name: str, base_url: str) -> str | None:
    surname = name.split()[-1].lower()
    best = None
    for a in card.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "tel:", "mailto:", "javascript:", "whatsapp:")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        text = a.get_text(" ", strip=True)
        if BOOKING.search(href) or (BOOKING.search(text) and not LINK_TEXT_HINT.search(text)):
            continue
        score = 0
        if surname in absolute.lower() or surname in text.lower():
            score += 3
        if PROFILE_URL_HINTS.search(urlsplit(absolute).path):
            score += 2
        if LINK_TEXT_HINT.search(text):
            score += 1
        if best is None or score > best[0]:
            best = (score, absolute)
    if card.name == "a" and card.get("href"):
        return normalize_url(urljoin(base_url, card["href"]))
    parent_a = card.find_parent("a", href=True)
    if (best is None or best[0] == 0) and parent_a:
        return normalize_url(urljoin(base_url, parent_a["href"]))
    return best[1] if best and best[0] > 0 else None


def leaf_lines(card: Tag) -> list[str]:
    """Texts of innermost elements; splits inline <span>name</span><span>role</span> layouts."""
    out: list[str] = []
    for el in card.find_all(True):
        if el.find(True) is None:
            t = " ".join(el.get_text(" ", strip=True).split())
            if t and (not out or out[-1] != t):
                out.append(t)
    return out


def card_record(card: Tag, name: str, base_url: str) -> DoctorRecord:
    rec = DoctorRecord(source_url=base_url, page_type="listing")
    lines = text_lines(card)
    leaves = leaf_lines(card)
    if len(lines) <= 2 and len(leaves) > len(lines):
        lines = leaves
    snippet = " | ".join(lines)[:500]
    rec.set("name", name, "listing_card", snippet)
    link = _profile_link(card, name, base_url)
    if link:
        rec.profile_url = link
    surname = name.split()[-1].lower()
    for line in lines:
        low = line.lower()
        if surname in low and len(line) < 90 and find_name_in_text(line):
            # The name line may carry degrees: "Dr. A B, MBBS, MD"
            if qualification_line(line):
                rec.set("qualifications", parse_qualifications(line), "listing_card", line)
            continue
        if ":" in line[:40]:
            label, value = line.split(":", 1)
            f = label_field(label)
            if f and value.strip():
                _set_card_field(rec, f, value.strip(), line)
                continue
        if BOOKING.search(line) and len(line) < 40:
            continue
        if "designation" not in rec.values:
            role, rest = split_designation(line)
            if role and len(line) < 160:
                rec.set("designation", role, "listing_card", line)
                if rest:
                    rec.set("specialty", rest, "listing_card", line)
                continue
        if "qualifications" not in rec.values and qualification_line(line):
            rec.set("qualifications", parse_qualifications(line), "listing_card", line)
            continue
        if "experience_years" not in rec.values and re.search(r"year|yrs|exp", line, re.I):
            v = parse_experience(line)
            if v:
                rec.set("experience_years", v, "listing_card", line)
                continue
        if "specialty" not in rec.values and len(line) < 90 and (sub_specialties(line) or re.search(r"\b\w+ology\b|onco", line, re.I)):
            rec.set("specialty", line, "listing_card", line)
            continue
        if "hospital" not in rec.values and HOSPITAL_WORDS.search(line) and len(line) < 120:
            rec.set("hospital", line, "listing_card", line)
            continue
        if re.search(r"language|speaks", low):
            langs = [m.group(1).title() for m in LANG_RE.finditer(line)]
            if langs:
                rec.set("languages", langs, "listing_card", line)
    card_emails = emails.from_links(card) + emails.find(" ".join(lines))
    if card_emails:
        assign_emails(rec, list(dict.fromkeys(card_emails)), "listing_card", snippet, base_url)
    if "city" not in rec.values:
        c = city_from(" ".join(lines))
        if c:
            rec.set("city", c, "listing_card", snippet)
    img = card.find("img")
    if img and (img.get("src") or img.get("data-src")) and not str(img.get("src", "")).startswith("data:"):
        rec.set("photo_url", urljoin(base_url, img.get("data-src") or img.get("src")), "listing_card", str(img)[:200])
    return rec


def _set_card_field(rec: DoctorRecord, f: str, value: str, line: str) -> None:
    if f == "qualifications":
        rec.set(f, parse_qualifications(value) or [value], "listing_card", line)
    elif f == "experience_years":
        v = parse_experience(value) or parse_experience(value + " years experience")
        if v:
            rec.set(f, v, "listing_card", line)
    elif f == "languages":
        rec.set(f, [m.group(1).title() for m in LANG_RE.finditer(value)], "listing_card", line)
    elif f == "designation":
        role, rest = split_designation(value)
        rec.set(f, role or value, "listing_card", line)
        if rest:
            rec.set("specialty", rest, "listing_card", line)
    elif f in ("name", "about"):
        return
    elif f in ("email", "contact_email"):
        assign_emails(rec, emails.find(value), "listing_card", line, rec.source_url)
    else:
        rec.set(f, value[:200], "listing_card", line)


def cards(main: Tag, base_url: str, exclude: set[str] | None = None) -> list[DoctorRecord]:
    nodes = _name_nodes(main)
    names = {n for _, n in nodes}
    exclude = {e.casefold() for e in (exclude or set())}
    out: list[DoctorRecord] = []
    used_cards: list[Tag] = []
    seen: set[str] = set()
    for node, name in nodes:
        if name.casefold() in seen or name.casefold() in exclude:
            continue
        card = _card_for(node, name, names)
        if any(card is c or card in c.descendants for c in used_cards):
            # Another name already owns this region (e.g. name repeated in alt text).
            continue
        used_cards.append(card)
        seen.add(name.casefold())
        out.append(card_record(card, name, base_url))
    return out


MEDICAL_HINT = re.compile(r"\b(?:\w+ology|onco\w*|MBBS|MD|DM|MCh|DNB|consultant|surgeon|physician|professor|director|specialist|hod)\b", re.I)


def repeated_cards(main: Tag, base_url: str) -> list[DoctorRecord]:
    """Card grids whose names carry no 'Dr.' prefix: find repeated sibling blocks."""
    groups: dict[tuple, list[Tag]] = {}
    for el in main.find_all(["div", "li", "article", "section", "a"]):
        cls = tuple(sorted(c for c in (el.get("class") or []) if not re.search(r"\d|active|odd|even|first|last", c)))
        if not cls or el.parent is None:
            continue
        groups.setdefault((id(el.parent), el.name, cls), []).append(el)
    out: list[DoctorRecord] = []
    for members in groups.values():
        if len(members) < 3:
            continue
        good = []
        for m in members:
            text = m.get_text(" ", strip=True)
            if not 15 <= len(text) <= 1500 or not MEDICAL_HINT.search(text):
                continue
            name = None
            for el in m.find_all(["a", "h2", "h3", "h4", "h5", "h6", "strong", "b", "span", "p", "div", "figcaption"]):
                t = " ".join(el.get_text(" ", strip=True).split())
                if t and len(t) <= 50:
                    name = find_name_in_text(t) or plain_person_name(t)
                    if name:
                        break
            if name:
                good.append((m, name))
        if len(good) >= 3 and len(good) >= len(members) * 0.6:
            for m, name in good:
                out.append(card_record(m, name, base_url))
    seen, unique = set(), []
    for r in out:
        k = r.get("name").casefold()
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique


# --------------------------------------------------------------------------
HEADER_FIELDS = {
    "name": "name", "faculty": "name", "doctor": "name", "faculty name": "name", "name of faculty": "name",
    "name of the faculty": "name", "name of doctor": "name", "consultant": "name", "consultants": "name",
}


def tables(main: Tag, base_url: str) -> list[DoctorRecord]:
    """Faculty tables: header row with Name + Designation/Department/Qualification."""
    out: list[DoctorRecord] = []
    for table in main.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        headers = [" ".join(c.get_text(" ", strip=True).lower().split()) for c in header_cells]
        mapping: dict[int, str] = {}
        for idx, h in enumerate(headers):
            h2 = re.sub(r"[^a-z ]", "", h).strip()
            f = HEADER_FIELDS.get(h2) or label_field(h2)
            if f:
                mapping[idx] = f
        if "name" not in mapping.values() or len(mapping) < 2:
            continue
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < len(mapping):
                continue
            rec = DoctorRecord(source_url=base_url, page_type="listing")
            snippet = " | ".join(c.get_text(" ", strip=True) for c in cells)[:500]
            for idx, f in mapping.items():
                if idx >= len(cells):
                    continue
                value = " ".join(cells[idx].get_text(" ", strip=True).split())
                if not value:
                    continue
                if f == "name":
                    name = find_name_in_text(value) or plain_person_name(value)
                    if name:
                        rec.set("name", name, "table_row", snippet)
                        a = cells[idx].find("a", href=True)
                        if a:
                            rec.profile_url = normalize_url(urljoin(base_url, a["href"]))
                elif f == "qualifications":
                    rec.set(f, parse_qualifications(value) or [value], "table_row", snippet)
                elif f == "designation":
                    role, rest = split_designation(value)
                    rec.set(f, role or value, "table_row", snippet)
                    if rest:
                        rec.set("specialty", rest, "table_row", snippet)
                elif f in ("email", "contact_email"):
                    addrs = emails.find(value) + emails.from_links(cells[idx])
                    assign_emails(rec, list(dict.fromkeys(addrs)), "table_row", snippet, base_url)
                elif f in ("languages", "expertise", "opd_timings", "education", "awards", "sub_specialties"):
                    rec.set(f, [v.strip() for v in re.split(r"[,;]", value) if v.strip()], "table_row", snippet)
                else:
                    rec.set(f, value, "table_row", snippet)
            if rec.get("name"):
                # A table caption/heading often names the department.
                heading = table.find_previous(["h1", "h2", "h3", "h4", "caption"])
                if heading and "department" not in rec.values and "specialty" not in rec.values:
                    htext = heading.get_text(" ", strip=True)
                    if sub_specialties(htext) or re.search(r"onco|ology|department", htext, re.I):
                        rec.set("department", htext, "table_row", htext)
                out.append(rec)
    return out


def profile_links(all_links: list[tuple[str, str]]) -> list[str]:
    """Links that look like individual doctor profiles."""
    out = []
    for url, text in all_links:
        path = urlsplit(url).path
        if BOOKING.search(path):
            continue
        if NAME_RE.match(text.strip()) or PROFILE_URL_HINTS.search(path):
            out.append(url)
    return out


def pagination_links(main_soup: Tag, base_url: str) -> list[str]:
    out: list[str] = []
    base_path = urlsplit(base_url).path.rstrip("/")
    for a in main_soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        rel = " ".join(a.get("rel") or [])
        href = normalize_url(urljoin(base_url, a["href"]))
        p = urlsplit(href)
        is_pager = (
            "next" in rel
            or re.fullmatch(r"\d{1,3}|next|next ?›|›|»|>|next page|load more|more", text, re.I) is not None
            or re.search(r"[?&](?:page|p|pg|paged)=\d+|/page/\d+", href, re.I) is not None
        )
        if is_pager and (p.path.rstrip("/").startswith(base_path) or base_path.startswith(p.path.rstrip("/").rsplit("/page", 1)[0])):
            if href != normalize_url(base_url) and href not in out:
                out.append(href)
    for link in main_soup.find_all("link", rel=lambda r: r and "next" in r):
        if link.get("href"):
            out.append(normalize_url(urljoin(base_url, link["href"])))
    return out
