# First permitted live-source pilot, v2

The [recorded pilot](LIVE_PILOT_RESULTS.md) did not complete: one ordinary backend probe failed DNS before HTTP, five other selected candidates were not attempted, and zero live records were queued/approved/exported. Three minimal Max factual-excerpt fixtures use locally reconstructed HTML, not native page captures. Fortis is held for permission review. This procedure describes the next verification, not a run already completed.

## 1. Establish source permission and a safe environment

Complete the offline fixture demo first. Follow [SOURCE_POLICY.md](SOURCE_POLICY.md): official public institutional pages, internal research only, individually reviewed purpose/fields/rights. Verify domain ownership and applicable source restrictions; keep unapproved references disabled. Do not seed patient stories, private profiles, or personal contact material. Public access and robots allowance do not grant reuse rights.

Use an operator-approved environment with working, permitted DNS/egress. Do not bypass the observed network restriction with proxies, alternate DNS, disabled TLS/robots, or an unapproved transport. The smallest next test is one normal `SafeFetcher` request with every control intact; stop at a common environmental blocker rather than repeatedly probing the same unavailable host.

## 2. Create a separate pilot database

Use a dedicated PostgreSQL staging database and [Alembic migrations](POSTGRESQL.md), with `DEMO_AUTO_CREATE=0`. Verify PostgreSQL behavior on a disposable test database before relying on it; that real-backend run remains open in this release. Do not mix fixtures and real rows or strip synthetic markers. No real source is automatically approved by migration or by appearing in a report.

Keep the paid model path disabled for the initial source-transport/extraction test. If a later authorized provider trial is desired, read [LLM_ADAPTER.md](LLM_ADAPTER.md) and review data transmission, account/model support, terms/settings, and spending separately.

## 3. Configure small per-run bounds

Edit the private external environment file. These are placeholders, not approved sources:

```text
MODE=live
DEMO_AUTO_CREATE=0
ALLOWED_DOMAINS=profiles.hospital.example
WORKER_COUNT=2
MAX_JOBS=5
MAX_REQUESTS=30
REQUESTS_PER_MINUTE=6
LLM_ENABLED=0
USER_AGENT=OncologyResearchDirectory/2.0
USER_AGENT_CONTACT=
```

Use exact hostnames without schemes, wildcards, paths, or ports. `hospital.example` does not cover `www.hospital.example`. Production profile/discovery fetches stay within the selected source host; do not broaden trust automatically for redirects. An optional contact URL/mailto must be real and authorized, never invented.

Caps persist across restarts for the current numbered run. Thirty request reservations cover fewer than thirty profiles when robots, redirects, failures, and retries count. A DNS-only failure can occur before any request reservation. Resetting budgets needs deliberate reviewer/note, a pause, no queued/retry/leased work and no active discovery; it cannot be used to erase history or bypass in-flight work.

Restart API and worker after configuration changes, and confirm the intended mode, URL, allowlist and caps. systemd loads its external environment file; an arbitrary shell does not load it automatically.

## 4. Add the approved source and test one profile

Use **Approved official sources and discovery** to record the verified institution, city, exact host, list URL, reviewer and actual permission/purpose note. Optional sitemap/path prefix must be scoped to that host. Source creation does not change the configured allowlist and does not approve records.

Check the source entry and start with one hand-checked profile URL through **Queue profile URLs** or `POST /api/jobs`. Inspect each returned item/error. New direct live URL entry requires exactly one active source on the host; when multiple list sources share the hostname, use the chosen source's discovery path or resolve the ambiguity first.

The worker runs separately from the API. Check its logs and actual progress; configured worker count and `/healthz` are not proof of completed fetches.

## 5. Inspect actual transport and source evidence

Record exact URL/final URL, time, status/error, robots outcome, content type/encoding, byte/hash data when available, and request counts. Keep missing origin fields null/unavailable. An error-message hash, search-index observation time, or reconstructed-fixture hash is not an original-page content hash or retrieval time.

Missing robots (404) permits the fetcher's no-rules path; a profile 404 is still a failure. Robots disallow, 403, malformed/unavailable rules, unsafe DNS/TLS, unsupported encoding, or blocked network conditions are not reasons to disable checks. Bounded gzip/deflate and XHTML are supported; JavaScript-only lists and Brotli remain gaps.

Inspect the immutable visible-text snapshot, proposal/errors and duplicate candidates. Deterministic support is intentionally conservative; even a name may be the only supported field. No missing specialty/hospital/contact may be invented to meet a pilot threshold. Capture only specifically permitted minimal native HTML for future regression tests, with real source metadata and transformations recorded; keep it separate from reconstructed excerpts.

## 6. Review a small complete workflow

Check intended physician identity, oncology specialty, city, affiliations, each populated extended field, exact support, contact scope, duplicate identity, and synthetic/source labels. Registration must remain `provider_claimed`. A clean schema is insufficient by itself. Use meaningful reviewer notes and approve only defensible records. Start with individual review; bulk selection is not a way to skip per-record checks.

Export through the UI or CLI. Verify only active approved non-quarantined rows appear, all evidence/source/review links are present, contact scope remains correct, and no formula injection or synthetic contamination appears. Exercise one correction/withdrawal and verify it disappears from a new export. With specific permitted requests, exercise refresh unchanged/changed behavior and reapproval. Do not call it refreshed when fetching failed.

Record any downloaded workbook recipients; withdrawal does not modify old files. Approval/export does not authorize public publication, outreach, or third-party sharing.

## 7. Expand discovery only after the profile path works

Choose the approved source ID, small page/profile limits, and optional sitemap in the UI/API. Traversal shares request budgets, robots, exact-host checks, database pacing, and discovery leases. Read returned profiles, visited pages, errors, `truncated`, and per-job outcomes. Limits are upper bounds, not coverage counts. Link pagination works; JavaScript “load more” is not rendered. Repeat only after diagnosing the actual remaining coverage gap, not because a run produced fewer candidates than expected.

## 8. Record acceptance evidence before scaling

Measure actual native-page core-field support, incorrect/unsupported facts, reviewer corrections, duplicate handling, source load, response sizes, database/restart/restore behavior, review time, and model usage if separately enabled. Identify the sample and denominator; report missing data and failures plainly.

The original targets of 8/10 usable native profiles, 200+ discovered doctors from a chain, and 100 reviews/hour remain unmeasured, not passed. Raising caps supplies no proof of those outcomes. Expand only after source permissions and observed end-to-end results support it. Keep registry verification, per-user access, JS coverage, production deployment and national completeness as explicit unresolved work where applicable.
