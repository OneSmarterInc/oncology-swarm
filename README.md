# India oncology research directory, v2

A bounded, evidence-first pipeline for researching public professional information on official hospital sites, reviewing it, and exporting approved records to Excel. The default synthetic demo runs without an API key or live website access after dependencies are installed.

**This is a research application, not a completed doctor directory, a clinical tool, or a verified registration service.** Real-source compatibility, deployment, and directory-scale throughput are not established by its offline tests. The [actual live pilot](docs/LIVE_PILOT_RESULTS.md) stopped at DNS: zero backend hospital pages fetched, no real rows approved or exported, and no paid model calls.

## Start here

- New local installation: [Windows PowerShell quickstart](docs/WINDOWS_QUICKSTART.md), or the commands below
- Existing v1 database: [v2 migration and recovery](docs/V2_MIGRATION.md) **before starting v2 against it**
- Implemented versus open work: [issue-by-issue gap matrix](docs/GAP_MATRIX.md)
- Source/use decision: [official-only internal research policy](docs/SOURCE_POLICY.md)
- Real-source trial: [live pilot procedure](docs/LIVE_PILOT.md) and [recorded results](docs/LIVE_PILOT_RESULTS.md)
- Administration: [API](docs/API.md), [operations/backups](docs/OPERATIONS.md), [review/security](docs/SECURITY_AND_REVIEW.md)
- Server templates: [EC2 Ubuntu](docs/EC2_UBUNTU.md), [PostgreSQL](docs/POSTGRESQL.md)

## What v2 implements

1. Manually approved source/hospital master, exact-host allowlist, bounded same-host oncology profile discovery, link pagination, and optional sitemap traversal
2. Safe HTTPS fetching with robots checks, public-address DNS pinning, bounded redirects, gzip/deflate decoding, XHTML support, response/time limits, and database-backed host pacing
3. Deterministic extraction plus optional OpenAI/Mistral structured proposals, both disabled until explicitly configured; extended fields retain source evidence
4. Indexed normalized-name duplicate checks; paginated/filterable queue; evidence-linked field forms and explicit human bulk approval of at most 25 clean, duplicate-free proposals
5. Audited correction, withdrawal, merge/unmerge, and manual source refresh with change quarantine and reapproval
6. Per-run durable request/job/model-call caps, safe audited resets, token usage/cost-uncertainty metrics, and approved-only Excel export

Discovery finds candidates, not verified doctors. Unknown fields remain blank. Official hosting does not prove current licensure, affiliation, lawful reuse, or contact scope. Source approval is a separate human decision from record approval.

## Fresh local demo (Python 3.12)

