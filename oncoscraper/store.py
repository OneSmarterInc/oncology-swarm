"""Local database of runs and doctors (SQLite, one file, no server).

Every crawl and batch is a run. Doctors found are saved as they come in, so the
history, the doctor directory and the dashboard survive a restart of the app.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .crawl import name_key
from .models import DoctorRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, kind TEXT, title TEXT, state TEXT, started REAL, finished REAL,
    settings TEXT, stats TEXT, xlsx TEXT, upload_name TEXT, upload BLOB, out_path TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS run_sites (run_id TEXT, row INTEGER, data TEXT, PRIMARY KEY (run_id, row));
CREATE TABLE IF NOT EXISTS doctors (
    run_id TEXT, key TEXT, source_hospital TEXT, name TEXT, hospital TEXT, city TEXT, specialty TEXT,
    is_onc INTEGER, has_email INTEGER, has_phone INTEGER, has_contact INTEGER, updated REAL, data TEXT,
    PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS doctors_key ON doctors (key, updated);
"""
CONTACT = ("phone", "email", "contact_phone", "contact_email")


def doctor_key(d: DoctorRecord) -> str:
    """Same doctor across runs: name + hospital list name (or website)."""
    where = d.source_hospital or (urlsplit(d.profile_url or d.source_url or "").hostname or "").removeprefix("www.")
    return f"{name_key(d.get('name') or '')}|{where.lower()}"


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            # Runs that were going when the app closed cannot still be going.
            self._db.execute("UPDATE runs SET state='interrupted' WHERE state IN ('running','paused')")
            self._db.commit()

    # -- runs ------------------------------------------------------------------
    def create_run(self, run_id: str, kind: str, title: str, settings: dict, upload_name: str | None = None,
                   upload: bytes | None = None, out_path: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO runs (id, kind, title, state, started, settings, stats, upload_name, upload, out_path)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, kind, title, "running", time.time(), json.dumps(settings), "{}", upload_name, upload, out_path))
            self._db.commit()

    def update_run(self, run_id: str, **fields) -> None:
        if not fields:
            return
        if "stats" in fields and not isinstance(fields["stats"], str):
            fields["stats"] = json.dumps(fields["stats"])
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
            self._db.commit()

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_dict(r) if r else None

    def list_runs(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM runs ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
            counts = dict(self._db.execute(
                "SELECT run_id, SUM(is_onc) FROM doctors GROUP BY run_id").fetchall())
        out = []
        for r in rows:
            d = self._run_dict(r)
            d["oncologists_saved"] = int(counts.get(r["id"]) or 0)
            out.append(d)
        return out

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            for table, col in (("doctors", "run_id"), ("run_sites", "run_id"), ("runs", "id")):
                self._db.execute(f"DELETE FROM {table} WHERE {col}=?", (run_id,))
            self._db.commit()

    @staticmethod
    def _run_dict(r: sqlite3.Row) -> dict:
        d = {k: r[k] for k in r.keys() if k != "upload"}
        d["settings"] = json.loads(d["settings"] or "{}")
        d["stats"] = json.loads(d["stats"] or "{}")
        d["has_upload"] = r["upload"] is not None
        return d

    def upload_of(self, run_id: str) -> tuple[str | None, bytes | None]:
        with self._lock:
            r = self._db.execute("SELECT upload_name, upload FROM runs WHERE id=?", (run_id,)).fetchone()
        return (r["upload_name"], r["upload"]) if r else (None, None)

    # -- per-hospital summary rows of a batch --------------------------------------
    def save_site(self, run_id: str, site: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO run_sites VALUES (?,?,?)", (run_id, int(site.get("row") or 0), json.dumps(site)))
            self._db.commit()

    def sites(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM run_sites WHERE run_id=? ORDER BY row", (run_id,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # -- doctors -----------------------------------------------------------------
    def save_doctors(self, run_id: str, records: list[DoctorRecord], default_hospital: str = "") -> int:
        """default_hospital names the site for a single-website crawl, so all its doctors group together."""
        now = time.time()
        rows = []
        for d in records:
            if not d.get("name"):
                continue
            if not d.source_hospital and default_hospital:
                d = DoctorRecord.from_full_dict(d.to_full_dict())
                d.source_hospital = default_hospital
            spec = d.get("specialty")
            rows.append((run_id, doctor_key(d), d.source_hospital, d.get("name"), d.get("hospital") or d.source_hospital,
                         d.get("city"), ", ".join(spec) if isinstance(spec, list) else spec,
                         int(d.is_oncology), int(bool(d.get("email"))), int(bool(d.get("phone"))),
                         int(any(d.get(f) for f in CONTACT)), now, json.dumps(d.to_full_dict(), ensure_ascii=False)))
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO doctors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._db.commit()
        return len(rows)

    def run_doctors(self, run_id: str) -> list[DoctorRecord]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM doctors WHERE run_id=?", (run_id,)).fetchall()
        return [DoctorRecord.from_full_dict(json.loads(r["data"])) for r in rows]

    def _latest_sql(self, where: str) -> str:
        # The newest copy of each doctor across all runs.
        return ("SELECT d.* FROM doctors d JOIN (SELECT key, MAX(updated) AS u FROM doctors GROUP BY key) m "
                f"ON d.key = m.key AND d.updated = m.u WHERE {where}")

    def directory(self, q: str = "", city: str = "", hospital: str = "", specialty: str = "",
                  has_email: bool = False, has_phone: bool = False, has_contact: bool = False,
                  oncologists_only: bool = True, limit: int = 100, offset: int = 0) -> tuple[int, list[dict]]:
        where, args = ["1=1"], []
        if oncologists_only:
            where.append("d.is_onc=1")
        for col, val in (("d.city", city), ("COALESCE(NULLIF(d.source_hospital,''), d.hospital)", hospital)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if specialty:
            where.append("LOWER(COALESCE(d.specialty,'')) LIKE ?")
            args.append(f"%{specialty.lower()}%")
        if q:
            where.append("LOWER(d.data) LIKE ?")
            args.append(f"%{q.lower()}%")
        for flag, col in ((has_email, "has_email"), (has_phone, "has_phone"), (has_contact, "has_contact")):
            if flag:
                where.append(f"d.{col}=1")
        sql = self._latest_sql(" AND ".join(where))
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
            rows = self._db.execute(sql + " ORDER BY d.source_hospital, d.name LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return total, [json.loads(r["data"]) | {"run_id": r["run_id"]} for r in rows]

    def directory_records(self, **filters) -> list[DoctorRecord]:
        _, rows = self.directory(**filters, limit=1_000_000, offset=0)
        return [DoctorRecord.from_full_dict(r) for r in rows]

    def facets(self) -> dict:
        sql = self._latest_sql("d.is_onc=1")
        with self._lock:
            cities = [r[0] for r in self._db.execute(f"SELECT DISTINCT city FROM ({sql}) WHERE city IS NOT NULL AND city<>'' ORDER BY city")]
            hosps = [r[0] for r in self._db.execute(
                f"SELECT DISTINCT COALESCE(NULLIF(source_hospital,''), hospital) h FROM ({sql}) WHERE h IS NOT NULL AND h<>'' ORDER BY h")]
        return {"cities": cities, "hospitals": hosps}

    def dashboard(self) -> dict:
        sql = self._latest_sql("1=1")
        with self._lock:
            t = self._db.execute(
                f"SELECT COUNT(*) all_doctors, SUM(is_onc) onc, SUM(is_onc*has_email) email, SUM(is_onc*has_phone) phone,"
                f" SUM(is_onc*has_contact) contact, COUNT(DISTINCT CASE WHEN is_onc=1 THEN COALESCE(NULLIF(source_hospital,''), hospital) END) hospitals FROM ({sql})").fetchone()
            per = self._db.execute(
                f"SELECT COALESCE(NULLIF(source_hospital,''), NULLIF(hospital,''), '(unknown)') h, SUM(is_onc) onc, SUM(is_onc*has_email) email,"
                f" SUM(is_onc*has_phone) phone, SUM(is_onc*has_contact) contact, MAX(updated) last FROM ({sql})"
                f" GROUP BY h HAVING onc > 0 ORDER BY onc DESC").fetchall()
            runs = self._db.execute("SELECT COUNT(*), SUM(state IN ('running','paused')) FROM runs").fetchone()
        return {
            "totals": {"runs": runs[0] or 0, "active_runs": runs[1] or 0, "hospitals": t["hospitals"] or 0,
                       "doctors": t["all_doctors"] or 0, "oncologists": t["onc"] or 0, "with_email": t["email"] or 0,
                       "with_phone": t["phone"] or 0, "with_contact": t["contact"] or 0},
            "hospitals": [dict(r) for r in per],
        }


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "run"
