"""Prompt to a run, using the shared seed sources.

The portal already takes a list of seed URLs and crawls them well. The only
thing missing was a way to get that list without a person assembling it by
hand, which is the same gap the campus and onco portals have. That work lives
in the shared `seedsources` package, so this module is thin on purpose: parse,
ask the chain, turn seeds into the Source rows the existing RunConfig expects,
and start the run that already works.

Two endpoints rather than one, because seeing the list before committing to a
crawl is the difference between a wasted hour and a corrected prompt:

    POST /api/prompt/preview   what would be crawled, and where each URL came from
    POST /api/prompt/run       the same, then start the normal run

Seeds that could not be resolved to a URL are reported, never dropped silently
and never given a guessed address.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .models import RunConfig, Source

# The shared package sits at the repository root, beside this module's package.
for candidate in (
    Path(__file__).resolve().parents[1],
    Path(__file__).resolve().parents[3],
):
    if (candidate / "seedsources").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=20, ge=1, le=200)
    registry_db: str | None = None
    use_searxng: bool = False
    resolve_websites: bool = True
    delay: float = Field(default=3.0, ge=0.0, le=30.0)
    # Crawl settings, passed straight through to the run that already exists.
    max_pages: int = Field(default=20, ge=1, le=2000)
    max_depth: int = Field(default=2, ge=0, le=8)
    parallel_sites: int = Field(default=2, ge=1, le=8)
    crawl_delay: float = Field(default=2.0, ge=0.5, le=60)


def _plan(request: PromptRequest) -> dict:
    """Run the seed chain and shape the answer for the portal."""
    try:
        from seedsources import default_chain, parse
    except ImportError as error:
        raise HTTPException(
            500,
            "The shared seedsources package is not importable. Unzip it into the "
            f"repository root beside attorneyscraper ({error}).",
        ) from error

    intent, unparsed = parse(request.prompt)
    chain = default_chain(
        registry_db=request.registry_db,
        searxng="http://127.0.0.1:8080/search" if request.use_searxng else None,
        delay=request.delay,
    )
    if not request.resolve_websites:
        for source in chain.sources:
            if hasattr(source, "resolve"):
                source.resolve = lambda blanks, taken=None: None

    seeds, trail = chain.find(intent, request.limit)

    return {
        "understood": {
            "vertical": intent.vertical,
            "location": intent.location,
            "discipline": intent.discipline,
            "target": intent.target,
            "count": intent.count,
            "summary": intent.describe(),
        },
        "unparsed": unparsed,
        "sources_tried": [
            {"source": r.source, "ok": r.ok, "seeds": len(r.seeds), "error": r.error}
            for r in trail
        ],
        "seeds": [
            {
                "name": s.name,
                "url": s.url,
                "source": s.source,
                "confidence": s.confidence,
                "location": s.location,
                "note": s.note,
                "rank": s.extra.get("rank"),
            }
            for s in seeds
        ],
        "crawlable": sum(1 for s in seeds if s.crawlable),
        "unresolved": [
            {"name": s.name, "reason": s.note} for s in seeds if not s.crawlable
        ],
    }


def register(app, store, manager) -> None:
    """Attach the prompt endpoints to an existing portal."""

    @app.post("/api/prompt/preview")
    def preview(request: PromptRequest):
        return _plan(request)

    @app.post("/api/prompt/run", status_code=201)
    def run(request: PromptRequest):
        plan = _plan(request)
        crawlable = [s for s in plan["seeds"] if s["url"]]
        if not crawlable:
            raise HTTPException(
                422,
                "No seed produced a URL. "
                + (
                    plan["sources_tried"][-1]["error"]
                    if plan["sources_tried"] and not plan["sources_tried"][-1]["ok"]
                    else "Widen the prompt or supply a list."
                ),
            )

        # One Source per seed. The run's own validator rejects two different
        # organisation names on one host, so sibling seeds that resolved to a
        # shared domain are collapsed to the first claimant here as well.
        seen_hosts: dict[str, str] = {}
        sources = []
        for seed in crawlable:
            try:
                candidate = Source(
                    organization=seed["name"][:253],
                    url=seed["url"],
                    city=(seed["location"] or "")[:100],
                )
            except Exception:
                continue  # a malformed url is dropped, not guessed at
            from .models import site_key

            host = site_key(candidate.url)
            if host in seen_hosts:
                continue
            seen_hosts[host] = candidate.organization
            sources.append(candidate)

        if not sources:
            raise HTTPException(422, "Every resolved URL was rejected as unusable.")

        config = RunConfig(
            name=f"Prompt: {request.prompt[:95]}",
            sources=sources,
            max_pages=request.max_pages,
            max_depth=request.max_depth,
            parallel_sites=request.parallel_sites,
            delay=request.crawl_delay,
        )
        with manager.lock:
            if manager.thread and manager.thread.is_alive():
                raise HTTPException(409, "A run is already active")
            rid = store.create(config)
            manager.start(rid)

        record = store.get(rid)
        record["prompt_plan"] = plan
        return record