Run from the repository root (or an extracted release directory). Do not use this fresh-install sequence to upgrade an existing database.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m app.cli token
# Set ADMIN_TOKEN in .env to that private generated value.
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.cli demo
.venv/bin/python -m app.cli run-once
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`, enter the token, inspect each fictional proposal and its evidence, then approve only after review. In another shell:

```sh
.venv/bin/python -m app.cli export --output approved_directory.xlsx
```

`python -m app.cli metrics` prints the current private run/usage metrics without making model calls.

The API does not start the worker. `run-once` processes currently available work and exits; delayed retries may remain. `python -m app.cli worker` runs continuously. Use one coordinator process for the SQLite demo and the initial server rollout. Each coordinator has 2–4 worker threads (default 3). Database pacing is implemented, but distributed PostgreSQL operation has not been live-tested.

`python -m app.cli init` remains an unversioned SQLite demo convenience, not an upgrade command. Prefer Alembic for new databases you intend to keep. An old v1 `init` database needs the verified-baseline procedure in the migration guide; never stamp it at head to skip schema changes.

Keep `LLM_ENABLED=0`: fixture mode limits source fetching, but enabling a model can still send fixture text and incur charges. No real identities or contact details appear in the two synthetic demonstration profiles. Three separate minimal Max factual-excerpt fixtures are developer regression inputs, not captured native HTML, seeded data, or an approved directory.

## Configuration

Copy `.env.example` to `.env`; environment variables override it. Never commit secrets or put the admin token in a URL. Production uses the root-only external environment file described in the server guide.

| Setting | Shipped example / meaning |
| --- | --- |
| `MODE` | `fixture`; use `live` only for approved public sources |
| `DATABASE_URL` | `sqlite:///./data/research.db` locally |
| `ADMIN_TOKEN` | A newly generated secret of at least 32 characters |
| `DEMO_AUTO_CREATE` | `0` in v2 examples; use Alembic explicitly |
| `ALLOWED_DOMAINS` | Empty; exact approved hostnames only |
| `WORKER_COUNT` | `3`, constrained to 2–4 internal threads |
| `MAX_JOBS`, `MAX_REQUESTS`, `MAX_LLM_CALLS` | `100`, `100`, `20`; per-run persistent ceilings, not currency limits |
| `REQUESTS_PER_MINUTE` | `6`; shared database-backed per-host pacing with robots delay |
| `MAX_RESPONSE_BYTES` | `2000000`; caps transmitted and decoded source-response bytes |
| `USER_AGENT` | `OncologyResearchDirectory/2.0` |
| `USER_AGENT_CONTACT` | Empty; optional real authorized HTTPS contact URL or mailto address |
| `LLM_ENABLED`, `LLM_PROVIDER` | `0`, `openai`; `mistral` also supported |
| `LLM_MAX_INPUT_CHARS` | `40000`; bounded literal excerpt selection, maximum 100000 |
| `LLM_MAX_OUTPUT_TOKENS` | `4000`; range 16–16000 |
| `LLM_INPUT_PRICE_PER_MILLION`, `LLM_OUTPUT_PRICE_PER_MILLION` | Unset; estimated USD cost remains unknown without both configured prices and usable provider usage |

Read the [model adapter guide](docs/LLM_ADAPTER.md) before transmitting text or enabling paid calls. Authentication remains one shared admin bearer token; reviewer names are self-entered audit labels. Keep the app on loopback through an SSH tunnel or restricted valid TLS. Never expose port 8000 or PostgreSQL publicly.

## Honest boundaries

- No JavaScript renderer, Brotli, proxy transport, authenticated-source login, CAPTCHA bypass, or PDF/OCR pipeline
- No authenticated per-user accounts/SSO or automatic current registry verification
- No automatic record approval, scheduled refresh, publication, outreach, or permission to reuse source content
- No completed live provider trial, real PostgreSQL integration run, or executed EC2/systemd/nginx deployment
- No demonstrated 200-doctor discovery target, 100-reviews/hour rate, or national coverage
- No full native hospital-page fixture corpus: the three Max cases are short reconstructed excerpts with explicit provenance limits

## Development checks

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Dependencies use the included constraints in `requirements.lock.txt`. Tests primarily use disposable SQLite databases and mocked source/provider responses. `POSTGRES_TEST_URL` opts into destructive integration setup in a dedicated disposable database only; never point it at production. A skipped PostgreSQL test is not a pass. See [TEST_REPORT.md](TEST_REPORT.md) for the authoritative executed counts and environment, and [architecture](docs/ARCHITECTURE.md) for data/consistency boundaries. Deployment instructions and upstream references are not proof of a deployment.

## Working with GitHub

The private repository is `OneSmarterInc/oncology-swarm`. Clone it once:

```sh
git clone https://github.com/OneSmarterInc/oncology-swarm.git
cd oncology-swarm
```

For an existing clean checkout, pull reviewed updates with `git pull --ff-only`.
Keep local configuration in the ignored `.env`; do not commit databases, tokens,
workbooks, or research outputs. Before running a changed version against a database,
read its migration notes and back up the database. Test local changes before pushing.

This GitHub copy preserves the v2 application, deployment templates, documentation,
and test suite. It omits the generated private `reports/live-pilot-v2.json` research
output; its artifact-only test skips when the file is absent. The small provenance-
tracked public-professional regression excerpts remain under `fixtures/real/`,
separate from the synthetic demo. No GitHub Actions workflow or deployment has been
enabled as part of the import. See `TEST_REPORT.md` for the repository-specific check.
