# Operations, budgets, backups, and restore

## Daily checks

Use the authenticated review interface, `GET /api/status`, and `GET /api/metrics` to check paused state, queue states, active discovery, review backlog, budgets, and model usage/cost uncertainty. Use **Connect / refresh** to get current data; there are no real-time push updates. These procedures and deployment templates were not executed against a real PostgreSQL/systemd server in this release.

On Ubuntu:

```bash
sudo systemctl status oncology-api oncology-worker --no-pager
sudo journalctl -u oncology-worker --since '1 hour ago' --no-pager
sudo journalctl -u oncology-api --since '1 hour ago' --no-pager
df -h
free -h
sudo ss -ltnp
```

Watch growing `failed`/`retry` counts, stale leases, depleted request limits, slow review, memory pressure, evidence/database growth, disk space, and certificate expiry if using TLS. The JSON metrics endpoint is bundled; hosted alerts, a scheduled daily summary, and a Prometheus service are not. Assign a human operator and a backup/restore owner before live use.

`/healthz` is a generic unauthenticated liveness check. It is not evidence that a worker is processing, the database is healthy, or a source fetch succeeded. Check authenticated status and a known reviewed record as well.

## Limits have explicit meanings

| Setting | Meaning |
|---|---|
| `WORKER_COUNT=3` | Internal threads in one coordinator; allowed range 2–4 |
| `MAX_REQUESTS=100` | Per-run HTTP-request allowance recorded in the database, across restarts; a reservation is consumed before each HTTP attempt, including robots, redirect hops, failed network attempts, and retries |
| `MAX_JOBS=100` | Per-run count of new candidate jobs and explicit refresh generations; submitting an already-known canonical URL returns its latest existing job without spending another slot |
| `REQUESTS_PER_MINUTE=6` | Database-backed per-host pacing shared by discovery and workers; a site's larger supported crawl delay also applies |
| `REQUEST_TIMEOUT_SECONDS=15` | Separate DNS-result wait and individual HTTP-exchange bounds; a whole job can involve multiple DNS/robots/page requests, pacing and retries. At most four unresolved OS DNS workers are allowed; timeouts do not use an alternate resolver |
| `MAX_RESPONSE_BYTES=2000000` | Per-response transmitted and decoded byte limits, including robots and sitemap responses; not a total database-size ceiling |
| `MAX_REDIRECTS=3` | Default bounded redirect hops; every destination is checked again |
| `LEASE_SECONDS=120` | Worker lease window, extended by a heartbeat while processing |
| `MAX_ATTEMPTS=3` | Maximum automatic attempts for retryable failures, including crash-recovered leases |
| `LLM_ENABLED=0` | Optional model calls disabled in the default run |
| `MAX_LLM_CALLS=20` | Durable per-run model-call reservation ceiling when enabled; not a monetary budget |
| `LLM_MAX_OUTPUT_TOKENS=4000` | Per-call output-token setting for the optional adapter |
| `LLM_MAX_INPUT_CHARS=40000` | Optional literal source-excerpt character bound; full evidence text remains retained |

With the default `LLM_ENABLED=0`, fixtures make no network calls. Fixture source loading makes zero HTTP requests and consumes no source-fetch HTTP-request budget. Optional model calls have a separate durable counter; consult [the adapter guide](LLM_ADAPTER.md) before enabling that path. `MODE=fixture` controls source fetching only: `LLM_ENABLED=1` can still send fixture text and incur paid model calls. Fixture jobs still use job slots. A paused reservation or a rejected URL need not create an outbound attempt. DNS resolution occurs before HTTP reservation; a DNS-only failure may not consume an HTTP-request slot.

Budget counters persist across restarts and belong to a numbered run. You may deliberately raise a current run's configured total cap, restart both services, and retry eligible failed work; a higher setting is a total ceiling, not an additional allowance. Alternatively, complete outstanding work, then pause and use `POST /api/control/reset-budgets` with a nonempty reviewer/note to open a new audited run. It requires **no queued, retry, or leased jobs and no active discovery lease**. Expired discovery runs are marked failed and fenced from future requests. Review/approved/history rows remain; source approvals and host pacing are not erased. The reset stays paused and records previous totals/reason in the audit.

