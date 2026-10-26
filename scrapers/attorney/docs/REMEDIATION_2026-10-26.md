# AttorneyScraper remediation and portal alignment

Date: 26 October 2026 (Asia/Calcutta). Release prepared for feature/attorney-scraper.

## Scope and references

Only the Attorney module is changed. Campus `before-prompting` and `legacy/pre-prompting` both resolve to **34e3bb43973160e04a489012394090bd6a5bfcf7**, message “Fix faculty contact extraction, directory discovery and stale resume results.” The CSS reference was read from that immutable checkpoint. Onco `onco-scraper` uses the same layout and palette. No prompting/discovery prototype or newer Campus workflow was imported.

Shared UI: top navigation, matching teal/header/card/table/form styles, responsive light/dark layout, Dashboard coverage, batch source preview, URL entry, run progress, paginated history, run-again/resume/delete, single-page extraction test, directory filters, evidence inspection and filtered exports. The module stays independent of Campus/Onco data models.

This is shared **portal/workflow parity**, not complete crawler-engine equivalence. Browser-rendered extraction, PDF CV enrichment and cross-subdomain crawling from Campus have not been ported. No unsupported settings are displayed. These are explicit remaining capabilities, not fixed audit defects.

## Audit closure

All 18 reported findings and both hardening risks have corresponding implementation changes. This does not mean every possible bug has been found.

| Findings | Change | Verification |
|---|---|---|
| F01 | Collect crawl links before removing navigation from biography content. | Navigation-only directory link regression; fixture crawl integration. |
| F02 | Accept compact US telephone formats. | Compact +1 number extraction and browser evidence display. |
| F03 | Do not reject a valid surname solely because it is also a generic legal word. | Jane Lawyer regression. |
| F04 | Validate malformed Person metadata without losing valid HTML. | Null-name JSON-LD regression. |
| F05 | Include profile contact sidebars, excluding identified other people. | Sidebar mailbox regression. |
| F06 | Remove other identified person's contact blocks; use whole normalized mailbox patterns rather than surname substrings. | John/Jane Smith attribution regression; middle-initial support and generic section preservation. |
| F07 | Combine matching HTML-card and Person structured metadata. | Card/schema email regression. |
| F08 | Preserve finished-site states and skip finished websites during resume; reset only sites with errors on retry. | Resume and retry regressions; actual browser worker pause/resume. |
| F09 | Add paginated history with total count. | 201-run regression and browser pagination. |
| F10 | OS-backed database owner lock, held through application/worker lifecycle; recovery takes same lock. | Independent subprocess blocked while owned, succeeds after release. Linux tested; Windows code uses msvcrt and awaits native Windows execution. |
| F11 | Classification terminology; Run audit export in place of Review history. | Workbook integration test. |
| F12 | Reject malformed origins with controlled 403. | Malformed-origin regression. |
| F13 | Reject HTTP/HTTPS scheme/port mismatches. | URL validation regression. |
| F14 | Check eligible, scoped, deduplicated links before declaring depth/frontier truncation. | External-link-only depth=0 finishes cleanly. |
| F15 | Use matching seed metadata; don't guess conflicting locations for discovered pages. | New York / Los Angeles seeds regression. |
| UI01 | Fresh New run clears source text, file and pending upload generations; preview/removal controls make loaded sources visible. | Browser fresh-run, invalid upload and late response tests. |
| UI02 | Explicit directory entry resets filters; Clear filters and selected-run scope remain visible. | Browser stale-filter and contact-filter checks. |
| UI03 | Request generation + run/view identity guard; controls capture displayed run ID. | Browser deliberately returns responses out of order; stale snapshot ignored. |
| S01 | Connect-time resolver rejects nonpublic/multicast addresses; numeric IP connection, original TLS SNI/Host, environment proxy disabled. | Private-address/rebinding probes, numeric connection test, actual HTTPcore TLS-name/Host handling with a simulated stream. |
| S02 | ASGI receive limit stops reading as soon as body crosses threshold. | Oversized chunked stream and valid replay tests. |

Additional fixes found while testing: mobile file-control overlap; generic section headings incorrectly treated as another person during initial remediation; bounded recent-page log sorted by update time; idle record-cache invalidation and defensive copies; explicit database location; robots wildcard patterns hardened against repeated-wildcard backtracking.

## Verification

- **123 tests passed**, zero failures/errors/skips, including baseline, audit regressions and new security/API tests. Ruff, JavaScript syntax and whitespace checks pass. Machine-readable results: `validation-2026-10-26.json`.
- Browser test exercises the real local API/database with mocked public websites. Desktop 1440px, tablet 768px, mobile 390px; light/dark screenshot inspection. URL-only creation, upload preview, fresh-run reset, stale uploads, test-page no-save behavior, contacts, CSV/Excel/JSON downloads, pause/resume, stale-run responses, inert HTML, pagination and delete are checked.
- Synthetic load: 10,000 people and 10,001 pages. Run snapshot 0.013s; bounded page log 0.011s; history 0.009s; directory first read 0.621s and cached read 0.287s; summary 0.392s; full Excel export 12.077s. Hardware/environment-specific measurements, not service-level guarantees.
- Five real-profile production-network crawl attempted: all five failed with **DNS resolution failed** in this environment. No source HTML was retrieved. Therefore fresh live extraction accuracy is **not verified**. Existing historical real-pilot results are not reused as proof of this version.
- Native Windows execution and remote GitHub CI have not run for these local changes. Cross-platform locking code and Windows subprocess tests are included for the existing CI matrix.
- FastAPI/Starlette TestClient emits one upstream deprecation warning. Tests pass without changing application HTTP behavior.

## Data migration and release notes

The migration adds a `pages.updated_at` column and index, preserving existing runs and evidence. Completed-site resume behavior prevents reprocessing. Older run records retain their original extraction results; use Run again to regenerate with corrected extraction. No user database was supplied or modified.

Use Python 3.11/3.12. Install updated requirements (`httpcore` is now explicitly pinned). Stop the server before updating/restarting. No Docker, model key or external search service is needed.

Historical failing-behavior audit scripts are archived as text under `audit/`; active regression tests now assert corrected behavior. Release validation is local; remote CI status must be checked separately.
