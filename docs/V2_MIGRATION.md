# Fresh installation and v1 → v2 migration

V2 adds source/hospital masters, refresh generations, extended evidence-backed fields, lifecycle flags, indexed names, source-level merge provenance, budget runs, shared pacing, discovery leases, and model usage. The Alembic chain is:

```text
e271a68cbbf7  initial v1 schema
b592cf2d0101  directory v2 (head)
```

**Back up and rehearse on a restored copy before changing a database with useful data.** The v2 migration is intentionally forward-only. Rollback means restoring a pre-upgrade backup with the matching v1 application, never `downgrade` or stamping away changes. Do not run v1 code against a migrated v2 database.

## 1. Fresh empty database

Copy `.env.example`, generate/set the private admin token, leave `MODE=fixture`, `LLM_ENABLED=0`, and `DEMO_AUTO_CREATE=0`. Use a genuinely new database path, not an existing v1 file. From the v2 directory with its environment Python:

```sh
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m alembic current
python -m app.cli demo
python -m app.cli run-once
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expect revision `b592cf2d0101`. On Windows substitute `.\.venv\Scripts\python.exe`; see the quickstart. For a fresh PostgreSQL database follow `POSTGRESQL.md` and run Alembic under the approved service identity/environment. PostgreSQL migration/runtime has not been executed against a real server in this release environment.

Do not use `app.cli init` as a migration. It uses SQLite `create_all`, cannot upgrade existing tables, and deliberately rejects detected v1 schema. New persistent v2 databases should be created by Alembic so their revision is recorded.

## 2. Inventory, pause, and stop v1 writers

Record the original release, Python/dependency environment, database URL, queue states/counts, reviewed/exportable records, audit count, settings, and last trusted workbook. Preserve the exact old code and private configuration separately. Do not copy secrets into a release archive.

Pause through the existing authenticated control endpoint and allow/plan for outstanding work. Stop the worker and API; no discovery/review process may write during migration. Local consoles: Ctrl+C and wait for exit. Existing systemd deployment:

```sh
sudo systemctl stop oncology-worker oncology-api
sudo systemctl is-active oncology-worker oncology-api
```

`inactive` is expected; the command's nonzero exit for inactive units is normal. Confirm no extra coordinator/API copies remain. Pause alone is not a substitute for stopping writers.

## 3. Make and verify an untouched backup

Use [operations backup/restore](OPERATIONS.md). For SQLite, create the backup folder and use its backup API after writers stop, with a new unique filename (the command refuses to overwrite an existing backup):

```sh
python -c "import sqlite3; from pathlib import Path; Path('backups').mkdir(exist_ok=True); b=Path('backups/v1-before-upgrade.db'); b.touch(exist_ok=False); s=sqlite3.connect('file:data/research.db?mode=ro',uri=True); d=sqlite3.connect(b); s.backup(d); d.close(); s.close()"
python -c "import sqlite3; c=sqlite3.connect('file:backups/v1-before-upgrade.db?mode=ro',uri=True); print(c.execute('PRAGMA integrity_check').fetchall()); print(c.execute('PRAGMA foreign_key_check').fetchall()); c.close()"
```

Choose a unique backup filename; do not overwrite an older backup. Expect integrity `ok` and no foreign-key violations. Record a checksum and retain this file unchanged. Make a **second writable rehearsal copy** and set `DATABASE_URL` to that copy, not the untouched backup. For PostgreSQL make a consistent dump and restore into a separate database; never rehearse in production.

## 4. Determine the existing schema baseline

`alembic current` reads migration metadata; no revision output does **not** mean an existing database is empty. V1 `app.cli init` and demo auto-create used `create_all`, so their SQLite files commonly have all v1 tables but no `alembic_version`.

- An empty database: use the fresh-install procedure
- A database already stamped `e271a68cbbf7`: verify it actually matches that schema, then rehearse the upgrade
- An unstamped v1 `init` database: verify the exact schema on the backup before stamping the **rehearsal copy** at `e271a68cbbf7`
- Any unexpected tables, columns, constraints, indexes, triggers, partial upgrade, revision, or schema mismatch: stop and investigate; do not guess a stamp

For schema verification, build a fresh disposable SQLite reference using the included baseline migration, with a separate explicit database URL:

```sh
# POSIX shell; replace the reference path with a new disposable path.
DATABASE_URL=sqlite:///./backups/v1-schema-reference.db python -m alembic upgrade e271a68cbbf7
```

On PowerShell set `$env:DATABASE_URL` to the reference URL for that command, then restore the intended rehearsal URL. Environment values override `.env`; always check the target before running a write command.

Compare the untouched backup with this baseline, ignoring only the migration metadata table. Use SQLAlchemy inspection or a reviewed schema tool to compare **all** tables, column types/lengths/nullability/defaults, primary/foreign keys, unique/check constraints, and indexes; inspect SQLite schema objects for unexpected views/triggers too. The baseline has exactly `control`, `jobs`, `audit_log`, `proposals`, `snapshots`, `doctors`, `affiliations`, `contacts`, and `registrations`. In particular `jobs.url` is unique in v1; v2 will replace it with `(url,generation)`. Neither matching table names nor a successful integrity check alone establishes a matching schema. Inspect all definitions in `migrations/versions/e271a68cbbf7_initial_durable_research_schema.py` and retain the comparison result.

Only after that exact-schema check succeeds on the backup, and `DATABASE_URL` points at the writable rehearsal copy of that verified database:

```sh
python -m alembic stamp e271a68cbbf7
python -m alembic current
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

