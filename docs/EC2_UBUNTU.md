# EC2 Ubuntu deployment: one private research server

This guide prepares an installation on **one new Ubuntu 24.04 LTS EC2 instance**: Python API, one bounded worker coordinator, and local PostgreSQL. It does not provision an instance for you. No EC2/systemd/nginx deployment or real PostgreSQL integration was executed for this release. Treat this as an unverified deployment template and complete staging acceptance before live work. For an existing installation read [V2_MIGRATION.md](V2_MIGRATION.md) first; do not overwrite a running release/database.

No RDS, Redis, SQS, Kubernetes, container registry, load balancer, or other separately managed AWS service is required. EC2 uses attached storage and networking, which can have their own charges. External backup storage is strongly recommended; it need not be an additional AWS service.

Start with SSH-tunnel-only access. A TLS nginx option is provided for an administrator who has a real domain, valid certificate, and restricted reviewer IPs. The review application is administrative, not a public doctor-search website.

## 1. Choose the server and networking deliberately

In your own AWS account, select a region and an official Ubuntu 24.04 LTS image from Canonical. A modest instance with enough memory for Python, PostgreSQL, and Excel exports is a starting point, not a benchmarked sizing promise. Use a small pilot and monitor memory/disk before scaling; more workers can increase source load without making review faster.

- Use an encrypted persistent volume with room for PostgreSQL, evidence, logs, and backups
- Use an SSH key you control; keep its private key on your computer
- Require IMDSv2 for instance metadata; the application needs no AWS credentials or instance role
- Security group: permit SSH TCP 22 **only from your current administrator public IP**, or your organization's approved access path
- Do **not** add inbound rules for TCP 8000 or 5432
- For tunnel-only use, add no HTTP/HTTPS ingress
- For optional TLS use, allow TCP 443 only from authorized reviewer public IPs
- Check both IPv4 and IPv6 rules; don't accidentally expose an IPv6 listener
- Do not enable broad permissions simply to fix a connection error

Review [EC2 security-group guidance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-security-groups.html) and [instance metadata configuration](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html). Security groups and host configuration must both agree with the intended exposure.

## 2. Connect from Windows and copy the archive

Use PowerShell on your own computer. Replace every placeholder with your actual key path, verified instance address, and downloaded archive name. Do not type placeholder angle brackets literally.

```powershell
ssh -i "$HOME\Keys\my-ec2-key.pem" ubuntu@YOUR_EC2_PUBLIC_DNS
```

Verify the server host key through your trusted EC2/administrator information when connecting for the first time; do not suppress host-key checks. In a second local PowerShell window:

```powershell
scp -i "$HOME\Keys\my-ec2-key.pem" .\oncology_swarm_v2.zip ubuntu@YOUR_EC2_PUBLIC_DNS:~/
```

If your archive has a different filename, use it. The [AWS SSH documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-to-linux-instance.html) covers operating-system-specific key permissions.

## 3. Install Python and the application

The following commands run in the **Ubuntu SSH shell**, not local Windows PowerShell:

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv unzip rsync postgresql postgresql-client
python3.12 --version
id oncology || sudo useradd --system --user-group \
  --home-dir /var/lib/oncology-swarm --create-home \
  --shell /usr/sbin/nologin oncology
mkdir -p ~/oncology-release
unzip ~/oncology_swarm_v2.zip -d ~/oncology-release
find ~/oncology-release -maxdepth 3 -name requirements.txt -print
```

Locate the extracted project directory containing `requirements.txt`, `app`, and `deploy`. Set `SOURCE` to that actual path. The next line is a typical ZIP layout; adjust it if the archive is wrapped differently:

```bash
SOURCE="$HOME/oncology-release/oncology_swarm_v2"
test -f "$SOURCE/requirements.txt" || { echo 'Correct SOURCE before continuing'; exit 1; }
sudo install -d -o root -g root -m 0755 /opt/oncology-swarm/app
sudo rsync -a --exclude=.venv --exclude=.env --exclude=data --exclude='*.xlsx' \
  "$SOURCE/" /opt/oncology-swarm/app/
sudo chown -R root:root /opt/oncology-swarm/app
sudo chmod -R u=rwX,go=rX /opt/oncology-swarm/app
sudo python3.12 -m venv /opt/oncology-swarm/app/.venv
sudo /opt/oncology-swarm/app/.venv/bin/python -m pip install --upgrade pip
sudo /opt/oncology-swarm/app/.venv/bin/python -m pip install \
  -r /opt/oncology-swarm/app/requirements.txt
sudo install -d -o oncology -g oncology -m 0700 /var/lib/oncology-swarm
```

Review the code and dependencies before installing. This is a root-managed virtual environment inside `/opt`, not a modification of Ubuntu's system Python. Runtime services run as `oncology` and cannot rewrite their application code. Do not copy a local Windows `.venv`, `.env`, or demonstration database into production.

## 4. Install/configure PostgreSQL and run migrations

Complete [PostgreSQL installation and migration](POSTGRESQL.md), steps 1–5. It creates the application role/database, verifies local peer authentication, disables database TCP listeners on the dedicated host, creates `/etc/oncology-swarm.env`, generates your administrator token, and runs `alembic upgrade head`.

Required production settings are:

```text
DATABASE_URL=postgresql+psycopg://oncology@/oncology?host=/var/run/postgresql
DEMO_AUTO_CREATE=0
MODE=live
ALLOWED_DOMAINS=
LLM_ENABLED=0
```

Leave the exact-host allowlist empty until the [live-source checklist](LIVE_PILOT.md) is complete. Your real `ADMIN_TOKEN` must be a unique random token, never the example or a placeholder. Environment file permissions must be root-owned `0600`. Do not leave a fallback `.env` in `/opt/oncology-swarm/app`.

## 5. Install and start the services

```bash
sudo install -o root -g root -m 0644 \
  /opt/oncology-swarm/app/deploy/oncology-api.service /etc/systemd/system/
