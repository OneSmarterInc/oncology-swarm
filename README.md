# OncoScraper

Give it a hospital website (home page, find-a-doctor page, department page, or one doctor's profile). It crawls the same site, finds doctor pages, extracts each doctor's details with rules only, keeps the oncologists, and writes an Excel file. No API key and no AI model are needed.

## Setup on Windows (one time)

From this folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Or double-click `setup_windows.bat`. The last line downloads Chromium (about 150 MB). The scraper uses it only when needed: when a site's firewall blocks plain HTTP clients (maxhealthcare.in does), or when a page is built by JavaScript.

## Check the install (2 minutes, no internet needed)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expect `29 passed`.

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
| Site blocks headless browsers | add `--show-browser` (opens a visible Chrome window) |
| Bigger crawl | `--max-pages 500 --depth 5` |
| Site uses an unusual profile URL | `--pattern /consultant/` |

Use `.\.venv\Scripts\python.exe` in place of `python` on Windows. Press Ctrl+C to stop a crawl early; what was found is still saved.

## Crawl many hospitals at once

`sources/india_hospitals.csv` lists official hospital and cancer-centre doctor pages. One command crawls them all, one by one, each with its own page budget:

```powershell
.\.venv\Scripts\python.exe -m oncoscraper batch sources\india_hospitals.csv --out india_oncologists.xlsx
```

| Option | Meaning |
|---|---|
| `--pages-per-site 150` | page budget per hospital (default 150) |
| `--only Apollo` | run only hospitals whose name contains "Apollo" |
| `--no-resume` | re-crawl hospitals that already finished |

Each finished hospital is saved in `india_oncologists_sites\`. If the run stops (Ctrl+C, power cut), run the same command again and it continues from the next hospital. The Excel file gets a **Hospitals** sheet with pages, doctors, oncologists and emails found per hospital. Add your own rows to the CSV; only one URL column is needed.

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
| Real browser (automatic) | the page as Chrome sees it, after JavaScript, scrolling and "load more" | firewalled sites, JavaScript sites |

Oncology is decided from specialty, department, designation and sub-specialty (Medical, Surgical, Radiation, Haemato-Oncology, BMT, Paediatric, Gynae, Head and Neck, Breast, Uro, Neuro, GI, Thoracic, Musculoskeletal and more). A cardiologist whose bio mentions "cardio-oncology" is not counted.

## Output (Excel)

| Sheet | Contents |
|---|---|
| Summary | run settings, page counts, field coverage % |
| Oncologists | one row per oncologist |
| All doctors | every doctor found, any speciality |
| Evidence | each field, which layer found it, the page text, the URL |
| Pages | every URL visited, HTTP status, page type |

Columns: name, designation, specialty, sub-specialties, department, qualifications, experience, hospital, city, languages, expertise, OPD timings, consultation fee, registration, phone, email, contact email, education, awards, about, photo URL, source hospital, profile URL, found-on pages, oncology score.

**Emails.** The scraper collects every email the page publishes in its body: plain text, `mailto:` links, Cloudflare-protected addresses (shown as "[email protected]" in the browser), and written-out forms like `name [at] hospital [dot] com`. It never guesses addresses from name patterns. Each address goes in one of two columns:

| Column | Gets | Example |
|---|---|---|
| Email | the doctor's own address: contains their name, or is the only non-desk address on their page or card | `meenu.walia@hospital.in` |
| Contact Email | desk and department addresses shown on the doctor's page | `appointments@`, `oncology@`, `info@` |

Many hospital profile pages publish no email at all; then both columns stay empty.

Phone numbers are taken only from explicit labels or `tel:` links in the page body. Header and footer contacts (the hospital switchboard) are ignored. Registration numbers are what the hospital page claims; they are not checked against the NMC register.

## Rules it follows

- robots.txt is always read and obeyed (including wildcard rules).
- Only the start site is crawled (`--subdomains` to include sub-sites).
- At least 1.5 s between requests to one site by default, longer if robots.txt asks.
- Booking, login, blog, careers, packages and language-copy pages are skipped.
- Page budget (`--max-pages`, default 200) and depth limit (default 4).

## When a site gives poor results

1. Run `python -m oncoscraper page <one profile URL>` and look at the output.
2. If fields are missing, run the crawl with `--save-html html_dump` and send the saved file for that page. It can be replayed offline with `python -m oncoscraper file html_dump\<file>.html` and a rule or template added.
3. `HTTP 403` on plain HTTP is handled automatically: the scraper switches that site to the real browser. The `page` command prints `Fetched with a real browser` when this happens.
4. If it says `HTTP 403 even in a real browser`, try `--show-browser` (some firewalls block headless browsers). If that still fails, the site blocks automated access; use another official source.
5. If it says `robots.txt disallows`, the hospital has asked crawlers to stay out of that page; the scraper respects it.

## Project layout

```
oncoscraper/
  fetch.py      HTTP, robots.txt, pacing, retries, switch to browser on 401/403/429/503
  robots.py     robots.txt parser with * and $ support
  render.py     real-browser fetching (firewalled and JavaScript sites)
  crawl.py      frontier, sitemaps, link scoring, duplicate merging
  extract.py    page classifier and layer orchestration
  structured.py JSON-LD, microdata, meta tags, embedded app JSON
  annotate.py   page -> heading / label / list / paragraph lines
  profile.py    one-doctor extraction
  listing.py    cards, repeated blocks, faculty tables, pagination
  oncology.py   oncology relevance score
  vocab.py      degrees, designations, cities, languages, labels, URL hints
  templates.py  optional per-site selectors
  emails.py     published, Cloudflare-protected and obfuscated emails; doctor vs desk
  batch.py      many hospitals from a CSV, per-site budget and resume
  export.py     Excel / CSV / JSON
  web.py        local web UI (static/index.html)
tests/          fictional hospital site, firewall simulator, 29 tests
```
