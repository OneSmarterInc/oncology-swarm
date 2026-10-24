"""Crawl-level clean-up of contacts.

A phone number or email that appears on the pages of several different doctors
is the hospital's switchboard or appointment desk, not any one doctor's. Those
values are moved from the doctor columns to the hospital/desk columns.
"""
from __future__ import annotations

import re

from .models import DoctorRecord

SHARED_THRESHOLD = 2  # seen on this many different doctors -> hospital/desk contact


def _split(value) -> list[str]:
    return [v for v in (value or "").split("; ") if v]


def _move(r: DoctorRecord, own: str, desk: str, gone: list[str]) -> None:
    keep = [v for v in _split(r.get(own)) if v not in gone]
    if keep:
        r.values[own] = "; ".join(keep)
    else:
        r.values.pop(own, None)
        r.layers.pop(own, None)
    existing = _split(r.get(desk))
    r.values[desk] = "; ".join(dict.fromkeys(existing + gone))
    r.layers.setdefault(desk, "patterns")


def demote_sitewide(records: list[DoctorRecord], value_pages: dict[str, set[str]], extra_pages: int = 2) -> int:
    """A doctor's number or email that the site also shows on pages that are not about that doctor
    (the header, footer, a branch contact list repeated on every page) belongs to the hospital."""
    moved = 0
    for r in records:
        mine = {u for u in [r.source_url, r.profile_url, *r.found_on] if u}
        for own, desk in (("phone", "contact_phone"), ("email", "contact_email")):
            gone = [v for v in _split(r.get(own)) if len(value_pages.get(v, set()) - mine) >= extra_pages]
            if gone:
                _move(r, own, desk, gone)
                moved += len(gone)
    return moved


def demote_shared(records: list[DoctorRecord], threshold: int = SHARED_THRESHOLD) -> int:
    moved = 0
    for own, desk in (("phone", "contact_phone"), ("email", "contact_email")):
        owners = {}
        for r in records:
            name = re.sub(r"^(?:dr|prof)\.?\s*", "", r.get("name", ""), flags=re.I)
            identity = re.sub(r"[^a-z0-9]", "", name.lower()) or r.profile_url or r.source_url
            for v in set(_split(r.get(own))):
                owners.setdefault(v, set()).add(identity)
        counts = {v: len(names) for v, names in owners.items()}
        shared = {v for v, n in counts.items() if n >= threshold}
        if not shared:
            continue
        for r in records:
            values = _split(r.get(own))
            keep = [v for v in values if v not in shared]
            gone = [v for v in values if v in shared]
            if not gone:
                continue
            if keep:
                r.values[own] = "; ".join(keep)
            else:
                r.values.pop(own, None)
                r.layers.pop(own, None)
            existing = _split(r.get(desk))
            r.values[desk] = "; ".join(dict.fromkeys(existing + gone))
            r.layers.setdefault(desk, "patterns")
            moved += len(gone)
    return moved
