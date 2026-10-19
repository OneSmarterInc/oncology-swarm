# Version 2 changes

19 October 2026. The original starter archive is preserved separately. This release changes code, schema, API response shapes, reviewer workflows and documentation; read the migration guide before running it against an existing database.

## Main additions

- Explicitly approved institutional source/hospital master; bounded same-host directory, pagination and sitemap discovery
- Robots-404 handling, bounded gzip/deflate and XHTML support, identifying user-agent contact option, shared host pacing, and bounded OS DNS waits
- Evidence-backed qualifications, experience, sub-specialty, languages, OPD timing, fee and public photo-URL fields; conservative name fallback and boilerplate reduction
- Optional OpenAI/Mistral adapters with per-call usage, safe unknown-price accounting and retained invalid-object proposals; no live provider request during development
- Indexed name deduplication; filter/search/page controls, evidence-linked forms, explicit reviewer checklist and bounded bulk review
- Audited correction, withdrawal, source refresh with pre-extraction change quarantine, merge/unmerge and source-specific provenance through export
- Numbered budget runs, discovery leases and stale-worker fencing, paid-call retry protection, metrics endpoint/CLI/UI
- Forward-only Alembic migration with a populated-v1 regression test and backup/rehearsal/recovery instructions

## Compatibility changes

- `/api/jobs` returns a paginated object, not the original bare array. Update clients using the API guide
- Live sources need both an exact allowed hostname and a human-approved source record. No institution is pre-approved or silently enabled
- Per-run reset is explicit and requires a paused, drained state; restarting does not clear durable counters
- Existing v1 databases must be backed up and migrated. Never use `create_all` or stamp `head` to skip the upgrade
- Approved data can be quarantined after a changed refresh and excluded from export until review. Source changes are captured even when later extraction fails

## Verification and scope

Use [the test report](TEST_REPORT.md) for executed checks and [the 37-issue matrix](docs/GAP_MATRIX.md) for implemented, partial and deferred status. A synthetic 200-record regression is included; it is not evidence of 200 real doctors or a measured human review rate.

The live backend probe failed DNS before any HTTP request. Native-page accuracy, real PostgreSQL, paid providers, browser rendering, server deployment, registry checks, per-user identity and production load/freshness behavior remain unverified or deferred. Real-site collection needs a working permitted environment and source-specific permission review. No real directory, reviewed real-person database, generated workbook, secret or deployment credential is bundled.
