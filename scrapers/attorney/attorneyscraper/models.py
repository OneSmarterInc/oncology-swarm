from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator, model_validator

CATEGORIES = {
    "plaintiff": "Medical malpractice / plaintiff",
    "defense": "Healthcare defense",
    "compliance": "Healthcare regulatory & compliance",
    "fraud": "Healthcare fraud & investigations",
    "transactional": "Corporate & transactional healthcare",
    "nurse": "Nurse attorney",
    "public_health": "Government & public health",
    "bioethics": "Bioethics & healthcare risk",
}


def canonical_url(value: str) -> str:
    p = urlsplit(value.strip())
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError(
            "Use a public http:// or https:// website URL without credentials."
        )
    if p.port not in {None, 443 if p.scheme == "https" else 80}:
        raise ValueError("Only standard web ports are supported.")
    host = p.hostname.lower().encode("idna").decode()
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k not in {"fbclid", "gclid"}
        )
    )
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit((p.scheme, netloc, p.path or "/", query, ""))


def site_key(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.lower().removeprefix("www.")


class Source(BaseModel):
    organization: str = Field(default="", max_length=253)
    url: str = Field(max_length=2000)
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def label_organization(self):
        self.organization = self.organization.strip() or site_key(self.url)
        return self

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        return canonical_url(v)


class RunConfig(BaseModel):
    name: str = Field(default="Attorney discovery", min_length=1, max_length=120)
    sources: list[Source] = Field(min_length=1, max_length=100)
    max_pages: int = Field(default=100, ge=1, le=2000)
    max_depth: int = Field(default=4, ge=0, le=8)
    parallel_sites: int = Field(default=3, ge=1, le=8)
    delay: float = Field(default=1.5, ge=0.5, le=60)
    categories: list[str] = Field(default_factory=lambda: list(CATEGORIES))

    @field_validator("categories")
    @classmethod
    def known_categories(cls, v):
        if not v or set(v) - CATEGORIES.keys():
            raise ValueError("Select one or more of the eight supported categories.")
        return sorted(set(v))

    @model_validator(mode="after")
    def consistent_orgs(self):
        hosts = {}
        for src in self.sources:
            key = site_key(src.url)
            if key in hosts and hosts[key] != src.organization.strip().casefold():
                raise ValueError(
                    "Use one organization name per website; multiple starting URLs are allowed."
                )
            hosts[key] = src.organization.strip().casefold()
        return self


class Review(BaseModel):
    status: str
    note: str = Field(min_length=3, max_length=2000)

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        if v not in {"confirmed", "needs_review", "excluded"}:
            raise ValueError("Invalid review status")
        return v


def person_key(name: str, url: str) -> str:
    # Different profile URLs preserve homonyms instead of merging on name alone.
    return (
        site_key(url)
        + "|"
        + re.sub(r"[^\w]", "", name.casefold())
        + "|"
        + canonical_url(url).rstrip("/")
    )
