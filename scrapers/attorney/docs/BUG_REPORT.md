# Attorney Atlas QA report — 24 October 2026

Scope: local development build; progress reporting, contact wording, upload handling, existing unit/integration suite and deterministic browser acceptance flow.

## Findings and fixes
| ID | Severity | Finding | Fix / validation |
|---|---|---|---|
| AA-01 | Medium | Fetching pages were absent from visible processed/queued counts. Users could not account for all work. | Added active page URLs and reading counts. State accounting regression tests cover queued, fetching, done, error and skipped. |
| AA-02 | Medium | No starting-URL denominator or progress bar; processed attempts could be confused with successful reads. | Added deduplicated starting URL completion bar, remaining count, success/failure/skipped counts and explanation. Discovered pages do not inflate the starting denominator. |
| AA-03 | Medium | Confirmed/verified wording could be interpreted as email deliverability verification. | Renamed UI classification to Attorney match supported; inspection dialog explicitly explains that delivery, phone reachability and active bar membership are unchecked. |
| AA-04 | Medium | Selecting an invalid file after a valid file retained the old uploaded source list. | Clear uploaded sources before validation. Browser regression confirms old sources are removed. |
| AA-05 | Low | View people first loaded the previous directory scope before fetching the requested run. | Set run scope before navigation, eliminating the extra stale request. |

## Executed checks
- 74 pytest tests passed, including six new progress regression cases.
- Ruff lint passed.
- Browser acceptance passed: actual local FastAPI/SQLite app with deterministic upstream HTML, create/crawl, starting progress bar, classification text, contact explanation, review save/queue, XLSX download, valid-to-invalid upload, desktop and 390px mobile layout, no JavaScript errors.
- Existing integration suite exercises crawl, storage, review, exports, retries/restarts, URL protection and contact attribution.
- Live websites were not re-crawled in this QA pass. Earlier five-profile pilot is documented separately in VALIDATION.md.

## Remaining limitations / risks
- Exact whole-website completion percentage and ETA cannot be established while discovery continues. The new bar explicitly measures starting URLs, not entire-site coverage.
- A finished starting-URL attempt can have failed; separate failed count must be inspected.
- Page/depth limits can leave incomplete coverage. Remaining queued work and stop reasons are shown.
- JavaScript-only pages, PDFs, CAPTCHA and authenticated content are unsupported.
- No SMTP delivery checks, phone calls or current bar-register validation.
- This pass ran on Linux; Windows execution was not tested here.
- One dependency deprecation warning from Starlette TestClient/httpx remains; no test failure.
- Public deployment still requires authentication and network hardening; see SDLC.md.
- No claim that every bug has been found. Concurrent browser response ordering and high-volume performance require further stress testing.

## Run locally without batch files
From the extracted application folder in Windows PowerShell:
    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe -m attorneyscraper serve

For browser tests:
    .\.venv\Scripts\python.exe -m playwright install chromium
    .\.venv\Scripts\python.exe scripts/ui_test.py

Open http://127.0.0.1:8002. The tested changes are prepared for feature/attorney-scraper in oncology-swarm.

## Second QA pass
- Fixed malformed upload payloads and corrupted XLSX files returning server errors; now return actionable HTTP 400 responses.
- Fixed numeric 0 and Boolean FALSE in Excel Enabled cells being treated as enabled.
- Added latest-request guards for upload and directory responses; starting a run during upload validation is blocked with an explanation.
- Moved the read transaction before run-state lookup so status and page counts share one database snapshot.
- Added eight upload regression tests: total 74 tests passed.
- Browser regression passed including out-of-order upload responses, invalid upload reset and the complete existing UI flow.
- Ruff checks passed. Browser upstream responses are simulated; this pass did not repeat live scraping or Windows testing.
