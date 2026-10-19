# OncoScraper

Give it a hospital website (home page, find-a-doctor page, department page, or one doctor's profile). It crawls the same site, finds doctor pages, extracts each doctor's details with rules only, keeps the oncologists, and writes an Excel file. No API key and no AI model are needed.

## Setup on Windows (one time)

From this folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Or double-click `setup_windows.bat`.

Optional, for hospital sites that build their pages with JavaScript (the scraper uses it only when a page comes back empty):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Check the install (2 minutes, no internet needed)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expect `23 passed` (or `22 passed, 1 skipped` without Playwright).

Then try the fictional demo hospital. Window 1:

```powershell
.\.venv\Scripts\python.exe tests\demo_site.py
```

Window 2:

```powershell
.\.venv\Scripts\python.exe -m oncoscraper scrape http://127.0.0.1:8765/ --allow-local --delay 0 --out demo.xlsx
```

Expect `11 oncologists`. Open `demo.xlsx`.

## Use it: web UI

```powershell
.\.venv\Scripts\python.exe -m oncoscraper serve
```

Open http://127.0.0.1:8000/, paste a URL, click **Start crawl**, watch progress, then **Download Excel**. Use **Test one page** first to see what the extractor reads from a single URL.

## Use it: command line

| Goal | Command |
|---|---|
| Crawl a hospital site | `python -m oncoscraper scrape https://www.hospital.in/find-a-doctor --out hospital.xlsx` |
| One page only, print what was found | `python -m oncoscraper page https://www.hospital.in/doctor/dr-name` |
| Re-run extraction on a saved page | `python -m oncoscraper file saved_page.html` |
| Save every fetched page for debugging | add `--save-html html_dump` |
| Keep all specialities, not only oncology | add `--all-specialties` |
| Faster or politer | `--delay 1` (default 1.5 seconds between requests) |
| Bigger crawl | `--max-pages 500 --depth 5` |
| Site uses an unusual profile URL | `--pattern /consultant/` |

Use `.\.venv\Scripts\python.exe` in place of `python` on Windows. Press Ctrl+C to stop a crawl early; what was found is still saved.

## Which URL to give it

Best to worst:

1. The hospital's **find-a-doctor** or **our doctors** page.
2. The **oncology / cancer care department** page (lists only cancer specialists).
3. The **home page** (it will find the doctor pages itself, but uses more of the page budget).
4. A **single doctor profile** (with `--max-pages 1` you get just that doctor).

## How it extracts data (no API, no model)

Each page goes through these layers. When two layers disagree, the higher one wins, and every value keeps the page text it came from (Excel sheet **Evidence**).

| Layer | Reads | Example |
|---|---|---|
| Site template (optional) | CSS selectors you write for one hospital | `templates/www.hospital.in.json` |
| JSON-LD / microdata | schema.org `Physician`, `Person`, `Hospital`, breadcrumbs | `"medicalSpecialty": "Oncologic"` |
| Embedded app data | `__NEXT_DATA__`, `window.__INITIAL_STATE__`, Next.js streams | React/Next sites with empty HTML |
| Labels and sections | `Experience: 20 years`, dt/dd, label/value divs, `<h3>Qualification</h3>` blocks | most hospital profile pages |
| Listing cards and tables | doctor card grids (with or without "Dr."), faculty tables | find-a-doctor and department pages |
| Text patterns | degrees (MBBS, MD, DM, MCh, DNB ...), designation, experience, fees, OPD timings, registration no., languages, city | anything left |
| Browser render (optional) | the page after JavaScript runs | sites that load doctors by XHR |

Oncology is decided from specialty, department, designation and sub-specialty (Medical, Surgical, Radiation, Haemato-Oncology, BMT, Paediatric, Gynae, Head and Neck, Breast, Uro, Neuro, GI, Thoracic, Musculoskeletal and more). A cardiologist whose bio mentions "cardio-oncology" is not counted.

## Output (Excel)

| Sheet | Contents |
|---|---|
| Summary | run settings, page counts, field coverage % |
| Oncologists | one row per oncologist |
| All doctors | every doctor found, any speciality |
| Evidence | each field, which layer found it, the page text, the URL |
| Pages | every URL visited, HTTP status, page type |

Columns: name, designation, specialty, sub-specialties, department, qualifications, experience, hospital, city, languages, expertise, OPD timings, consultation fee, registration, phone, email, education, awards, about, photo URL, profile URL, found-on pages, oncology score.

Phone and email are taken only from explicit labels or `tel:`/`mailto:` links in the page body, never from the site header or footer. Registration numbers are what the hospital page claims; they are not checked against the NMC register.

## Rules it follows

- robots.txt is always read and obeyed (including wildcard rules).
- Only the start site is crawled (`--subdomains` to include sub-sites).
- At least 1.5 s between requests to one site by default, longer if robots.txt asks.
- Booking, login, blog, careers, packages and language-copy pages are skipped.
- Page budget (`--max-pages`, default 200) and depth limit (default 4).

## When a site gives poor results

1. Run `python -m oncoscraper page <one profile URL>` and look at the output.
2. If fields are missing, run the crawl with `--save-html html_dump` and send the saved file for that page. It can be replayed offline with `python -m oncoscraper file html_dump\<file>.html` and a rule or template added.
3. If the page comes back nearly empty, install Playwright (above) so JavaScript pages are rendered.
4. If the log shows `HTTP 403 (bot check page)` or `robots.txt disallows`, that site blocks automated access; use another official source.

## Project layout

```
oncoscraper/
  fetch.py      HTTP, robots.txt, pacing, retries, bot-check fallback
  robots.py     robots.txt parser with * and $ support
  render.py     optional headless Chromium
  crawl.py      frontier, sitemaps, link scoring, duplicate merging
  extract.py    page classifier and layer orchestration
  structured.py JSON-LD, microdata, meta tags, embedded app JSON
  annotate.py   page -> heading / label / list / paragraph lines
  profile.py    one-doctor extraction
  listing.py    cards, repeated blocks, faculty tables, pagination
  oncology.py   oncology relevance score
  vocab.py      degrees, designations, cities, languages, labels, URL hints
  templates.py  optional per-site selectors
  export.py     Excel / CSV / JSON
  web.py        local web UI (static/index.html)
tests/          fictional hospital site + 23 tests
```
