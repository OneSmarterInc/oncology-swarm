# Official-hospital pilot results, 19 October 2026

## Result

**The live backend pilot is blocked before its first HTTP request.** Six public
physician-profile candidates were located across Max Healthcare and Fortis
Healthcare. One normal application `SafeFetcher.fetch()` call was attempted; it
failed during DNS resolution. Five candidates were not attempted. Zero hospital
pages were fetched by the backend, zero native HTML fixtures were captured, and
no live records were queued, approved, or exported.

Separate web research returned indexed text for five of the six profile pages;
the sixth was search-visible but its open operation returned an internal error.
This is **not evidence that the application's fetcher works**. It allowed three
small, explicitly reconstructed Max text-excerpt regression fixtures. Their eight
offline tests passed. Native-page extraction accuracy remains unmeasured.

The original release included a machine-readable `reports/live-pilot-v2.json`.
That generated private research output is intentionally omitted from GitHub;
its artifact-only test skips when absent. The documentation below preserves the
recorded limits and outcome.
It records a status for every selected page, exact URLs, observation/attempt
times, available hashes, missing origin metadata, permission notes, and the full
backend exception chain. Missing source hashes are null, never substituted with
an error-message or reconstructed-fixture hash.

## Actual backend attempt

- Time: 2026-10-19 09:19:04 UTC
- URL: <https://www.maxhealthcare.in/doctor/dr-meenu-walia>
- Method: ordinary `app.fetcher.SafeFetcher.fetch(url)` in live mode, after the
  fetcher worker reported its 77 focused tests passing
- Result: `FetchError: DNS resolution failed`
- Underlying error: `gaierror: [Errno -3] Temporary failure in name resolution`
- HTTP requests reserved: 0; elapsed time: 0.001 seconds
- HTTP status, robots status/content, origin retrieval time, body/hash: unavailable
- Fetcher source SHA-256 at the attempt:
  `a6a67f57d386ecf7cc1672dec0d5e95e83c5f0ab3bbf8d6cad677eedda91aead`

The environment advertised a restricted network allowlist that excludes these
hospital domains. The observed error establishes DNS failure, not that the
hospital returned 403, its robots policy disallowed access, or the new fetcher
logic failed on a valid HTTP response. No proxy, alternate DNS, network escalation,
disabled checks, browser fallback, or other transport workaround was used.

The exact host allowlist was `www.maxhealthcare.in`; pacing was 6 requests/minute,
timeout 15 seconds, response cap 2,000,000 bytes, redirect cap 3, user agent
`OncologyResearchStarter/1.0`, and an in-memory cap of 6 request reservations. This
was a bounded transport probe, **not a durable worker-budget or PostgreSQL test**.
The remaining same-host candidates were not retried after the shared DNS blocker
was established.

## Candidate-by-candidate outcomes

