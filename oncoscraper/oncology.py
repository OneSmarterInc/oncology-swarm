"""Oncology relevance scoring for a doctor record."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .models import DoctorRecord
from .vocab import ONCOLOGY_TERMS

ONCO_SUBS = {
    "Medical Oncology", "Surgical Oncology", "Radiation Oncology", "Haemato-Oncology", "Bone Marrow Transplant",
    "Paediatric Oncology", "Gynaecologic Oncology", "Head and Neck Oncology", "Breast Oncology", "Uro-Oncology",
    "Neuro-Oncology", "Musculoskeletal Oncology", "Thoracic Oncology", "GI Oncology", "Thoracic/Lung Cancer Surgery",
    "Onco-Pathology", "Psycho-Oncology", "Interventional Oncology",
}
# Field weights: header fields decide; free text only nudges.
WEIGHTS = {"specialty": 1.0, "department": 1.0, "designation": 1.0, "sub_specialties": 1.0,
           "expertise": 0.5, "about": 0.35, "qualifications": 0.5}


def _terms(text: str) -> list[tuple[str, float]]:
    low = text.lower()
    out = []
    for term, w in ONCOLOGY_TERMS.items():
        if re.search(r"(?<![a-z])" + re.escape(term), low):
            out.append((term, w))
    return out


def score(rec: DoctorRecord, page_context: str = "") -> None:
    total = 0.0
    terms: list[str] = []
    for f, weight in WEIGHTS.items():
        v = rec.get(f)
        if not v:
            continue
        text = " | ".join(v) if isinstance(v, list) else str(v)
        if f == "sub_specialties":
            hits = [s for s in v if s in ONCO_SUBS]
            if hits:
                total += 2.0
                terms.extend(hits)
            continue
        found = _terms(text)
        if found:
            total += weight * max(w for _, w in found)
            terms.extend(t for t, _ in found)
    # Profile URL and the page/department the doctor was listed on.
    url_text = " ".join(urlsplit(u).path for u in [rec.profile_url or "", rec.source_url] if u)
    if re.search(r"onco|cancer|radiation|haemato|hemato|bmt", url_text, re.I):
        total += 0.6
        terms.append("url")
    if page_context:
        found = _terms(page_context)
        if found:
            total += 0.5 * max(w for _, w in found)
            terms.append("listed on oncology page")
    rec.oncology_score = max(rec.oncology_score, total)
    for t in terms:
        if t not in rec.oncology_terms:
            rec.oncology_terms.append(t)
