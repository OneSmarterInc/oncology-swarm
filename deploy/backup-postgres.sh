#!/usr/bin/env bash
# Small-installation logical backup. Run as root; no credentials on command line.
# No retention deletion is performed. Copy encrypted backups off this host.
set -euo pipefail
umask 077
if [[ ${EUID} -ne 0 ]]; then
  echo "Run this backup script with sudo." >&2
  exit 1
fi
out_dir=${1:-/var/backups/oncology-swarm}
install -d -o root -g root -m 0700 "$out_dir"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
temporary=$(mktemp "$out_dir/.oncology-${timestamp}-XXXXXX")
trap 'rm -f "$temporary" "$temporary.list"' EXIT
sudo -u postgres pg_dump --dbname=oncology --format=custom --no-owner --no-acl > "$temporary"
pg_restore --list "$temporary" > "$temporary.list"
final="$out_dir/oncology-${timestamp}-$(basename "$temporary" | awk -F- '{print $NF}').dump"
mv "$temporary" "$final"
mv "$temporary.list" "$final.list"
sha256sum "$final" > "$final.sha256"
echo "Backup written: $final"
echo "Validate by restoring into a separate database; archive listing alone is not a restore test."
