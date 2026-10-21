"""Crawl-level clean-up of contacts.

A phone number or email that appears on the pages of several different doctors
is the hospital's switchboard or appointment desk, not any one doctor's. Those
values are moved from the doctor columns to the hospital/desk columns.
"""
from __future__ import annotations

from collections import Counter

from .models import DoctorRecord

SHARED_THRESHOLD = 3  # seen on this many different doctors -> hospital/desk contact


def _split(value) -> list[str]:
    return [v for v in (value or "").split("; ") if v]


def demote_shared(records: list[DoctorRecord], threshold: int = SHARED_THRESHOLD) -> int:
    moved = 0
    for own, desk in (("phone", "contact_phone"), ("email", "contact_email")):
        counts = Counter(v for r in records for v in set(_split(r.get(own))))
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
