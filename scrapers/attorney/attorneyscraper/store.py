"""SQLite durable jobs. Each finished page and its discoveries commit atomically."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import RunConfig, site_key


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,name TEXT,status TEXT,config TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS sites(run_id TEXT,host TEXT,organization TEXT,status TEXT DEFAULT 'queued',reason TEXT DEFAULT '',PRIMARY KEY(run_id,host),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS pages(id INTEGER PRIMARY KEY,run_id TEXT,host TEXT,url TEXT,depth INTEGER,priority INTEGER,status TEXT DEFAULT 'queued',note TEXT DEFAULT '',record_count INTEGER DEFAULT 0,elapsed REAL DEFAULT 0,UNIQUE(run_id,url),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS pages_queue ON pages(run_id,host,status,priority);
            CREATE TABLE IF NOT EXISTS observations(run_id TEXT,page_url TEXT,person_key TEXT,payload TEXT,PRIMARY KEY(run_id,page_url,person_key),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS reviews(run_id TEXT,person_key TEXT,status TEXT,note TEXT,updated_at TEXT,PRIMARY KEY(run_id,person_key),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,run_id TEXT,action TEXT,detail TEXT,created_at TEXT);
            """)
            db.execute("INSERT OR IGNORE INTO schema_versions VALUES(1,?)", (now(),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def recover(self):
        with self.db() as db:
            db.execute(
                "UPDATE runs SET status='interrupted',updated_at=? WHERE status IN ('running','pausing','stopping')",
                (now(),),
            )
            db.execute("UPDATE pages SET status='queued' WHERE status='fetching'")
            db.execute("UPDATE sites SET status='interrupted' WHERE status='running'")

    def create(self, config: RunConfig):
        rid = uuid.uuid4().hex[:12]
        with self.db() as db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?)",
                (rid, config.name, "queued", config.model_dump_json(), now(), now()),
            )
            for src in config.sources:
                host = site_key(src.url)
                db.execute(
                    "INSERT OR IGNORE INTO sites(run_id,host,organization) VALUES(?,?,?)",
                    (rid, host, src.organization),
                )
                db.execute(
                    "INSERT OR IGNORE INTO pages(run_id,host,url,depth,priority) VALUES(?,?,?,?,?)",
                    (rid, host, src.url, 0, 1000),
                )
            db.execute(
                "INSERT INTO audit(run_id,action,detail,created_at) VALUES(?,?,?,?)",
                (rid, "create", config.model_dump_json(), now()),
            )
        return rid

    def get(self, rid):
        with self.db() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if not row:
                raise KeyError(rid)
            item = dict(row)
            item["config"] = json.loads(item["config"])
            # Keep run state, progress counts and frontier in one snapshot.
            item["sites"] = [
                dict(r)
                for r in db.execute(
                    "SELECT s.*, (SELECT count(*) FROM pages p WHERE p.run_id=s.run_id AND p.host=s.host AND p.status IN ('done','error','skipped')) AS processed, (SELECT count(*) FROM pages p WHERE p.run_id=s.run_id AND p.host=s.host AND p.status='queued') AS queued, (SELECT count(*) FROM pages p WHERE p.run_id=s.run_id AND p.host=s.host AND p.status='error') AS errors FROM sites s WHERE run_id=?",
                    (rid,),
                )
            ]
            item["pages_processed"] = sum(s["processed"] for s in item["sites"])
            item["pages_queued"] = sum(s["queued"] for s in item["sites"])
            pages = [
                dict(p)
                for p in db.execute(
                    "SELECT host,url,status FROM pages WHERE run_id=?", (rid,)
                )
            ]
            seeds = {s["url"] for s in item["config"]["sources"]}
            terminal = {"done", "error", "skipped"}
            item["progress"] = {
                "starting_total": len(seeds),
                "starting_finished": sum(
                    p["url"] in seeds and p["status"] in terminal for p in pages
                ),
                "successful": sum(p["status"] == "done" for p in pages),
                "failed": sum(p["status"] == "error" for p in pages),
                "skipped": sum(p["status"] == "skipped" for p in pages),
                "waiting": sum(p["status"] == "queued" for p in pages),
                "reading": [p["url"] for p in pages if p["status"] == "fetching"],
                "discovered": len(pages),
            }
            for site in item["sites"]:
                subset = [p for p in pages if p["host"] == site["host"]]
                site["reading"] = sum(p["status"] == "fetching" for p in subset)
                site["successful"] = sum(p["status"] == "done" for p in subset)
                site["skipped"] = sum(p["status"] == "skipped" for p in subset)
            # A fixed per-site budget keeps discovery from moving progress backwards.
            # Finished sites consume their share even if they exhaust links early.
            budget = item["config"]["max_pages"]
            sites = item["sites"]
            units = sum(
                budget
                if site["status"] in {"completed", "limited", "error"}
                else min(site["processed"], budget)
                for site in sites
            )
            finished = item["status"] in {"completed", "completed_with_issues"}
            item["progress"]["percent"] = (
                100.0
                if finished
                else min(99.0, round(100 * units / max(1, budget * len(sites)), 1))
            )
            return item

    def runs(self):
        with self.db() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM runs ORDER BY created_at DESC LIMIT 200"
                )
            ]
        return [self.get(rid) for rid in ids]

    def set_status(self, rid, status):
        with self.db() as db:
            db.execute(
                "UPDATE runs SET status=?,updated_at=? WHERE id=?", (status, now(), rid)
            )
            db.execute(
                "INSERT INTO audit(run_id,action,detail,created_at) VALUES(?,?,?,?)",
                (rid, "status", status, now()),
            )

    def set_site(self, rid, host, status, reason=""):
        with self.db() as db:
            db.execute(
                "UPDATE sites SET status=?,reason=? WHERE run_id=? AND host=?",
                (status, reason, rid, host),
            )

    def next_page(self, rid, host):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM pages WHERE run_id=? AND host=? AND status='queued' ORDER BY priority DESC,id LIMIT 1",
                (rid, host),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE pages SET status='fetching' WHERE id=?", (row["id"],)
                )
            return dict(row) if row else None

    def finish_page(
        self, page, result=None, status="done", note="", elapsed=0, links=()
    ):
        with self.db() as db:
            for record in (result or {}).get("records", []):
                db.execute(
                    "INSERT OR REPLACE INTO observations VALUES(?,?,?,?)",
                    (page["run_id"], page["url"], record["key"], json.dumps(record)),
                )
            for link in links:
                db.execute(
                    "INSERT OR IGNORE INTO pages(run_id,host,url,depth,priority) VALUES(?,?,?,?,?)",
                    (
                        page["run_id"],
                        page["host"],
                        link["url"],
                        page["depth"] + 1,
                        link["priority"],
                    ),
                )
            db.execute(
                "UPDATE pages SET status=?,note=?,record_count=?,elapsed=? WHERE id=?",
                (
                    status,
                    note,
                    len((result or {}).get("records", [])),
                    elapsed,
                    page["id"],
                ),
            )
            db.execute(
                "UPDATE runs SET updated_at=? WHERE id=?", (now(), page["run_id"])
            )

    def retry_errors(self, rid):
        with self.db() as db:
            db.execute(
                "UPDATE pages SET status='queued',note='' WHERE run_id=? AND status='error'",
                (rid,),
            )

    def pages(self, rid):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM pages WHERE run_id=? ORDER BY id", (rid,)
                )
            ]

    def records(self, rid=None):
        with self.db() as db:
            rows = db.execute(
                "SELECT o.*,r.created_at FROM observations o JOIN runs r ON r.id=o.run_id "
                + ("WHERE o.run_id=? " if rid else "")
                + "ORDER BY r.created_at,o.page_url",
                (rid,) if rid else (),
            ).fetchall()
            reviews = {
                (r["run_id"], r["person_key"]): dict(r)
                for r in db.execute("SELECT * FROM reviews")
            }
        grouped = {}
        for row in rows:
            p = json.loads(row["payload"])
            # Hide legacy name-only widget observations without deleting stored evidence.
            if (
                not p["is_profile"]
                and p["profile_url"] == p["source_url"]
                and not p.get("attorney_evidence")
                and not p["categories"]
                and not p["contacts"]
                and (row["run_id"], p["key"]) not in reviews
            ):
                continue
            key = (row["run_id"], p["key"])
            p["run_id"] = row["run_id"]
            if key not in grouped:
                p["evidence_urls"] = [p["source_url"]]
                grouped[key] = p
            else:
                old = grouped[key]
                combined = old["contacts"] + p["contacts"]
                evidence_urls = sorted(set(old["evidence_urls"] + [p["source_url"]]))
                # Profile evidence outranks listing evidence. Do not union firm-wide specialties.
                if p["is_profile"] or not old["is_profile"]:
                    grouped[key] = p
                grouped[key]["contacts"] = list(
                    {
                        (c["kind"], c["value"], c["owner"], c["source_url"]): c
                        for c in combined
                    }.values()
                )
                grouped[key]["evidence_urls"] = evidence_urls
        owners = {}
        for (run, key), p in grouped.items():
            for c in p["contacts"]:
                owners.setdefault(
                    (run, site_key(p["profile_url"]), c["kind"], c["value"]), set()
                ).add(key)
        latest = {}
        for (run, key), p in grouped.items():
            for c in p["contacts"]:
                if (
                    len(
                        owners[(run, site_key(p["profile_url"]), c["kind"], c["value"])]
                    )
                    > 1
                    and c["owner"] == "individual"
                ):
                    c["owner"] = "organization"
                    c["ownership_note"] = "Published for multiple people on this site"
            p["automatic_status"] = p["status"]
            review = reviews.get((run, key))
            if review:
                p["status"] = review["status"]
                p["review"] = review
            latest[key] = p
        return sorted(
            latest.values(),
            key=lambda p: (p["organization"].casefold(), p["name"].casefold()),
        )

    def review(self, rid, key, status, note):
        if not any(r["key"] == key for r in self.records(rid)):
            raise KeyError(key)
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?)",
                (rid, key, status, note, now()),
            )
            db.execute(
                "INSERT INTO audit(run_id,action,detail,created_at) VALUES(?,?,?,?)",
                (
                    rid,
                    "review",
                    json.dumps({"key": key, "status": status, "note": note}),
                    now(),
                ),
            )

    def audit(self, rid):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM audit WHERE run_id=? ORDER BY id", (rid,)
                )
            ]
