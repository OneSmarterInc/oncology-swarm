# V2 verification report

Build date: **19 October 2026**. Linux x86-64, Python **3.12.14**. These are executed development checks, not production certification. The original v1 archive was preserved.

## GitHub import verification

The private GitHub source copy was verified on 19 October 2026 after excluding
`reports/live-pilot-v2.json`, the generated private research output. The
artifact-only test explicitly skips when that file is absent; no application
code changed for the import. The deployment templates and other documentation
remain templates, not evidence of deployment.

- Repository Python suite: **345 passed, 2 skipped, 2 warnings in 28.80s**
  (`reports/github-import-tests.txt`)
- Skips: the original PostgreSQL integration test needs a dedicated disposable
  server; the private pilot-artifact check needs the intentionally excluded file
- JavaScript syntax and offline DOM regression checks: passed
- Dependency consistency (`pip check`): no broken requirements
- Documentation: 55 local links resolve; 35 shell blocks and the backup script
  pass `bash -n`; all 37 gap IDs remain present once
- Publication excludes secrets, databases, caches, generated workbooks, research
  outputs, and GitHub Actions workflows

The earlier 346-pass ZIP result below is retained as release history. It is not
the test count for a GitHub checkout without the private pilot artifact.

## Original ZIP verification

## Passed

| Check | Actual result | Evidence |
| --- | --- | --- |
| Full Python regression from extracted ZIP in the new environment | **346 passed, 1 skipped, 2 warnings in 29.52s** | `reports/pytest-results-v2.txt` |
| Clean dependency installation | New isolated virtual environment installed `requirements-dev.txt` with the pinned constraint file; `pip check` clean | `reports/clean-install-v2.txt` |
| Fresh schema and demo | Alembic head `b592cf2d0101`, no schema drift; two synthetic proposals in review; zero doctors, HTTP reservations and model calls; SQLite integrity/FK checks clean | `reports/fresh-schema-migration-v2.txt` |
| Populated v1 upgrade | Three migration tests passed in the new environment; approved v1 data/provenance retained, old unversioned-schema init rejected, absent SQLite parent directory created | `tests/test_migrations_v2.py`, fresh-schema report |
| Reviewer UI | JavaScript syntax check and offline DOM mocks passed: paging/filtering, lossless forms, checklist, selected bulk review, safe source rendering, navigation and logout | `reports/ui-dom-mock-results-v2.txt` |
| Compilation/dependencies | `compileall -q app tests migrations scripts`; `pip check`: no broken requirements | `reports/dependency-check-v2.txt` |
| Documentation | Local links, fenced shell syntax and backup script checked; all 37 gap IDs present once | `reports/documentation-check-v2.txt` |

The final full suite ran against a freshly extracted release in the clean virtual environment; `reports/extracted-release-tests-v2.txt` retains its output. Only release reports/manifest were updated afterward, with application/test/migration bytes unchanged. The fresh-install smoke also used a separate clean extraction with no pre-existing `data/` directory.

The two test warnings are upstream Starlette/httpx and AnyIO test-client deprecations. They did not cause failures. Updating those dependencies requires another compatibility run.

### Important regression coverage

- **Fetching/discovery:** exact-host/public-address/TLS pinning, redirect/source binding, bounded DNS waits and concurrent resolver slots, robots decisions, streaming compression bombs/truncation, XHTML, shared pacing, bounded candidate/pagination/sitemap traversal, budgets and stale discovery fencing
- **Extraction/evidence:** conservative visible-name fallback, long-page literal selection, extended fields, unsupported facts/orphan evidence, hidden content, contact scope, registration status, invalid model objects and malicious model/source content
- **Optional providers:** OpenAI and Mistral adapters, token/cost uncertainty, malformed outcomes, durable per-call sequence and ambiguous paid-call retry fencing, all using mocked responses
- **Review/lifecycle:** validation-gated approval, concurrent versions, duplicate acknowledgment, correction/withdrawal audit history, quarantine before extraction failure, unchanged refresh skip, rejected/reverted refresh recovery, pending-refresh races, merge/unmerge, outer-first nested split, and source-specific child evidence in exports
- **Scale smoke:** 200 entirely fictional offline profiles pass mocked queue → extraction → programmatic review → approved-only workbook checks. This tests a code path and the indexed lookup; it measures neither real coverage nor human reviewer throughput
- **Independent adversarial tests:** `tests/test_independent_audit.py` covers 14 previously identified edge cases. Findings were fixed before the final suite; this is not a professional penetration test

## Not executed or not established

1. **Real PostgreSQL:** the one skipped test requires a dedicated server through `POSTGRES_TEST_URL`. No PostgreSQL binary/server is installed here. SQLite tests do not prove PostgreSQL migrations, multi-process locks, backup/restore or production behavior
2. **Successful live hospital backend:** six official profile references were recorded; one bounded Max backend probe failed DNS before HTTP and five candidates were not attempted. Zero backend pages, real approvals or live exports. Details: [pilot results](docs/LIVE_PILOT_RESULTS.md). Three minimal provenance-tracked Max factual-excerpt fixtures are reconstructed markup, not captured native HTML; their eight offline tests pass but all lack a complete extracted core record
3. **Live/paid providers:** no OpenAI/Mistral request, API credential, live model-quality/compatibility test, or billing verification. Prices remain unknown unless an operator configures model-specific rates
4. **Real browser/Windows:** headless Chromium was attempted but blocked by the workspace runtime restrictions. DOM mocks are not visual/browser testing. Windows commands were reviewed, not executed
5. **Deployment:** no EC2 instance, account, external database or website was modified; systemd service startup, nginx/TLS, reboot recovery and actual backup restoration were not executed
6. **Acceptance benchmarks:** no 8-of-10 native-page accuracy, 200 reviewed real doctors, 100 human reviews/hour, national coverage, production load test or fresh-data guarantee
7. **Deferred features:** no JS rendering, Brotli, proxy, current NMC/state-registry verification, per-user authentication/SSO, scheduled summary/alerts, or automated refresh scheduler. No legal/compliance certification, publication/outreach permission, or source reuse clearance

## Dependency advisory scan provenance

Requirements are unchanged from the original starter. Its historical dependency advisory report is retained under `reports/v1-baseline/` and clearly labeled. **No fresh advisory-database scan was performed for v2.** Clean installation and `pip check` do not prove absence of vulnerabilities; rerun an up-to-date advisory audit before deployment.

## Release hygiene

The ZIP excludes `.env`, virtual environments, caches/bytecode, databases, keys, logs and generated workbooks. It includes synthetic fixtures, tests, source code, deployment templates, reports and three deliberately minimal public-professional excerpt fixtures with provenance. It contains no generated real-person directory or approved real records. `MANIFEST.sha256` covers shipped files other than itself.

See [the gap matrix](docs/GAP_MATRIX.md): **19 implemented, 12 partial, 6 deferred**. “Implemented” is scoped code/policy with offline evidence, not a statement that all original production exit criteria passed. [Migration instructions](docs/V2_MIGRATION.md) require a backup and a restored-copy rehearsal; the v2 upgrade is forward-only.