Drain before pausing if you want to reset: queued/retry work cannot drain while claims are paused. If exhausted caps strand work, inspect causes and deliberately authorize adequate current-run caps before finishing that work. There is no force-reset or queue-delete bypass. Do not remove audit/history or recreate the database to hide a cap.

Host pacing is persisted in the database and shared by API discovery and worker fetchers. It rechecks larger learned robots delays instead of preallocating stale future slots. This implementation has offline tests, not a real PostgreSQL multiprocess load test. Keep **one coordinator process** for the SQLite demo and initial rollout. Database pacing is not permission to scale traffic or a production-performance guarantee.

The optional adapter replaces deterministic extraction when enabled. A transport/envelope/malformed-JSON model failure does not silently fall back; it becomes a failed job requiring diagnosis and, where eligible, deliberate manual retry. A completed JSON object with schema/evidence errors remains in human review, with approval blocked. A crashed job with a reserved model call is failed rather than automatically charged again. Inspect provider usage first: an interrupted response can leave the prior charge uncertain. Retrying previously reserved model work requires `{"acknowledge_possible_additional_model_charge":true}` and can incur an additional charge. A model-call reservation is consumed before the adapter runs, including a local early rejection before a completed provider response. Raising `MAX_LLM_CALLS` authorizes more calls, not a known currency amount.

## Pause, stop, and retries

- `POST /api/control` with `{"paused":true}` records a durable pause; `{"paused":false}` resumes
- Pause stops new job claims and future HTTP reservations; a request already in flight can finish
- For maintenance, stop the worker and API services and wait for them to exit
- Retryable network/429/5xx failures can enter delayed `retry` until the automatic attempt limit
- Robots refusal, unsafe URL/DNS/TLS conditions, and exhausted budgets are not invitations to bypass checks
- `run-once` exits when no jobs are currently ready; delayed retries can remain. Use `worker` for continuous processing
- After fixing a legitimate failed-fetch cause, `POST /api/jobs/{id}/retry` with an appropriate JSON body requeues that job and resets its attempts, but does not reset HTTP or model-call budgets
- A human-rejected proposal cannot be retried as a failed fetch; its review and evidence remain in the audit trail

An interrupted lease is recovered after expiry by a coordinator. A stale worker must not commit over a recovered lease. Recovery can involve another permitted request, which still consumes budget. Keep the clock correct on the server and monitor repeated lease expiry before raising concurrency.

For a service pause during maintenance:

```bash
sudo systemctl stop oncology-worker oncology-api
# Perform the planned maintenance only after services are stopped.
sudo systemctl start oncology-api oncology-worker
```

A paused database remains paused after restart. Resume deliberately through the authenticated interface/API.

## Correction, withdrawal, merge, and refresh

Use current proposal versions and a meaningful reviewer/note for every action. Correction removes an unmerged approved record from export while it is repaired/reapproved. Withdrawal removes it from future workbooks and quarantines any merged target depending on its evidence. Existing downloaded workbooks are not recalled automatically; track recipients and replace them through an authorized process.

Merge only after comparing both approved source records. The target identity stays primary and all contributing evidence survives. To repair a merged group, unmerge through the UI/API; both resulting groups are withdrawn. Split outer/nested groups first, then correct and explicitly reapprove individual records. Still-merged sources cannot be corrected/refreshed directly.

Manual refresh spends a new job slot and source requests. An equal hash records a fresh unchanged snapshot without another model call only when the original remains active, approved and unquarantined. Changed evidence is retained and the old record quarantined before extraction/model work, including when that later work fails. A failed refresh with a retained snapshot cannot retry in place; create another refresh from the original. Returning to the original hash after quarantine still requires fresh review. Approving the replacement supersedes the old job. Rejecting the change does not revive the stale export. Pending/newer changed evidence cannot be bypassed by old correction or merge. There is no periodic refresh scheduler, so assign a freshness process explicitly.

## Model-usage monitoring

`/api/metrics` or `python -m app.cli metrics` reports current-run token counts, reserved calls, recorded responses, calls without usage, available USD estimates, and partial/unknown flags. Prices are unknown unless both USD-per-million rates are configured; missing usage stays unknown. An estimate is not provider billing or a monetary cap. Check provider billing after ambiguous calls and use provider-side spending controls before enabling paid extraction.