| Official profile | Separate web inspection | Application backend | Retained fixture |
| --- | --- | --- | --- |
| [Max: Meenu Walia](https://www.maxhealthcare.in/doctor/dr-meenu-walia) | Indexed page opened; reported crawl age 4 days | Attempted; DNS failed before HTTP | 10-word factual excerpt, reconstructed markup |
| [Max: Atul Sharma](https://www.maxhealthcare.in/doctor/dr-atul-sharma) | Indexed page opened; reported crawl age 3 days | Not attempted; same-host DNS blocker | 10-word factual excerpt, reconstructed markup |
| [Max: Vaibhav Choudhary](https://www.maxhealthcare.in/doctor/dr-vaibhav-choudhary) | Indexed page opened; reported crawl age about a month | Not attempted; same-host DNS blocker | 11-word factual excerpt, reconstructed markup |
| [Fortis: Vedant Kabra](https://www.fortishealthcare.com/doctors/dr-vedant-kabra-8279) | Indexed page opened; reported crawl age about a month | Not attempted; permission-review hold | None |
| [Fortis: Avanish Saklani](https://www.fortishealthcare.com/doctors/dr-avanish-saklani-14121) | Search result available; page open returned Internal Error | Not attempted; permission-review hold | None |
| [Fortis: Jalaj Baxi](https://www.fortishealthcare.com/doctors/dr-jalaj-baxi-oncology-4508) | Indexed page opened; reported crawl age about a month | Not attempted; permission-review hold | None |

Selection was manual research, not a successful application discovery run. Web
observation times are recorded separately from origin retrieval times. Indexed
content may be stale; no current employment or licensure is certified here.

## Hospital/source-master candidates: UNAPPROVED

These two verified institutional source references are **UNAPPROVED**, disabled,
and **must not be automatically imported**. The original JSON report included a separate
`source_candidates` section with null approval dates/reviewers and
`automatic_import: false`.

| Hospital network | Exact official hostname | Verified doctor-list URL | Permission status |
| --- | --- | --- | --- |
| Max Healthcare | `www.maxhealthcare.in` | [Find a Doctor](https://www.maxhealthcare.in/find-a-doctor) | UNAPPROVED; purpose/reuse review needed |
| Fortis Healthcare | `www.fortishealthcare.com` | [Doctors](https://www.fortishealthcare.com/doctors) | UNAPPROVED; held for terms/permission review |

Both entries describe networks, not individual hospitals. A future source-master
review must map branches and cities, inspect robots, establish the allowed
collection/reuse purpose, and record an operator approval. These references do
not expand the runtime allowlist or grant crawl permission. Other institutions
were not verified in this bounded exercise and are not guessed into a seed file.

## Permission and source-quality findings

The [Fortis terms](https://www.fortishealthcare.com/terms-and-conditions), sections
8 and 11, restrict content reproduction and public/commercial reuse. Section 8.3
also restricts availability/performance/functionality monitoring and benchmarking
or competitive access. No broad permission was obtained. Automated Fortis probes
and fixture reuse were held for an operator permission decision once those terms
were found. Only references and the research outcome are retained.

For Max, only minimal public professional factual spans were retained for private
regression work; no broad reuse license or legal clearance is asserted. Reading
public material does not authorize public directory publication, outreach, or
full-page redistribution. No biographies, photographs, contact details, patient
material, appointment actions, fees, or schedules are bundled. Robots access is a
separate question from permission to reuse content, and the backend did not reach
robots.txt in this environment.

Search revealed two official Fortis URLs for the same named physician:
[one profile](https://www.fortishealthcare.com/doctors/dr-vedant-kabra-8279) and
[another profile](https://www.fortishealthcare.com/doctors/dr-vedant-kabra-6783).
Their titles and visible affiliation labels differ. The set selects only one;
no identity merge or current-affiliation decision was made. This is a concrete
reason to retain reviewer checks even when a page is on an official site.

## Real-source excerpt tests

Files are in [`fixtures/real/`](../fixtures/real/README.md), with per-file SHA-256
hashes, original URLs, web-observation timestamps, excerpt line locations,
transformation notes, and a small golden facts file. Each excerpt contains only a
name, one oncology specialty, and one hospital/location. Plain HTML wrappers were
authored locally; these are **not original DOM excerpts** and contain no invented
field labels, schema.org records, or original attributes.

One real heading used a compound professor/doctor honorific. That exposed a
conservative name-fallback gap, which was fixed with literal-evidence tests. The
three excerpt cases now yield supported names, while unlabelled specialties and
hospital lines remain unassigned. All three therefore lack the complete core
name/specialty/hospital outcome. This is an honest parser boundary for these
reconstructed inputs, not a claim about unavailable native page JSON-LD or DOM.

Command actually run at 2026-10-19 09:24:46 UTC:

```sh
/tmp/oncology-release-venv/bin/python -m pytest -q tests/test_real_fixtures.py
```

Result: **8 passed in 0.06s**, exit code 0. These tests check provenance/hashes,
short factual spans, name fallback, literal evidence, and conservative omissions.
They make no network calls. Passing them does not complete the live pilot.

## What remains unverified

- Native source HTML retrieval, robots compatibility, and source response formats
- Complete real-page specialty/hospital extraction and manual correction rate
- Live PostgreSQL queue, leases, source budgets, review, and approved-only export
- Paid LLM provider behavior or cost; no model call or credential was used
- Deployment, site coverage, reviewer throughput, and directory reuse permission

No accuracy percentage, population size, or arbitrary pass threshold is claimed.

## Smallest next verification

1. Resolve the intended directory use and per-source permission position, keeping
   Fortis held until that review permits the planned activity
2. Use an operator-approved environment with permitted, working DNS/egress, then
   repeat the same single-page `SafeFetcher` probe with all checks intact
3. If it succeeds, record the actual origin timestamp/hash, capture only minimal
   permitted original HTML, and keep those captures distinct from reconstructed
   excerpts
4. Follow `LIVE_PILOT.md` with a separate PostgreSQL database and small budgets;
   measure actual core-field support and corrections, review every proposal, and
   verify approved-only export before expanding

Do not rerun network probes merely to regenerate a test report or turn a blocked
run into a nominal pass.
