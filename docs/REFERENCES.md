# Primary technical references

Documentation links consulted on 19 October 2026. These support platform conventions; the v2 application's own files and tests define its implemented behavior. Versions shown on upstream “current” pages may differ from the versions installed from Ubuntu 24.04 or pinned in `requirements.txt`. Check compatibility before changing versions.

## Python and local demo

- [Python 3.12 virtual environments](https://docs.python.org/3.12/library/venv.html): environment creation and direct interpreter invocation without activation
- [Official Windows Python downloads](https://www.python.org/downloads/windows/): trusted download source
- [Python SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup): consistent SQLite backups

## PostgreSQL and migrations

- [Ubuntu PostgreSQL installation](https://ubuntu.com/server/docs/how-to/databases/install-postgresql/): distribution packages and local authentication defaults
- [PostgreSQL peer authentication](https://www.postgresql.org/docs/current/auth-peer.html): matching local operating-system identity to database role
- [PostgreSQL createuser](https://www.postgresql.org/docs/17/app-createuser.html): non-superuser role creation options
- [PostgreSQL connection settings](https://www.postgresql.org/docs/current/runtime-config-connection.html): Unix sockets and disabling TCP listeners with an empty `listen_addresses`
- [SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html): connection URL and driver behavior
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html): explicit migration commands
- [PostgreSQL backup approaches](https://www.postgresql.org/docs/current/backup.html): logical dumps, physical backups, and continuous archiving
- [pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html) and [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html): custom archive backup/restore behavior

## Server access and services

- [systemd execution settings](https://www.freedesktop.org/software/systemd/man/257/systemd.exec.html): environment files, service identities, and filesystem restrictions
- [systemd-run](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html): transient commands under a service identity
- [nginx HTTPS configuration](https://nginx.org/en/docs/http/configuring_https_servers.html): certificate/private-key and TLS configuration
- [nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html): reverse-proxy behavior and forwarded headers
- [Ubuntu TLS certificate guidance](https://ubuntu.com/server/docs/how-to/security/obtain-tls-certificates/): obtaining and operating certificates
- [EC2 security groups](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-security-groups.html): scoped inbound rules
- [EC2 SSH access](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-to-linux-instance.html): trusted instance connection prerequisites
- [EC2 metadata options](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html): requiring IMDSv2

## Costs

No numeric price estimate is supplied. Check your actual region, instance, disk, networking, and backup choices against [EC2 pricing](https://aws.amazon.com/ec2/pricing/), [EBS pricing](https://aws.amazon.com/ebs/pricing/), [VPC pricing](https://aws.amazon.com/vpc/pricing/), and the [AWS calculator](https://calculator.aws/).

For implemented scope and unverified acceptance criteria, use [GAP_MATRIX.md](GAP_MATRIX.md), [LIVE_PILOT_RESULTS.md](LIVE_PILOT_RESULTS.md), and [TEST_REPORT.md](../TEST_REPORT.md). The v2 upgrade procedure is [V2_MIGRATION.md](V2_MIGRATION.md); source decisions are [SOURCE_POLICY.md](SOURCE_POLICY.md). No link below is evidence of a real deployment or a completed live provider call.

These references do not provide authorization to scrape a medical institution, verify a doctor, publish personal information, or use contacts for outreach. Source selection and review remain operator responsibilities.
