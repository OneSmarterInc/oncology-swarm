# Validation record — release 0.1.0

Validation performed 24 October 2026. This report distinguishes executed checks from supplied future checks.

## Executed checks

| Layer | Result | What was exercised |
|---|---|---|
| Clean virtual environment | Passed | Installed declared dependencies from scratch; pip check found no broken requirements; all 60 tests passed again |
| Python unit/integration/regression suite | 60 passed | All eight categories, negative controls, names, ownership, obfuscation, robots, redirects, private IPs, size limits, lifecycle, SQLite, filtering, review and exports |
| Ruff lint | Passed | Import ordering, unused imports/variables and configured Python error/style checks |
| Python compilation | Passed | All application modules |
| JavaScript syntax | Passed | Portal script checked with Node |
| Browser acceptance | Passed | Actual UI and API with deterministic HTTP transport: create run, complete crawl, open directory, inspect evidence, save review, review queue, Excel download, desktop, mobile and no JS errors |
| Visual inspection | Passed | Desktop 1440 px and mobile 390 px; screenshots inspected locally and not included as deliverables |
| Live official-source pilot | Passed | Five biographies across two firms; four confirmed attorneys and one excluded paralegal; five published emails; support attribution and fax exclusion |
| Parallelism check | Passed | Three controlled independent sites return the same record counts, with parallel elapsed time below 85% of serial elapsed time |

JUnit reports are generated locally by the test commands and are not committed. The resolved Linux dependency snapshot is `requirements-lock.txt`. The test runner emitted a dependency deprecation warning about Starlette's future HTTP client direction; it did not fail tests. No warning is represented as a clean vulnerability audit.

## Real-data pilot

Run ID: `a9d9cb54ca49`. Elapsed: **10.30 seconds** for the configured five-profile pilot, including robots checks. Settings: two parallel sites, depth 0, 1.5-second request gap, ten-page budget per site. The explicit depth limit is visible in run/page outcomes; this was not a whole-firm crawl.

| Person | Expected outcome | Published individual email | Observed result |
|---|---|---|---|
| Juliane C. Miller | Healthcare defense attorney | jcmiller@hccw.com | Found; category and email matched |
| Eli Fuchsberg | Plaintiff medical malpractice attorney | e.fuchsberg@fuchsberg.com | Found; category and email matched |
| Kathleen Kettles | Nurse attorney | k.kettles@fuchsberg.com | Found; category and email matched |
| Emma Klein | Plaintiff medical malpractice attorney | e.klein@fuchsberg.com | Found; category and email matched |
| Jacqueline Cabrera | Paralegal, exclude from confirmed attorneys | j.cabrera@fuchsberg.com | Found and excluded correctly |

The expected values are encoded in `scripts/real_pilot.py`, with official URLs in `examples/real-profile-pilot.csv`. Run `scripts/real_pilot.py` to generate reports and contacts in `results/real-pilot/`. Research outputs are not committed.

Juliane Miller's assistant and paralegal emails were classified as support contacts, not her individual contacts. The Fuchsberg firm switchboard was classified as an organization number. The published fax was excluded from phone output. No unexpected names appeared in this five-page sample.

The network environment routes external DNS through a SOCKS proxy. The pilot therefore used its explicit two-domain remote-DNS test adapter; normal product requests still use public-address validation. Browser acceptance used an installed Chromium 153 executable because the default Playwright browser download returned an invalid archive in this environment. These adaptations are test-environment details, not product configuration changes.

## Real-site failures observed during development

Some other attempted official profiles returned HTTP 403 or JavaScript-only shells. Those attempts were not included in the passing five-profile sample and do not establish successful extraction on those firms. They motivated explicit error messages. The delivered application reports these limitations instead of silently marking an empty result as complete extraction.

## Limits of the evidence

- The live pilot covers **two firms and three of eight category types**. All eight are covered by synthetic classifier tests, which are a different kind of evidence.
- These are record-level checks against a defined sample, not a statistically representative accuracy study. General recall and precision have not been established.
- Emails were verified as published on the profile; SMTP delivery and current bar registration were not tested.
- Browser tests use deterministic external HTML through the real backend. Live network pilot tests use the real remote sources through the real job manager. The two layers complement each other.
- Windows execution and GitHub Actions execution remain pending. Their scripts/configurations are included. Linux test success does not prove Windows success.
- Production multi-user deployment, penetration testing, dependency vulnerability auditing, disaster recovery drills and large-scale load testing are not claimed.

## Acceptance on the user's machine

Run `setup_windows.bat`, confirm the automated test result, start `run_ui.bat`, and run the five-profile CSV with depth 0. Compare against the table above. Review any changed/blocked sources, then increase scope using official attorney directories and the normal crawl settings. Preserve page outcomes and review ambiguous records before exporting a confirmed-only contact list.
