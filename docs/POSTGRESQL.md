# PostgreSQL installation and schema migrations

PostgreSQL is the intended persistent-server backend, but no PostgreSQL server was installed/available for a real integration run in this release environment. The instructions below are templates, not evidence of deployment. For an existing v1 database, read [V2_MIGRATION.md](V2_MIGRATION.md) before changes.

Use PostgreSQL for a persistent server deployment. SQLite is the fixture demonstrator. These commands target a **new dedicated Ubuntu 24.04 LTS server**, with its distribution-maintained Python 3.12 and PostgreSQL packages. The same pattern can be adapted to another supported Ubuntu release after checking package versions. It is not a Windows PostgreSQL installation guide.

The deployment in this starter keeps PostgreSQL on the same server and connects through a Unix socket. A non-superuser database role matches the Linux service user. There is no database password in an application URL and no reason to open port 5432. This design uses Ubuntu's local peer authentication ([Ubuntu installation guidance](https://ubuntu.com/server/docs/how-to/databases/install-postgresql/), [PostgreSQL peer authentication](https://www.postgresql.org/docs/current/auth-peer.html)).

## 1. Install PostgreSQL

```bash
sudo apt update
sudo apt install -y postgresql postgresql-client
sudo systemctl enable --now postgresql
sudo -u postgres psql -Atc 'SHOW server_version;'
```

Use the client tools matching the installed server major version. Do not replace distribution configuration with a random tutorial or change `listen_addresses` to `*`.

## 2. Create the service account and database role

The EC2 guide also creates this Linux account. If it already exists, leave it in place.

```bash
id oncology || sudo useradd --system --user-group \
  --home-dir /var/lib/oncology-swarm --create-home \
  --shell /usr/sbin/nologin oncology
sudo -u postgres createuser --no-superuser --no-createdb --no-createrole oncology
sudo -u postgres createdb --owner=oncology --encoding=UTF8 --template=template0 oncology
sudo -u postgres psql -d oncology -v ON_ERROR_STOP=1 <<'SQL'
REVOKE ALL ON DATABASE oncology FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
```

Run role/database creation once. If either already exists, inspect it rather than dropping it to rerun the command. The application role owns only this database; it is not a PostgreSQL superuser. This minimal deployment uses that owner for migrations and runtime. A higher-assurance deployment should use a separate migration owner and a narrower runtime role after reviewing the required grants.

## 3. Check local authentication and remove TCP exposure

First test peer authentication under the correct Linux user:

```bash
sudo -u oncology psql -h /var/run/postgresql -d oncology \
  -c 'SELECT current_user, current_database();'
```

It should report `oncology` for both values. If it fails, inspect the actual configuration files:

```bash
sudo -u postgres psql -Atc 'SHOW hba_file;'
sudo -u postgres psql -Atc 'SHOW config_file;'
```

In `pg_hba.conf`, an appropriate local rule is `local oncology oncology peer`. Put a deliberately scoped rule before any broader rule that would otherwise match. Do not change local authentication to `trust`. Reload after an authorized change with `sudo systemctl reload postgresql` and retest.

For this **dedicated server only**, disable PostgreSQL TCP listeners entirely:

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER SYSTEM SET listen_addresses = '';"
sudo systemctl restart postgresql
sudo -u oncology psql -h /var/run/postgresql -d oncology -c 'SELECT 1;'
sudo ss -ltnp
```

There should be no TCP listener on port 5432. A shared database host may have other clients; do not apply this change there without checking those dependencies. Keep EC2 security-group ingress for 5432 absent even when TCP is disabled. See [PostgreSQL connection settings](https://www.postgresql.org/docs/current/runtime-config-connection.html).

## 4. Supply the production database URL

The application code/virtual environment should already be installed at `/opt/oncology-swarm/app`, as described in the [EC2 guide](EC2_UBUNTU.md).

Create an external environment file:

```bash
sudo install -o root -g root -m 0600 \
  /opt/oncology-swarm/app/deploy/oncology-env.example /etc/oncology-swarm.env
sudoedit /etc/oncology-swarm.env
```

Use this URL, with the local socket path:

```text
DATABASE_URL=postgresql+psycopg://oncology@/oncology?host=/var/run/postgresql
DEMO_AUTO_CREATE=0
```

Generate a fresh token from the application directory:

```bash
cd /opt/oncology-swarm/app
/opt/oncology-swarm/app/.venv/bin/python -m app.cli token
```

Set `ADMIN_TOKEN` to that value in `/etc/oncology-swarm.env` using `sudoedit`. Keep `ALLOWED_DOMAINS` empty until you approve real source hosts. Never publish this file, store it in Git, or make it world-readable. The system service manager reads this root-only file and supplies its variables to the application process.

## 5. Apply Alembic migrations explicitly

With production services stopped, run:

```bash
sudo systemd-run --wait --pipe --collect --service-type=exec \
  --uid=oncology --gid=oncology \
  --working-directory=/opt/oncology-swarm/app \
  --property=EnvironmentFile=/etc/oncology-swarm.env \
  /opt/oncology-swarm/app/.venv/bin/alembic upgrade head
```

Then verify:

```bash
sudo systemd-run --wait --pipe --collect --service-type=exec \
  --uid=oncology --gid=oncology \
  --working-directory=/opt/oncology-swarm/app \
  --property=EnvironmentFile=/etc/oncology-swarm.env \
  /opt/oncology-swarm/app/.venv/bin/alembic current
sudo -u oncology psql -h /var/run/postgresql -d oncology \
  -c 'SELECT version_num FROM alembic_version;'
```

`upgrade head` applies the included chain through `b592cf2d0101`; `current` must report that revision. The v2 migration is forward-only; rollback restores a pre-upgrade backup with the matching old release. Do not run `alembic init`, replace the migrations directory, or stamp a database without proving its schema matches. The API does not auto-create a PostgreSQL schema. `DEMO_AUTO_CREATE=1` is a SQLite-only demonstration convenience, not a migration strategy ([Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)).

## 6. Start the application and test the real backend

Return to the EC2 guide and start the systemd services. Check `/healthz`, an authenticated `/api/status`, and the database-backed review flow through an SSH tunnel. A successful SQLite fixture test does not prove PostgreSQL leasing or a particular server's permissions work. Perform a small end-to-end PostgreSQL staging test before allowing live work.

For every later schema change: back up, restore-test if the change is material, stop writers, install the matching reviewed application version, run its migration, verify status, then restart. Do not use downgrade as an unexamined data-recovery method. See [backup and restore](OPERATIONS.md).