## Rotate the administrator token

1. Plan for active reviewers to reauthenticate; there is one shared admin token, not separate user accounts
2. Generate a new token with `python -m app.cli token` in the project's virtual environment
3. Edit `.env` locally or root-only `/etc/oncology-swarm.env` on the server
4. Restart both application services so their cached settings are replaced
5. Test the new token, confirm the old token is rejected, and remove it from any local password record/clipboard

The token is sent only in `Authorization: Bearer ...`. Never place it in a query string, export, screenshot, shell transcript, nginx config, or repository. The UI keeps it only in page memory; a reload requires entering it again. An environment file is a practical starter configuration, not a hardware-backed secret store; root and a compromised application account remain trusted.

## PostgreSQL backup

Application jobs, visible-text snapshots, response-content hashes, proposals, evidence, approved records, and audit events are in PostgreSQL. Original raw HTML is **not** retained. A database backup covers the application's retained data; it cannot recreate discarded HTML. Also retain the exact application release and migration files. Back up `/etc/oncology-swarm.env` separately in protected encrypted storage, or plan to generate a new admin token during recovery. Do not bundle secrets into normal support/release archives.

For this small single-host starter, a logical dump is an accessible baseline. `pg_dump` produces a consistent database snapshot; it is not continuous point-in-time recovery. Choose backup frequency and retention for the loss window your organization accepts. Higher-volume deployments need a reviewed physical/WAL backup strategy. See [PostgreSQL backup documentation](https://www.postgresql.org/docs/current/backup.html).

Install and run the included script:

```bash
sudo install -o root -g root -m 0750 \
  /opt/oncology-swarm/app/deploy/backup-postgres.sh /usr/local/sbin/oncology-backup
sudo /usr/local/sbin/oncology-backup
sudo ls -lh /var/backups/oncology-swarm
```

It writes a custom-format database dump, an archive listing, and a SHA-256 checksum with restrictive permissions. The archive listing is a structural check, **not a full restore test**. It neither encrypts the dump itself nor copies it off-host. Use your approved encrypted off-host storage/transfer process. A second file on the same EC2 volume will not protect you from volume/account loss.

Optional daily local backup timer, at 02:15 UTC with up to five minutes of jitter:

```bash
sudo install -o root -g root -m 0644 \
  /opt/oncology-swarm/app/deploy/oncology-backup.service /etc/systemd/system/
sudo install -o root -g root -m 0644 \
  /opt/oncology-swarm/app/deploy/oncology-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oncology-backup.timer
sudo systemctl list-timers oncology-backup.timer --all
sudo journalctl -u oncology-backup.service --no-pager
```

The timer must be explicitly installed/enabled; it is not running merely because files are included. The script performs no retention deletion. Review available disk space, decide a retention policy, and safely remove superseded copies only after verifying another required recoverable backup exists.

## Restore test into a separate database

Run this test before relying on backups, after material schema changes, and at an interval appropriate to your recovery goal. Use trusted backups only: restoring a database can execute stored SQL. Keep the original production database intact.

1. Choose a real dump path and inspect its checksum/listing:

```bash
BACKUP=/var/backups/oncology-swarm/REPLACE_WITH_REAL_BACKUP.dump
sudo test -s "$BACKUP"
sudo sha256sum "$BACKUP"
sudo cat "$BACKUP.sha256"
sudo pg_restore --list "$BACKUP" | head -n 30
```

Compare checksum values. A checksum detects corruption relative to your trusted recorded value, not whether an attacker changed both files.

2. Create a **new** empty test database and restore:

```bash
sudo -u postgres createdb --owner=oncology --template=template0 oncology_restore_test
sudo -u postgres psql -d oncology_restore_test -v ON_ERROR_STOP=1 \
  -c "REVOKE ALL ON DATABASE oncology_restore_test FROM PUBLIC; REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
set -o pipefail
sudo cat "$BACKUP" | sudo -u oncology pg_restore \
  --host=/var/run/postgresql --dbname=oncology_restore_test \
  --no-owner --no-acl --exit-on-error
```

The pipe lets root read the private backup while the restore runs as the non-superuser application owner. Stop on errors. If `oncology_restore_test` already exists, inspect it or choose another fresh name; don't automatically drop it.

3. Check the restored data and keep its workers paused:

```bash
sudo -u oncology psql -h /var/run/postgresql -d oncology_restore_test \
  -v ON_ERROR_STOP=1 <<'SQL'
UPDATE control SET paused = TRUE WHERE id = 1;
SELECT version_num FROM alembic_version;
SELECT state, COUNT(*) FROM jobs GROUP BY state ORDER BY state;
SELECT COUNT(*) AS retained_doctors FROM doctors;
SELECT COUNT(*) AS exportable_doctors FROM doctors WHERE active = TRUE AND needs_review = FALSE;
SELECT COUNT(*) AS evidence_snapshots FROM snapshots;
SELECT COUNT(*) AS audit_events FROM audit_log;
SQL
```

Compare counts and sample records with values recorded at backup time, not a later production database that may have changed. Confirm expected evidence and review decisions are present.

4. Test a read/export against the restored database with a separate root-only configuration:

```bash
sudo install -o root -g root -m 0600 \
  /etc/oncology-swarm.env /etc/oncology-restore-test.env
sudoedit /etc/oncology-restore-test.env
```

Change only that file's URL to:

```text
DATABASE_URL=postgresql+psycopg://oncology@/oncology_restore_test?host=/var/run/postgresql
```

Then:

```bash
sudo systemd-run --wait --pipe --collect --service-type=exec \
  --uid=oncology --gid=oncology \
  --working-directory=/opt/oncology-swarm/app \
  --property=EnvironmentFile=/etc/oncology-restore-test.env \
  /opt/oncology-swarm/app/.venv/bin/python -m app.cli export \
  --output /var/lib/oncology-swarm/restore-test.xlsx
```

Inspect the exported workbook with an authorized reviewer. Do not start a worker against the restore-test database. Record restore time, backup timestamp, application version, counts, sample verification, and any errors. Limit access to the restored copy and plan its authorized cleanup because it contains another copy of the same data.

## Recover the service after a failure

1. Stop API/worker writers; preserve the failed database/volume for diagnosis
2. Rebuild on a clean server if the host or credentials may be compromised
3. Recreate the Linux service account/database role and install the matching trusted application release
4. Restore a trusted backup into a new database and complete the checks above
5. Keep the recovered control row paused; point `/etc/oncology-swarm.env` to the recovered database and rotate the administrator token if needed
6. Apply only reviewed migrations needed for the chosen application version, after another backup
7. Start the API; verify data, permissions, listening ports, and a reviewed export before starting/resuming workers
8. Assess lost work after the backup timestamp and whether any source information is now stale

Do not delete the old database until recovery is verified and its retention decision is approved. Do not promise zero data loss with periodic logical dumps.

## SQLite demonstration backup

Stop the API and coordinator first. From Windows PowerShell in the project directory:

```powershell
New-Item -ItemType Directory -Force .\backups | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item .\data\research.db ".\backups\research-$Stamp.db"
```

For a consistent snapshot while other connections exist, use SQLite's backup API instead of copying an active database or its WAL files independently:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('data/research.db'); d=sqlite3.connect('backups/research-backup.db'); s.backup(d); d.close(); s.close()"
```

Choose a unique destination if you need to preserve an earlier backup. Test a restored copy in a separate demonstration folder before replacing any working database. Do not use a live `.db` file copy as the production PostgreSQL backup procedure.

## Updating safely

For v1 upgrades, follow [V2_MIGRATION.md](V2_MIGRATION.md), especially the verified baseline for unstamped SQLite databases. The v2 migration is forward-only; rollback restores a pre-upgrade backup with the matching old release. Never stamp head to bypass schema changes.

Back up first. Stop writers, install a reviewed release, install its exact dependencies into the environment, run its migrations, and verify before resuming. Keep the previous application release and a tested recovery path. Running `pip install --upgrade` against arbitrary application dependencies is not an upgrade plan. Restrict dependency changes to reviewed/tested releases, keep the OS patched, and retest fetch/evidence/security boundaries after changes.
