# Attorney Atlas — Healthcare Attorney Scraper

A separate local Python platform for researching US healthcare attorneys. Enter official website URLs or upload a CSV/XLSX source list. The platform discovers biographies, extracts published professional contacts, classifies eight healthcare-law categories, saves evidence, and exports a reviewable directory.

This is version 0.1.0, a working local release. It follows the familiar OncoScraper/CampusScraper workflow but has its own package, database, tests, and port. It does not change either existing project. No AI model or paid search API is required.

## SWARM integration

This module lives at `scrapers/attorney/` on `feature/attorney-scraper` in `OneSmarterInc/oncology-swarm`. Run setup and test commands from this module folder. The existing root oncology application has its own environment. See the repository-root `SWARM.md` for the collection plan.

## Windows setup

Extract the complete folder to `D:\attorney-scraper`. Double-click `setup_windows.bat` (Python 3.12 required), then `run_ui.bat`.

Or use PowerShell:

```powershell
cd D:\attorney-scraper
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m attorneyscraper serve --port 8002
```

Open **http://127.0.0.1:8002**. OncoScraper can remain on port 8000 and CampusScraper on 8001.

## First real-data run

1. Open **New research run**.
2. Upload `examples/real-profile-pilot.csv`.
3. Set link depth to **0** and pages per website to **10**. This processes only the five supplied profiles and the necessary robots files.
4. Start the run. Open the directory and inspect each person's evidence.
5. The pilot expects four attorneys and one excluded paralegal. The five expected names and contact checks are in `scripts/real_pilot.py`. Websites can change after this release.
6. Download Excel from the run for people, contact evidence, page outcomes, and review history.

The broader research workflow uses depth 4 and a 100-page budget per website. Begin with attorney or healthcare practice directories rather than a generic home page for better coverage.

## Portal

| Screen | Function |
|---|---|
| Overview | Counts, category distribution, recent runs |
| New research run | Paste organization/URL pairs; validate CSV/XLSX sources; choose categories, page budget, depth, delay and parallel websites |
| Starting-website search | Builds search-engine links for a category and location. You choose official sources to crawl. It does not automatically scrape search results. |
| Run history | Persistent runs with per-site progress, queued/processed pages, errors and stop reasons |
| Run detail | Pause, stop, resume remaining pages, retry errors, inspect page log, download a partial snapshot |
| Attorney directory | Search and filter people, category, review status, and individual email availability; paginated results; CSV/XLSX/JSON exports |
| Review queue | Inspect published evidence and record a confirmation, exclusion, or review-needed decision with an audit note |

## Categories and inclusion

1. **Plaintiff medical malpractice:** attorney evidence plus representation of patients, families, victims or plaintiffs in medical negligence cases.
2. **Healthcare defense:** attorney evidence plus defense of providers or hospitals in medical liability matters.
3. **Regulatory/compliance:** attorney evidence plus healthcare regulatory work, HIPAA, Stark or Anti-Kickback matters.
4. **Fraud/investigations:** attorney evidence plus healthcare context and fraud, False Claims Act, qui tam or OIG matters. General non-healthcare fraud is insufficient.
5. **Transactional healthcare:** attorney evidence plus healthcare mergers, acquisitions, contracts or transactions.
6. **Nurse attorney:** attorney evidence plus an explicitly stated nursing background or RN credential. A nursing background does not establish a current nursing license.
7. **Government/public health:** attorney evidence plus an explicitly stated public-health-law or health-agency legal role.
8. **Bioethics/risk:** attorney evidence plus healthcare context and clinical ethics, consent, patient rights, end-of-life or clinical risk work.

People can have several category tags. Nonlawyer staff such as paralegals are retained as excluded candidates so exclusions can be audited; filter to Confirmed when exporting a qualified list. A practice category is not a bar certification. Current license status, current nursing registration, historical versus current practice, and ambiguous descriptions may require human review.

Firm-wide practice descriptions are not automatically assigned to every person on a listing page. A person with insufficient individual evidence enters the review queue. Selecting categories restricts the confirmed target set; people with only other categories are marked outside scope.

## Contact quality