sudo install -o root -g root -m 0644 \
  /opt/oncology-swarm/app/deploy/oncology-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oncology-api.service oncology-worker.service
sudo systemctl status oncology-api.service oncology-worker.service --no-pager
curl --fail http://127.0.0.1:8000/healthz
sudo ss -ltnp
```

The API must listen only on `127.0.0.1:8000`. PostgreSQL should have no TCP listener. A generic `/healthz` response only proves the HTTP process responds; test authenticated database operations through the interface next. The API and worker use the same external environment file. Only **one worker coordinator process** is installed. Internal worker threads are bounded by `WORKER_COUNT`.

If a service fails, read its logs before retrying or changing network settings:

```bash
sudo journalctl -u oncology-api.service -u oncology-worker.service -n 100 --no-pager
```

Do not copy logs containing tokens, private URLs, or contact data into a public issue. See [systemd execution and sandboxing](https://www.freedesktop.org/software/systemd/man/257/systemd.exec.html) for the unit hardening directives.

## 6. Open the private interface through an SSH tunnel

In local Windows PowerShell, keep this command running:

```powershell
ssh -i "$HOME\Keys\my-ec2-key.pem" -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8000:127.0.0.1:8000 ubuntu@YOUR_EC2_PUBLIC_DNS
```

Open `http://127.0.0.1:8000/` in your local browser and enter the token from the server environment file using your authorized administrative access. Traffic between your computer and EC2 travels through SSH. Do not browse to `http://YOUR_EC2_PUBLIC_DNS:8000` and do not open that port to make it work.

If local port 8000 is busy, map local 8080 instead:

```powershell
ssh -i "$HOME\Keys\my-ec2-key.pem" -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8080:127.0.0.1:8000 ubuntu@YOUR_EC2_PUBLIC_DNS
```

Then use `http://127.0.0.1:8080/`. Close the tunnel with Ctrl+C when you finish. Use the interface's **Refresh** button to see new status and jobs. Verify an authenticated status response and complete a small reviewed staging workflow before live collection.

## 7. Optional: restricted nginx over valid TLS

Skip this entire step for tunnel-only operation. You need an owned domain pointing to the instance, a valid matching certificate with its full chain, and a renewal plan. Getting a certificate is not implemented by this starter. Use your established certificate process or [Ubuntu's certificate guidance](https://ubuntu.com/server/docs/how-to/security/obtain-tls-certificates/). Do not bypass certificate warnings or send the admin token over public plaintext HTTP.

```bash
sudo apt install -y nginx
sudo install -o root -g root -m 0644 \
  /opt/oncology-swarm/app/deploy/nginx-tls.conf.example \
  /etc/nginx/sites-available/oncology-swarm
sudoedit /etc/nginx/sites-available/oncology-swarm
```

Replace the example hostname, certificate paths, and **documentation-only** reviewer IP ranges. Keep the IP allowlist narrow. The supplied paths use a common Let's Encrypt layout, but the template does not obtain a certificate. Keep the TLS private key readable only by the required privileged service.

On this new dedicated server, disable nginx's default HTTP site and enable this one:

```bash
sudo unlink /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/oncology-swarm /etc/nginx/sites-enabled/oncology-swarm
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx
```

If the `default` link is already absent, skip its unlink command. Do not alter unrelated sites on an existing shared web server. If `nginx -t` fails, fix that error before starting/reloading. Only then allow 443 from the same authorized reviewer IPs in the EC2 security group and any active host firewall. Keep 8000 and 5432 closed.

Test `https://YOUR_REAL_DOMAIN/` from an allowed client and confirm the certificate is valid. Confirm a client outside the allowlist cannot reach the interface. The bearer token is still required. Do not weaken either control to diagnose an error. Schedule certificate expiry/renewal verification through your normal operations process. See [nginx HTTPS](https://nginx.org/en/docs/http/configuring_https_servers.html) and [proxy headers](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).

## 8. Make backups and restart tests part of acceptance

Complete [operations and restore testing](OPERATIONS.md), including one restore into a separate database. Restart the two application services and confirm your jobs and review decisions remain. Test the reboot path during an approved maintenance window. Keep at least one encrypted backup off the instance and document who can restore it.

## Costs and what is not provided

No live dollar or rupee estimate is included: instance types, region, EBS storage, public IPv4, data transfer, backup location, tax, and pricing change. Review the current [EC2 pricing](https://aws.amazon.com/ec2/pricing/), [EBS pricing](https://aws.amazon.com/ebs/pricing/), [VPC pricing](https://aws.amazon.com/vpc/pricing/), and [AWS calculator](https://calculator.aws/) for your selected configuration before launch. Package installation and the deterministic extractor require no paid model/search key. The optional LLM adapter is disabled by default; if enabled, its provider charges are additional and its call ceiling is not a currency budget. Check the adapter guide and your provider account before enabling it.

This single-host starter is not high availability, a managed service, a compliance certification, or a security audit. It has no SSO, granular reviewer permissions, automated certificate issuance, or production alerting service. Database-backed host pacing and a private JSON metrics endpoint exist, but distributed throughput and live backend behavior remain unverified. Persistent database jobs survive application restarts; they do not protect against loss of the server volume without tested backups.
