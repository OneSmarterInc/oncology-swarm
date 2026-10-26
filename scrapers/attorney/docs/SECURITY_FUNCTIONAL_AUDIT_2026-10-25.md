# Attorney Atlas security and software audit — 25 October 2026

> Historical audit baseline. Remediation and current status: [REMEDIATION_2026-10-26.md](REMEDIATION_2026-10-26.md). Baseline reproductions are archived as text; active fixed-behavior tests are in tests/test_audit_regressions.py.

Audited code: branch `feature/attorney-scraper`, production changes through commit `d988ae9cba2dedc993001252bfafbf4ad91ecacd`. Local audit files are not pushed. Production code was not changed during this audit.

## Outcome and scope

18 reproducible findings, including two usability issues, plus two security-hardening risks identified by code review. This is not a guarantee that all defects have been found. Highest priorities are incorrect contact ownership, wrong-run UI/control mismatch, multiple-process job corruption, and crawl navigation loss.

Tested on Linux with temporary SQLite databases, FastAPI TestClient, local Chromium at desktop/mobile sizes, mocked upstream HTTP responses, and deliberately reordered browser responses. No hostile requests were sent to outside websites or private infrastructure. The user's actual Windows database and installation were not available. No fresh live-site crawl, exhaustive fuzzing, dependency-vulnerability database scan, or production-scale load test was performed.

## Reproduced findings

Severity reflects this local, loopback-bound application's use, rather than an internet-facing deployment.

| ID | Severity | Finding and reproduction | Impact | Recommended correction |
|---|---|---|---|---|
| F01 | High | `extract.py`: a home page whose only Attorneys link is inside `<nav>` returns no crawl links. Navigation/header/footer elements are removed before link collection. | Runs may finish after missing the entry point to the attorney directory. | Collect and prioritize safe crawl links before removing presentation elements from biography text. |
| F02 | Medium | `extract.py:phone_values`: `+12125552345` produces no phone; `212-555-2345` does. | Standard compact `tel:`/structured-data phone numbers are lost. | Normalize valid US telephone formats before parsing; retain extensions and fax separation. |
| F03 | Medium | `extract.py:clean_name`: the valid name `Jane Lawyer` produces no person because Lawyer is blacklisted. | Genuine people with surnames matching generic terms disappear. | Use contextual heading/card evidence rather than blanket rejection of surname words. |
| F04 | Medium | `extract.py`: a profile heading plus Person JSON-LD with `name:null` raises AttributeError. | One malformed metadata field causes the engine to mark an otherwise useful page as an error. | Validate metadata types and ignore invalid metadata without discarding valid HTML. |
| F05 | Medium | `extract.py`: Jane's published email inside an `<aside>` is absent from extracted contacts. | Profile contact sidebars are routinely missed. | Identify relevant profile contact blocks before removing sidebar content. |
| F06 | High | `extract.py`: Jane Smith's profile contains a separate John Smith contact section; `john.smith@firm.com` is marked as Jane's individual email. | Exported contacts can target the wrong person. | Require person-specific block/link attribution; surname overlap alone is insufficient. |
| F07 | Medium | `extract.py`: an HTML attorney card and JSON-LD Person share a profile URL; JSON-LD contains the email but the first card wins and the email is lost. | Structured published contacts disappear when multiple representations coexist. | Merge matching candidates with field-level attribution instead of skipping later candidates. |
| F08 | Medium | `engine.py:_site` and `store.py:get`: a finished site's progress share changes from 99% to 1% when `_site` restarts it; observed inside the actual fetcher factory callback. | Resume can briefly move the bar backwards. The previous fix did not cover this transition. | Preserve completed-site state during resume and define retry progress separately. |
| F09 | Medium | `store.py:runs`: create 201 runs; history returns only 200, with no pagination to reach the oldest. The oldest still exists by ID. | Old work becomes inaccessible through run history. | Add paginated history and total count. |
| F10 | High | `store.py:recover`: a second Store on the same database calls recovery while a run is active; the active run becomes interrupted and its fetching page becomes queued and claimable again. | Running a second server/CLI against the same DB can duplicate processing and corrupt job state. | Add an interprocess owner lock/lease; recovery must distinguish a crashed worker from a live owner. |
| F11 | Low | `export.py`: Excel still includes `Review history`, `Review status`, and review wording after removal of the review UI. | Export terminology conflicts with the requested product flow. | Rename automatic classifications and remove obsolete review surfaces from normal exports while preserving legacy audit data separately if needed. |
| F12 | Low | `web.py:local_protection`: GET `/api/health` with malformed `Origin: http://[` returns 500. | Invalid input produces an unhandled server error. No privilege bypass was demonstrated. | Catch URL parsing errors and return a controlled 400/403. |
| F13 | Medium | `models.py:canonical_url`: `http://firm.com:443/path` becomes `http://firm.com/path`; HTTPS with explicit port 80 similarly changes. | The application fetches a different destination from the supplied URL. | Reject scheme/port mismatches or preserve the explicit permitted port. |
| F14 | Medium | `engine.py`: profile with only an external link and depth=0 finishes with `depth_limit`/issues although there are no eligible remaining pages. | False coverage warnings undermine completion status. | Determine depth/frontier truncation after canonicalization, scope filtering and deduplication. |
| F15 | Medium | `engine.py:_run`: two seeds on one host specify New York and Los Angeles; both extracted records inherit New York from the first seed. | Wrong location labels are exported as source-list provenance. | Preserve source-specific metadata or reject conflicting host metadata explicitly. |
| UI01 | Medium | Start from a populated form/upload, navigate away, then click New research run. Prior URL text and validated upload remain. | A subsequent run can unintentionally scrape previous uploaded sources. | Explicit fresh-run reset, or a clearly labeled reuse/duplicate-run action; show all active sources. |
| UI02 | Low | Search the directory, navigate away, then enter a selected run's directory. Search/category/email filters persist while status alone resets. | Valid results can appear missing; filter reset behavior is inconsistent. | Provide a clear reset-all control and visibly indicate active filters; choose consistent navigation defaults. |
| UI03 | High | Delay old run-detail responses; select another run; return new responses first and old responses last. Heading shows Old run while `selectedRun` remains new-run. | Displayed data and Pause/Stop/export targets can refer to different runs. | Capture run ID at request start, discard stale responses, and bind actions to the displayed ID. |

