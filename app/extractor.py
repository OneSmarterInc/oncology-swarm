"""Conservative deterministic proposals, never instructions or licensing decisions.

Explicitly labeled fields, one schema.org Physician, and guarded name headings are considered.
Structured facts must also occur in visible text. Every accepted claim has a literal
visible support excerpt; unclassified is the default contact scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import re
import unicodedata
from typing import Any

from .fetcher import FetchResult, _VOID, hidden_element


@dataclass(eq=False)
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)
    parent: Any = None
    hidden: bool = False
    depth: int = 0

    def text(self, raw: bool = False) -> str:
        if self.hidden and not raw:
            return ""
        return " ".join(" ".join(
            child.text(raw) if isinstance(child, _Node) else child for child in self.children
        ).split())


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.current = self.root
        self.nodes: list[_Node] = []

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 10000 or self.current.depth >= 128:
            raise ValueError("HTML structural limit exceeded")
        attributes = dict(attrs)
        node = _Node(tag, attributes, parent=self.current,
                     hidden=self.current.hidden or hidden_element(tag, attributes), depth=self.current.depth + 1)
        self.current.children.append(node)
        self.nodes.append(node)
        if tag not in _VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


@dataclass(frozen=True)
class _Fact:
    value: str
    support: str
    label: str = ""


_LABELS = {
    "name": "name", "doctor name": "name", "physician name": "name",
    "specialty": "specialty", "medical specialty": "specialty", "speciality": "specialty",
    "city": "city", "practice city": "city", "hospital": "affiliation",
    "affiliation": "affiliation", "hospital affiliation": "affiliation", "institution": "affiliation",
    "email": "email", "contact email": "email", "phone": "phone", "telephone": "phone",
    "doctor email": "doctor_email", "doctor professional email": "doctor_email",
    "doctor phone": "doctor_phone", "doctor professional phone": "doctor_phone",
    "central email": "central_email", "hospital central email": "central_email",
    "institution central email": "central_email", "central phone": "central_phone",
    "hospital central phone": "central_phone", "institution central phone": "central_phone",
    "registration number": "registration_number", "provider-claimed registration number": "registration_number",
    "registration authority": "registration_authority", "provider-claimed registration authority": "registration_authority",
    "qualification": "qualifications", "qualifications": "qualifications", "education": "qualifications",
    "experience": "experience_years", "years of experience": "experience_years", "experience years": "experience_years",
    "sub specialty": "sub_specialty", "sub-specialty": "sub_specialty", "subspecialty": "sub_specialty",
    "sub speciality": "sub_specialty", "sub-speciality": "sub_specialty",
    "languages": "languages", "languages spoken": "languages",
    "opd timings": "opd_timings", "opd hours": "opd_timings", "consultation timings": "opd_timings",
    "consultation fee": "consultation_fee", "consultation fees": "consultation_fee",
    "profile photo url": "profile_photo_url", "photo url": "profile_photo_url",
}
_FIELDS = set(_LABELS.values())
DOCTOR_FIELDS = ("name", "specialty", "city", "qualifications", "experience_years",
                 "sub_specialty", "languages", "opd_timings", "consultation_fee", "profile_photo_url")
_VALUE_LIMITS = {"name": 300, "specialty": 300, "city": 300, "experience_years": 300,
                 "consultation_fee": 300, "qualifications": 1000, "opd_timings": 1000,
                 "profile_photo_url": 2048, "registration_number": 300, "registration_authority": 300}


def _heading_name(text: str) -> str | None:
    """Require a doctor honorific and at least two name tokens, never directory headings."""
    pieces = re.split(r"\s+(?:\||[-–—])\s+|\s*\|\s*", text, maxsplit=1)
    candidate = pieces[0].strip()
    if len(pieces) > 1 and re.search(r'\b(?:dr\.?|doctor|prof\.?)\s+\S+', pieces[1], re.I):
        return None  # Do not turn a multi-doctor heading into the first person's profile.
    match = re.fullmatch(r"(?:(?:Prof(?:essor)?\.?)\s*\(?\s*(?:Dr\.?|Doctor)\)?|Dr\.?|Doctor|Prof\.?)\s+(.+)", candidate, re.I)
    if not match or len(candidate) > 300:
        return None
    tokens = match.group(1).split()
    if not 2 <= len(tokens) <= 7 or any(not token[0].isalpha() or not all(
            c.isalpha() or unicodedata.category(c).startswith('M') or c in ".'’-" for c in token) for token in tokens):
        return None
    excluded = {"doctor", "doctors", "oncology", "oncologist", "hospital", "hospitals", "best", "profile", "book", "appointment"}
    return candidate if not any(token.casefold().strip('.') in excluded for token in tokens) else None


def _label(text: str) -> str:
    return " ".join(text.strip().rstrip(":").lower().replace("_", " ").split())


def _plain(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def extract(result: FetchResult) -> dict:
    proposal = {
        "schema_version": 1, "synthetic": result.synthetic,
        "doctor": {key: "" if key == "name" else None for key in DOCTOR_FIELDS},
        "affiliations": [], "contacts": [], "registration": [], "evidence": [], "warnings": [],
    }
    warnings: list[str] = proposal["warnings"]
    facts: dict[str, list[_Fact]] = {}
    tree = _Tree()
    try:
        tree.feed(result.html)
        tree.close()
    except (ValueError, RecursionError):
        warnings.append("Malformed or deeply nested HTML could not be extracted safely.")
        return proposal

    def add(key: str, value: str | None, support: str, label: str = ""):
        value_limit = _VALUE_LIMITS.get(key, 500)
        if not value or len(value) > value_limit or len(support) > 4000:
            return
        if support not in result.text or value not in support:
            return
        fact = _Fact(value, support, label)
        if not any(existing.value == value for existing in facts.get(key, [])):
            facts.setdefault(key, []).append(fact)

    for node in tree.nodes:
        if node.hidden:
            continue
        try:
            text = node.text()
        except RecursionError:
            warnings.append("A deeply nested HTML field was skipped.")
            continue
        explicit = (node.attrs.get("data-field") or "").strip().lower().replace("-", "_")
        if explicit in _FIELDS:
            label, sep, value = text.partition(":")
            known_label = _LABELS.get(_label(label))
            if sep and known_label == explicit:
                add(explicit, value.strip(), text, label)
            else:
                # A data attribute can identify a fact, but cannot grant contact scope.
                neutral_key = "email" if explicit.endswith("_email") else "phone" if explicit.endswith("_phone") else explicit
                add(neutral_key, text, text)
        leaf_field = node.tag in {"div", "span"} and not any(
            isinstance(child, _Node) and child.tag in {"div", "p", "li", "dl", "table", "section"}
            for child in node.children)
        if node.tag in {"p", "li"} or leaf_field:
            label, sep, value = text.partition(":")
            key = _LABELS.get(_label(label))
            if sep and key:
                add(key, value.strip(), text, label)
        if node.tag == "dt" and node.parent:
            siblings = node.parent.children
            index = siblings.index(node)
            following = next((s for s in siblings[index + 1:] if isinstance(s, _Node)), None)
            if following and following.tag == "dd":
                key = _LABELS.get(_label(text))
                support = text + " " + following.text()
                if key:
                    add(key, following.text(), support, text)
        if node.tag == "tr":
            cells = [c for c in node.children if isinstance(c, _Node) and c.tag in {"th", "td"}]
            if len(cells) == 2:
                label = cells[0].text()
                key = _LABELS.get(_label(label))
                if key:
                    add(key, cells[1].text(), text, label)

    # JSON-LD is parsed as data only; URLs, instructions and @context are never executed.
    physicians: list[dict] = []
    for node in tree.nodes:
        if node.tag != "script" or (node.attrs.get("type") or "").lower() != "application/ld+json":
            continue
        try:
            document = json.loads(node.text(raw=True))
        except (ValueError, RecursionError):
            warnings.append("Malformed JSON-LD was ignored.")
            continue
        pending = [document]
        visited = 0
        while pending and visited < 1000:
            item = pending.pop()
            visited += 1
            if isinstance(item, list):
                pending.extend(reversed(item))
            elif isinstance(item, dict):
                types = item.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if isinstance(types, list) and any(t in {"Physician", "https://schema.org/Physician", "http://schema.org/Physician"}
                                                   for t in types if isinstance(t, str)):
                    physicians.append(item)
                if "@graph" in item:
                    pending.append(item["@graph"])

    def visible_support(value: str | None) -> str | None:
        if not value or value not in result.text:
            return None
        # Prefer a short literal visible element over the whole document.
        candidates = []
        for node in tree.nodes:
            if node.hidden or node.tag not in {"p", "dd", "li", "h1", "h2", "span", "td", "address", "a"}:
                continue
            try:
                text = node.text()
            except RecursionError:
                continue
            if value in text and text in result.text and len(text) <= 1500:
                candidates.append(text)
        return min(candidates, key=len) if candidates else value

    if len(physicians) == 1:
        physician = physicians[0]
        structured = [("name", _plain(physician.get("name"))),
                      ("specialty", _plain(physician.get("medicalSpecialty"))),
                      ("email", _plain(physician.get("email"))),
                      ("phone", _plain(physician.get("telephone"))),
                      ("languages", _plain(physician.get("knowsLanguage"))),
                      ("profile_photo_url", _plain(physician.get("image")))]
        address = physician.get("address")
        if isinstance(address, dict):
            structured.append(("city", _plain(address.get("addressLocality"))))
        for relationship in ("hospitalAffiliation", "affiliation"):
            affiliations = physician.get(relationship, [])
            affiliations = affiliations if isinstance(affiliations, list) else [affiliations]
            for affiliation in affiliations:
                if isinstance(affiliation, dict):
                    structured.append(("affiliation", _plain(affiliation.get("name"))))
        for key, value in structured:
            support = visible_support(value)
            if support:
                add(key, value, support)
            elif value:
                warnings.append(f"JSON-LD {key} was skipped because it lacks visible support.")
    elif len(physicians) > 1:
        warnings.append("Multiple Physician records were found; structured records were not merged.")
        # A directory page is not a single-doctor profile, even if it also has labels.
        return proposal

    if not facts.get("name"):
        # Titles/metadata can only point to names that independently occur in visible
        # snapshot text. Hidden-only metadata never supplies evidence.
        candidates: dict[str, str] = {}
        for node in tree.nodes:
            if node.tag == "h1" and not node.hidden:
                value = _heading_name(node.text())
            elif node.tag == "title":
                value = _heading_name(node.text(raw=True))
            elif node.tag == "meta" and (node.attrs.get("property") or "").lower() == "og:title":
                value = _heading_name(node.attrs.get("content") or "")
            else:
                continue
            if value and value in result.text:
                candidates[value] = visible_support(value) or value
        # Also reject an h1 list even when only one of its names has an honorific.
        visible_headings = [node for node in tree.nodes if node.tag == "h1" and not node.hidden and node.text()]
        if len(candidates) == 1 and len(visible_headings) <= 1:
            value, support = next(iter(candidates.items()))
            add("name", value, support)
            warnings.append("Doctor name proposed from a heading/title; reviewer must confirm profile identity.")

    if len(facts.get("name", [])) != 1:
        warnings.append("A single explicitly supported doctor name was not found; no profile facts were proposed.")
        return proposal

    def evidence(path: str, value: str, fact: _Fact):
        proposal["evidence"].append({
            "id": f"e{len(proposal['evidence']) + 1}", "field": path, "value": value,
            "source_url": result.url, "support_text": fact.support, "retrieved_at": result.retrieved_at,
        })

    for key in DOCTOR_FIELDS:
        candidates = facts.get(key, [])
        if len(candidates) == 1:
            fact = candidates[0]
            proposal["doctor"][key] = fact.value
            evidence(f"doctor.{key}", fact.value, fact)
        elif candidates:
            warnings.append(f"Conflicting {key} values were omitted.")

    for fact in facts.get("affiliation", [])[:20]:
        index = len(proposal["affiliations"])
        proposal["affiliations"].append({"name": fact.value})
        evidence(f"affiliations.{index}.name", fact.value, fact)

    seen_contacts: set[tuple[str, str]] = set()
    # Explicitly labeled scopes precede unclassified duplicates.
    for key in ("doctor_email", "doctor_phone", "central_email", "central_phone", "email", "phone"):
        contact_type = "email" if key.endswith("email") else "phone"
        scope = "doctor" if key.startswith("doctor_") else "institution_central" if key.startswith("central_") else "unclassified"
        for fact in facts.get(key, [])[:20]:
            if len(proposal["contacts"]) >= 50:
                break
            value = fact.value
            if contact_type == "email" and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
                warnings.append("An invalid or multi-value email field was omitted.")
                continue
            if contact_type == "phone" and (not re.fullmatch(r"[+()\d .\-xX]{5,60}", value) or not 7 <= sum(c.isdigit() for c in value) <= 15):
                warnings.append("An invalid or multi-value phone field was omitted.")
                continue
            if (contact_type, value) in seen_contacts:
                continue
            seen_contacts.add((contact_type, value))
            explicit_keys = {k for k in (f"doctor_{contact_type}", f"central_{contact_type}")
                             if any(f.value == value for f in facts.get(k, []))}
            actual_scope = scope
            if len(explicit_keys) > 1:
                actual_scope = "unclassified"
                warnings.append("A contact with conflicting doctor/central labels was left unclassified.")
            index = len(proposal["contacts"])
            proposal["contacts"].append({"type": contact_type, "value": value, "scope": actual_scope})
            evidence(f"contacts.{index}.value", value, fact)
            evidence(f"contacts.{index}.type", contact_type, fact)
            if actual_scope != "unclassified":
                evidence(f"contacts.{index}.scope", actual_scope, fact)

    authorities = facts.get("registration_authority", [])
    for fact in facts.get("registration_number", [])[:20]:
        index = len(proposal["registration"])
        record = {"number": fact.value, "authority": None, "status": "provider_claimed"}
        proposal["registration"].append(record)
        evidence(f"registration.{index}.number", fact.value, fact)
        evidence(f"registration.{index}.status", "provider_claimed", fact)
        if len(authorities) == 1 and len(facts.get("registration_number", [])) == 1:
            record["authority"] = authorities[0].value
            evidence(f"registration.{index}.authority", authorities[0].value, authorities[0])
    if proposal["registration"]:
        warnings.append("Registration is provider-claimed; current licensure was not independently verified.")
    if not proposal["contacts"]:
        warnings.append("No explicitly supported contact details were found.")
    if result.synthetic:
        warnings.insert(0, "SYNTHETIC fixture data: not a real doctor, institution, contact, or registration.")
    proposal["warnings"] = list(dict.fromkeys(warnings))
    return proposal