**Never stamp an old v1 database at `head`/`b592cf2d0101`.** Stamping records metadata without running schema changes. Do not stamp an unfamiliar already-v2/create-all database using the v1 procedure either; obtain a separate exact-schema reconciliation.

## 5. Verify the rehearsal

Keep models disabled and the restored database paused; do not accidentally send live requests from a copy. Check integrity/foreign keys again, revision, counts, representative evidence/review history, extended columns, normalized-name backfill, sources/provenance links, and a reviewed export. Preserve synthetic labels. Migrated real records with no approved source remain `unclassified`; the migration does not invent source approval dates, reviewers, permissions, or hospital links.

The migration tests exercise fresh SQLite and a populated v1→v2 forward upgrade. They do not verify your database, backup restoration, Windows host, or PostgreSQL deployment. Use the dedicated opt-in PostgreSQL tests only on a disposable test database.

## 6. Apply, start privately, then resume deliberately

After a successful rehearsal and an unchanged/verified production baseline:

1. Keep all writers stopped and take a fresh pre-upgrade backup if anything changed
2. Install the matching reviewed v2 code/dependencies; preserve the old release
3. Set `DEMO_AUTO_CREATE=0`, correct `DATABASE_URL`, and keep `LLM_ENABLED=0`; do not broaden source allowlists
4. If production is unstamped v1, repeat the verified-baseline stamp at `e271a68cbbf7` only; then run `upgrade head`, `current`, and `check` against the intended database
5. For systemd, use the explicit service-user/environment migration invocation in `POSTGRESQL.md`; an arbitrary shell does not load `/etc/oncology-swarm.env`
6. Start the API privately first, authenticate, verify status, evidence, review flags, export and revision; restart the worker only after these checks
7. Confirm the database remains paused, then resume deliberately if source/permission/budget conditions permit

Typical existing-service commands after verification:

```sh
sudo systemctl start oncology-api
# Check private authenticated status/review/export before starting research.
sudo systemctl start oncology-worker
sudo systemctl status oncology-api oncology-worker --no-pager
```

Restarting services does not reset budget counters or remove pause. Per-run reset needs its own recorded reason and paused/drained checks. No source or model use is automatically approved by migration.

## Recovery if anything fails

Stop writers and preserve the failed upgraded database for diagnosis. Restore the untouched pre-upgrade backup into a new database/path, install the matching v1 release/configuration, and check counts/evidence/export while paused. Point services to that restored copy only after verification. Do not overwrite the sole backup, attempt a destructive downgrade, run mixed code versions, or stamp head to suppress an error. Work performed after the backup may need explicit reconciliation; a backup rollback does not promise zero data loss.
