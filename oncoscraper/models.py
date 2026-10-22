"""Data shapes shared by the fetcher, extractor, crawler and exporters."""
from __future__ import annotations

import html

from dataclasses import dataclass, field
from typing import Any

# Fields every doctor record can carry. Order is the export column order.
SCALAR_FIELDS = (
    "name", "designation", "specialty", "department", "hospital", "city",
    "experience_years", "registration", "consultation_fee", "phone", "contact_phone", "email", "contact_email",
    "photo_url", "about",
)
LIST_FIELDS = (
    "qualifications", "sub_specialties", "expertise", "languages",
    "opd_timings", "education", "awards",
)
ALL_FIELDS = SCALAR_FIELDS + LIST_FIELDS

# Higher number wins when two layers disagree on a scalar field.
LAYER_PRIORITY = {
    "template": 100,
    "h1": 95,
    "jsonld": 90,
    "microdata": 85,
    "embedded_json": 80,
    "labels": 70,
    "sections": 65,
    "listing_card": 55,
    "table_row": 55,
    "patterns": 50,
    "meta": 30,
    "url": 10,
}


@dataclass
class Evidence:
    field: str
    value: str
    layer: str
    snippet: str
    source_url: str


@dataclass
class DoctorRecord:
    source_url: str
    profile_url: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    layers: dict[str, str] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    oncology_score: float = 0.0
    oncology_terms: list[str] = field(default_factory=list)
    found_on: list[str] = field(default_factory=list)
    page_type: str = "profile"
    source_hospital: str = ""   # set in batch mode: which hospital list row produced this record

    # -- field helpers -------------------------------------------------
    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def set(self, name: str, value: Any, layer: str, snippet: str = "", source_url: str | None = None) -> bool:
        """Set a field if empty or if ``layer`` outranks the current one.

        List fields are unioned instead of replaced. Returns True if the
        record changed.
        """
        if value is None:
            return False
        if isinstance(value, str):
            if not name.endswith("_url"):  # URLs keep '&region=' etc. exactly
                value = html.unescape(html.unescape(value))  # '&amp;#038;' -> '&'
            value = " ".join(value.split()).strip(" ,;:|-")
            if not value:
                return False
        src = source_url or self.source_url
        if name in LIST_FIELDS:
            items = value if isinstance(value, (list, tuple)) else [value]
            items = [html.unescape(i) if isinstance(i, str) else i for i in items]
            current = list(self.values.get(name, []))
            seen = {i.casefold() for i in current}
            changed = False
            for item in items:
                item = " ".join(str(item).split()).strip(" ,;:|-•·")
                if name == "qualifications" and item:
                    base = item.split()[0].casefold()
                    bases = {c.split()[0].casefold(): idx for idx, c in enumerate(current)}
                    if base in bases:
                        idx = bases[base]
                        # Keep the more detailed form: 'MD (Medicine)' beats 'MD'.
                        if len(item) > len(current[idx]) and " " not in current[idx]:
                            current[idx] = item
                            changed = True
                        continue
                if item and item.casefold() not in seen and len(item) <= 300:
                    current.append(item)
                    seen.add(item.casefold())
                    changed = True
                    self.evidence.append(Evidence(name, item, layer, (snippet or item)[:500], src))
            if changed:
                self.values[name] = current
                self.layers.setdefault(name, layer)
            return changed
        old_layer = self.layers.get(name)
        if old_layer and LAYER_PRIORITY.get(old_layer, 0) >= LAYER_PRIORITY.get(layer, 0):
            return False
        self.values[name] = value
        self.layers[name] = layer
        self.evidence.append(Evidence(name, str(value), layer, (snippet or str(value))[:500], src))
        return True

    def absorb(self, other: "DoctorRecord", fill_only: bool = False) -> None:
        """Merge another record of the same doctor into this one.

        ``fill_only`` keeps every scalar this record already has (used when a
        listing card is merged into a full profile page).
        """
        for name in ALL_FIELDS:
            if name not in other.values:
                continue
            if fill_only and name in SCALAR_FIELDS and self.values.get(name):
                continue
            layer = other.layers.get(name, "patterns")
            snippet = next((e.snippet for e in other.evidence if e.field == name), "")
            self.set(name, other.values[name], layer, snippet, other.source_url)
        if not self.profile_url and other.profile_url:
            self.profile_url = other.profile_url
        self.oncology_score = max(self.oncology_score, other.oncology_score)
        for term in other.oncology_terms:
            if term not in self.oncology_terms:
                self.oncology_terms.append(term)
        for url in [other.source_url, *other.found_on]:
            if url and url not in self.found_on and url != self.source_url:
                self.found_on.append(url)
        if other.page_type == "profile" and self.page_type != "profile":
            self.page_type = "profile"
            self.source_url = other.source_url

    @property
    def is_oncology(self) -> bool:
        return self.oncology_score >= 1.0

    def filled_fields(self) -> list[str]:
        return [f for f in ALL_FIELDS if self.values.get(f)]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.values.get("name")}
        for f in ALL_FIELDS:
            if f != "name":
                out[f] = self.values.get(f)
        out.update({
            "profile_url": self.profile_url,
            "source_url": self.source_url,
            "found_on": self.found_on,
            "is_oncology": self.is_oncology,
            "oncology_score": round(self.oncology_score, 2),
            "oncology_terms": self.oncology_terms,
            "page_type": self.page_type,
            "source_hospital": self.source_hospital,
            "field_sources": dict(self.layers),
        })
        return out

    def to_full_dict(self) -> dict[str, Any]:
        d = self.to_dict()
        d["values"] = dict(self.values)
        d["evidence"] = [e.__dict__ for e in self.evidence]
        return d

    @classmethod
    def from_full_dict(cls, d: dict[str, Any]) -> "DoctorRecord":
        rec = cls(source_url=d.get("source_url") or "", profile_url=d.get("profile_url"))
        rec.values = dict(d.get("values") or {})
        rec.layers = dict(d.get("field_sources") or {})
        rec.evidence = [Evidence(**e) for e in d.get("evidence") or []]
        rec.oncology_score = float(d.get("oncology_score") or 0)
        rec.oncology_terms = list(d.get("oncology_terms") or [])
        rec.found_on = list(d.get("found_on") or [])
        rec.page_type = d.get("page_type") or "profile"
        rec.source_hospital = d.get("source_hospital") or ""
        return rec


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    content_type: str = "text/html"
    rendered: bool = False
    error: str | None = None


@dataclass
class PageResult:
    url: str
    page_type: str  # profile | listing | other | error
    doctors: list[DoctorRecord] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    link_texts: dict[str, str] = field(default_factory=dict)
    profile_links: list[str] = field(default_factory=list)
    pagination_links: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