## Security risks: not demonstrated as end-to-end exploits

**S01 — DNS rebinding / SSRF validation gap (potential high impact).** `fetch.py:public_url` resolves and rejects private addresses, but `_raw` subsequently gives HTTPX the original hostname. The approved address is not pinned to the connection. The controlled test confirms this separation using public DNS answers and MockTransport; it does not contact a private address. A controlled hostname could change resolution between validation and connection. Environment proxies (`trust_env=True`) can also resolve independently. Pin validated addresses while preserving Host/TLS verification, or verify the connected peer via a controlled transport; define proxy behavior explicitly. A real exploit would require additional DNS/network/TLS conditions and was not claimed here.

**S02 — Request-body limit enforced after buffering (medium resource-exhaustion risk).** `web.py:local_protection` checks Content-Length, but for an authenticated chunked/missing-length request it calls `await request.body()` before checking its size. Thus 3 MB is not a streaming memory bound. Enforce an incremental receive limit. No memory-exhaustion attack was executed; exposure is reduced by loopback binding and session-token enforcement.

## Additional operational observations requiring follow-up

- Run detail polls all page rows every 2.5 seconds but displays only 100; history repeatedly aggregates each run; directory filtering loads all observations before pagination. Large-database latency/memory thresholds remain unmeasured. Add server-side page-log pagination and benchmark representative batches.
- “Latest 100 pages” is implemented as highest page IDs, not latest completion/update times. Prioritized processing can make this log misleading; verify with a realistic large crawl before redesigning it.
- Review/discovery API routes remain although their UI was removed. This is residual functionality, not evidence of a security bypass.
- Running the CLI from a different directory selects a different relative `results/attorneys.db`, which can look like missing history. Document the path or make it explicit in the UI.
- Browser tests use mocked upstream sites. They validate the application flow, not current extraction accuracy across real firm websites.

## Controls that passed the local probes

Missing mutation token rejected (403); cross-origin token request rejected (403); unexpected Host rejected (400); tested static traversal rejected; SQL-shaped search remained harmless; spreadsheet formula strings escaped; private DNS answers rejected; CSP includes frame restrictions. This is limited positive evidence, not certification of the whole security boundary.

## Evidence and commands

`audit/test_confirmed_findings.py` contains 15 defect reproductions, one DNS transport-gap probe, and one grouped security-controls test. **Passing these tests confirms current defects, not remediation.**

`audit/browser_audit.py` runs the existing browser acceptance flow and reproduces UI01–UI03 with temporary data.

Results: **86 existing tests passed + 17 audit tests passed = 103**; browser acceptance and all three UI reproductions passed. One upstream FastAPI/Starlette TestClient deprecation warning appeared.

```powershell
python -m pytest -q tests audit/test_confirmed_findings.py
python audit/browser_audit.py --chromium <path-to-chromium>
```

Fix priority: UI03/F06/F10/F01 first; S01/S02 hardening next; then extraction completeness, progress/resume and other operational findings. Convert each defect reproduction into an expected-correct regression test when fixing it. No fixes or GitHub push were performed in this audit.