- Extracts visible, mailto, `[at]/[dot]`, Cloudflare-obfuscated and Person JSON-LD emails.
- Keeps individual, organization, support-staff and unassigned contacts separate.
- Names in already-published email addresses help attribute ownership; email addresses are never generated from name patterns.
- Organization schema and header/footer contacts are office contacts. A shared contact across multiple people is demoted to organization ownership.
- Direct-number labels and Person telephone properties support individual ownership. Unlabeled numbers remain unassigned. US numbers and extensions are normalized; fax numbers are excluded when labeled.
- Each contact includes source URL, supporting text and observation time. Published does not mean mailbox delivery was tested.
- Exact names at different profile URLs remain separate to avoid merging different people. Name variations or URL aliases may still need manual review.
- City/state supplied in a source list remain labeled as source-list information, not independently verified person locations. Country is not inferred automatically.

## Crawl behavior and durability

The default uses three websites concurrently and one request at a time per website. Same-host sources share a queue and budget. Starting URLs take priority. Same-site links, pagination links and declared sitemaps provide discovery. News, login, careers and binary-document URLs are skipped during discovery.

The crawler checks robots.txt, honors its delay, bounds response size and redirects, and retries transient failures. Unavailable or malformed robots responses stop that fetch conservatively. HTTP 404/410 robots files mean no rules; other errors remain visible.

SQLite commits a finished page, extracted people and new links in one transaction. Pause/stop interrupts waits, retains completed pages and returns an interrupted fetch to the queue. On server restart, active runs become Interrupted. Resume continues pending pages; Retry requeues failed pages. A new run refreshes already completed pages. Closing the application does not erase history.

**Completed** means the configured discovery queue was exhausted. **Completed with issues** includes errors or page/depth/frontier limits. Neither status guarantees whole-site coverage. Depth 0 intentionally limits the pilot to supplied profiles. Retry does not increase a configured page limit; create a new run with a larger budget when needed.

## Data and backup

The local database is `results/attorneys.db` (override with `--db` or `ATTORNEY_DB`). Stop the application before copying the database for a simple backup. While the app is running, use SQLite's backup API rather than copying only the `.db` file. Database, exports and secrets are ignored by git. Generated pilot reports and contact exports stay local and are not committed to SWARM.

## Testing

A clean Linux virtual environment also passed all tests and `pip check`. `requirements-lock.txt` records the resolved versions. To reproduce them, add `-c requirements-lock.txt` when installing `requirements-dev.txt`; Windows may add platform-specific dependencies.

```powershell
# Unit, integration, regression and parallelism checks
.\.venv\Scripts\python.exe -m pytest -q

# Browser acceptance: one-time browser install, then the actual UI/API flow
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\ui_test.py

# Opt-in real network pilot (robots rules still apply)
.\.venv\Scripts\python.exe scripts\real_pilot.py
```

CI definitions are included for Python 3.11/3.12 on Linux/Windows and a browser job. These workflows have not run on GitHub until the project is placed in a repository and pushed. Local Linux execution does not establish Windows execution; run setup/tests on your machine before accepting the release there.

## Known limits and deployment boundary

- Server-rendered HTML and JSON-LD are supported. Browser-only JavaScript rendering, CAPTCHAs, login walls, PDFs and vCards are not implemented in this release. Empty JS shells and HTTP blocks are reported; they are not counted as successful extraction.
- It discovers people inside supplied websites. It does not automatically enumerate all US law firms or harvest search engines/social networks.
- Rule-based classification is conservative and cannot reliably resolve every historical role, legal specialty or unusual layout. The five-profile live pilot covers three categories and two firms; synthetic tests cover all eight categories. Do not extrapolate that small pilot to nationwide accuracy.
- No SMTP deliverability probe, bar-registry verification, CRM integration or email sending is implemented.
- This is a single-user, loopback-only tool. It deliberately binds to `127.0.0.1`, not a public interface. It includes origin/host checks, session tokens, output escaping, and private-host checks. Public hosting would require authentication, authorization, a network egress policy, and operational hardening. Do not expose this local server through an internet tunnel.
- Pause/stop may wait for an in-flight network timeout (up to the configured client timeout); retry/pacing waits are immediately interruptible.

## Developer handoff

See `docs/SDLC.md` for requirements, architecture, acceptance checks, risk decisions and release process, and `docs/VALIDATION.md` for the actual execution evidence and remaining checks. The implementation is new code informed by the existing scraper handoffs and CampusScraper README inspected at commit `58d139aeb0904f0427e281b2d0f7d5521c7a7096`.
