# AttorneyScraper · SWARM

Local healthcare-attorney extraction with source evidence, published personal/office contacts, durable runs, and Excel/CSV/JSON export. No API key or AI model is required.

The portal follows OncoScraper and CampusScraper's **before-prompting** UI: top tabs, teal header, cards, coverage tables, run controls and filterable directories. Campus reference: `OneSmarterInc/campus_scrapper`, branch `before-prompting`, commit `34e3bb43973160e04a489012394090bd6a5bfcf7`. Neither other scraper is modified.

## Windows setup and run (commands only)

From the repository root:

```powershell
cd scrapers\attorney
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m attorneyscraper serve
```

Open http://127.0.0.1:8002. After an update, stop the old server with Ctrl+C, install updated requirements, start again and refresh with Ctrl+F5. The existing database is migrated automatically; no results are deleted by the migration.

For an existing virtual environment, do not recreate it—run the last two commands only.

## Portal workflows

- **Dashboard:** latest unique profiles, supported matches, own-email coverage, organization-level contact coverage, recent runs and the active database location.
- **New run:** paste one complete URL per line, or upload CSV/Excel with a URL column. Organization/City/State are optional. Upload preview shows exactly what will be combined with typed URLs. New run clears previous source files and text.
- **Settings:** per-website page budget, link depth, parallel websites, request delay and eight healthcare-law categories. Robots rules and longer crawl delays take precedence.
- **Test one page:** extract a public page without creating a run or saving people. Pause a running crawl before testing a page.
- **Progress:** increasing budget-based percentage, real page counts, errors, current URLs and website stop reasons. Completed websites finish their share. Paused runs retain progress. A retry is a new attempt and can reset its failed website's share.
- **Runs:** paginated history; Open, Excel, Run again, Resume interrupted work and confirmed Delete. Completed websites are not restarted on resume. Retry is available for actual failed pages.
- **Directory:** search name/email/title/location, filter organization/city/practice/classification and own email/phone/any contact. Clear filters, inspect evidence and export precisely the filtered records.
- **Exports:** Summary, Attorneys, Evidence, Pages and Run audit. There is no review queue. Legacy review data remains stored for backward compatibility; the normal workflow requires no manual approval.

An organization inferred from a URL is a domain label, not an independently verified law-firm name. Locations supplied in source rows retain their source-specific provenance. Conflicting location fields are not guessed for discovered pages.

## Data and operational safety

Default database: `results/attorneys.db`, relative to the command's working folder. The Dashboard shows its absolute path. Use `--db D:\path\attorneys.db` to keep an explicit location.

Only one server or CLI crawl may own a database at a time. Another process receives a clear error instead of modifying active work. The OS releases the lock after a crash; stale work is recovered on next startup. Do not remove a `.lock` file while a process is running. Stop the server before copying the database for a manual backup.

The UI binds to `127.0.0.1`; this is not a multi-user internet service. Mutations require a session token; Host/origin checks, CSP, bounded uploads, formula escaping and parameterized SQL are enforced. HTTP connections resolve at connect time and connect only to approved public IPs, keeping the original TLS hostname and Host header. Environment HTTP/SOCKS proxies are deliberately not used because independent proxy DNS could bypass that boundary.

Use **Run again** to refresh old records after extraction fixes. Previously stored evidence cannot be reconstructed from HTML that was never saved; old runs remain available for comparison.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\ui_test.py
```

The browser test uses a real temporary FastAPI/SQLite instance with deterministic upstream responses. It checks desktop/mobile layouts, creation, uploads, filters, exports, pause/resume, race conditions and HTML injection. Playwright is needed for browser testing, not for normal scraping.

```powershell
.\.venv\Scripts\python.exe scripts\load_test.py
.\.venv\Scripts\python.exe scripts\real_pilot.py --out results\real-pilot-new
```

The load test creates temporary synthetic data. The real pilot contacts five public biographies, obeys robots, uses production network protections and exits nonzero if its historical expectations do not match. Website changes can invalidate those expectations. No proxy/DNS bypass adapter is provided.

## Scope and limitations

This release ports shared portal and operational workflows, not every domain-specific capability of the Campus crawler. Campus faculty/student-office fields and Onco doctor specialties do not belong in the attorney directory. Prompt discovery, Groq/LLM workflows, and post-checkpoint features are not included.

The attorney engine reads server-rendered HTML, JSON-LD and sitemaps. JavaScript-only sites, PDF CV enrichment, cross-subdomain crawling, CAPTCHA/login pages and OCR remain unsupported; no controls falsely advertise those capabilities. Active bar membership, mailbox delivery and phone reachability are not verified. A completed run means its configured work ended, not exhaustive discovery of every attorney on the site.

See `docs/REMEDIATION_2026-10-26.md` for the audit closure, verification evidence and remaining validation limits.
