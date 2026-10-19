# Windows PowerShell quickstart: synthetic local demo

This walkthrough uses Python 3.12, SQLite, and included fictional fixtures. You do not need PostgreSQL, Docker, an AWS account, a credit card, or paid API keys. Internet access is needed to install Python and Python packages; the fixture run itself does not fetch live sources.

Commands below go in **PowerShell**, not inside Python's `>>>` prompt. Run one numbered step at a time. Keep the extracted folder outside protected system folders. Do not run as Administrator.

**Existing v1 data:** stop and follow [V2_MIGRATION.md](V2_MIGRATION.md). This walkthrough is for a fresh folder/database; never overwrite an existing `.env` or use a fresh-install command to suppress a migration error.

## 1. Clone the repository or extract a release

If Git is installed, clone the private repository in PowerShell, then continue
with step 2 from the `oncology-swarm` folder:

```powershell
git clone https://github.com/OneSmarterInc/oncology-swarm.git
Set-Location oncology-swarm
Get-ChildItem
```

Authenticate with your existing GitHub account when Git asks. Keep credentials
out of command text, files, and commits. Alternatively, use a release ZIP:

In File Explorer, right-click the downloaded ZIP and choose **Extract All**. Open the extracted folder that contains `README.md`, `requirements.txt`, and `app`.

Open PowerShell in that folder, or use a path like this, replacing it with your actual path:

```powershell
Set-Location "$HOME\Downloads\oncology_swarm_v2"
Get-ChildItem
```

If the ZIP produced an extra enclosing folder, navigate down one more level until you can see `requirements.txt`.

## 2. Check Python 3.12

Install Python 3.12 from [Python's official downloads](https://www.python.org/downloads/windows/) if it is not already installed. Open a **new** PowerShell window after installation.

```powershell
py -3.12 --version
```

The result must start with `Python 3.12`. If `py` is unavailable but the correct Python is installed, use its full installation path to create the environment below. Do not install packages from an unverified download link.

## 3. Create a virtual environment and install dependencies

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This guide calls the environment's Python directly. You do **not** need to run `Activate.ps1` or change PowerShell execution policy. Everywhere the main README says `python`, you can use `.\.venv\Scripts\python.exe` instead.

If a package installation fails, stop here and retain the error message with any tokens removed. Do not continue with a half-installed environment.

## 4. Generate a private token and write local settings

The next block replaces `.env`. Run it only for a new demonstration folder, not an existing live installation. It generates a unique token; there is no shared default password.

```powershell
$AdminToken = (.\.venv\Scripts\python.exe -m app.cli token).Trim()
if ($LASTEXITCODE -ne 0 -or $AdminToken.Length -lt 32) { throw "Token generation failed" }
@(
    "MODE=fixture"
    "DATABASE_URL=sqlite:///./data/research.db"
    "ADMIN_TOKEN=$AdminToken"
    "DEMO_AUTO_CREATE=0"
    "ALLOWED_DOMAINS="
    "WORKER_COUNT=3"
    "MAX_REQUESTS=100"
    "MAX_JOBS=100"
    "REQUESTS_PER_MINUTE=6"
    "REQUEST_TIMEOUT_SECONDS=15"
    "MAX_RESPONSE_BYTES=2000000"
    "LEASE_SECONDS=120"
    "MAX_ATTEMPTS=3"
    "LLM_ENABLED=0"
) | Set-Content -Encoding ascii .env
```

Keep `LLM_ENABLED=0` for this keyless exercise. Enabling it can make paid model calls even when the source mode is `fixture`.

Keep `.env` private. Do not email it, include it in screenshots, upload it with support logs, or commit it to Git. The program reads it automatically when you run commands from this folder. If you have conflicting `MODE`, `DATABASE_URL`, or other variables already set in PowerShell, remove those variables or use a fresh shell before proceeding; process environment values override `.env`.

## 5. Create the database and queue demonstration work

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m app.cli demo
.\.venv\Scripts\python.exe -m app.cli run-once
```

The migration revision should be `b592cf2d0101`. `demo` enqueues included synthetic profiles. `run-once` drains available work and exits. Expect two synthetic proposals to need human review. Read any warnings and validation errors; the fixtures exercise the review boundary and different contact scopes, not a production-complete dataset.

The SQLite file is local at `data\research.db`. A future retry can remain pending after `run-once`; the long-running worker handles waiting retries.

## 6. Open the review interface

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Leave this PowerShell window running. Open this address in your browser:

http://127.0.0.1:8000/

Enter the generated administrator token in the interface. To copy it, open `.env` locally in Notepad and copy only the value after `ADMIN_TOKEN=`. Do not put it in the address bar. The application may serve its interface shell without authentication; protected data/actions still require the bearer token.

Use the paginated queue and field-by-field evidence form to review each proposal. Enter your reviewer label/note, complete the checklist, and approve an eligible fictional row. To try the rejection path, you may reject the other fixture with a note such as "Demonstration rejection only"; the fixtures do not require rejection merely because they are fictional. Resolve or reject duplicate/quality warnings instead of approving every row. Approval only means you accepted the evidence for this exercise: it does not make a fictional doctor real or a claimed registration current.

## 7. Export approved records

Open a second PowerShell window in the same project folder. Do not start another worker.

```powershell
.\.venv\Scripts\python.exe -m app.cli export --output approved_directory.xlsx
```

Open `approved_directory.xlsx` in Excel or another compatible spreadsheet viewer. Only approved rows belong in the directory export. If you did not approve any rows, an empty approved directory is expected. Read the evidence and status fields before interpreting any row.

Close Excel before overwriting the same workbook on Windows. A file-lock error does not mean the queue lost its records.

## 8. Try the continuous coordinator, if needed

For new jobs or delayed retries, run this in the second PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m app.cli worker
```

It keeps waiting for work until you press **Ctrl+C**. One coordinator internally runs three workers by default. With SQLite, run only one `worker` or `run-once` process at a time. The API is a separate process and can stay open for review.

## 9. Stop safely and keep your work

- Press **Ctrl+C** in the worker window, then in the API window
- Keep `data\research.db`, your private `.env`, and any exported workbooks
- Do not delete a database to clear an error or reset a budget
- To start the review interface again, repeat step 6 from the same directory
- Use the [SQLite backup instructions](OPERATIONS.md#sqlite-demonstration-backup) before changing or removing demonstration data

## Common problems

| Symptom | What to check |
|---|---|
| `No module named app` | You are in the folder containing `app`, not its parent |
| `No module named ...` | Use the environment Python from step 3, and ensure dependencies installed successfully |
| Browser cannot connect | Keep the API command running; use exactly `127.0.0.1:8000` |
| Address already in use | A previous API may still be running; stop it with Ctrl+C instead of starting a second copy |
| Unauthorized / invalid token | Copy the token value only; restart the API after changing `.env` |
| Token missing or too short | Generate a fresh token with `python -m app.cli token`; never use a sample password |
| Database is locked | Stop extra coordinator processes; SQLite is the demonstration backend |
| No exported doctors | Review and approve an eligible proposal first |
| Budgets exhausted | Check [operations](OPERATIONS.md); raising limits is a deliberate operator decision |
| Live source refused | This guide uses fixtures. Live mode requires both an exact domain allowlist and an active human-approved official source record |

For real data, do not merely change `MODE` and run blindly. Read [live pilot](LIVE_PILOT.md) first. For a server, follow [PostgreSQL](POSTGRESQL.md) and [EC2](EC2_UBUNTU.md), with schema migrations and `DEMO_AUTO_CREATE=0`.
