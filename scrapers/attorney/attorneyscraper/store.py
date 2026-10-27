"""SQLite durable jobs. Each finished page and its discoveries commit atomically."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import RunConfig, canonical_url, site_key, without_language
from .security import DatabaseOwner


def now():
    return datetime.now(timezone.utc).isoformat()


def _fold_listing_cards(grouped):
    """One person listed on a directory page and again on their own page is one person.

    A card on /attorneys that carries no link of its own keys to /attorneys, while the
    person's page keys to /attorneys/their-name, so the two never met: fuchsberg.com
    returned Alan Fuchsberg twice. The card is folded into the page when the same name
    on the same site has one, keeping the card's contacts and evidence.
    """
    by_person = {}
    for (run, key), person in grouped.items():
        name = re.sub(r"[^a-z]", "", str(person.get("name") or "").casefold())
        if name:
            by_person.setdefault((run, site_key(person["profile_url"]), name), []).append((run, key))
    drop = set()
    for keys in by_person.values():
        if len(keys) < 2:
            continue
        pages = [k for k in keys if grouped[k]["is_profile"]]
        cards = [k for k in keys if not grouped[k]["is_profile"]]
        if len(pages) != 1 or not cards:
            continue  # two real pages are two people, or homonyms: leave them alone
        page = grouped[pages[0]]
        for card_key in cards:
            card = grouped[card_key]
            page["contacts"] = list(
                {(c["kind"], c["value"], c["owner"], c["source_url"]): c
                 for c in page["contacts"] + card["contacts"]}.values()
            )
            page["evidence_urls"] = sorted(set(page["evidence_urls"] + card["evidence_urls"]))
            for field in ("title", "city", "state"):
                if not page.get(field) and card.get(field):
                    page[field] = card[field]
            drop.add(card_key)
    return {k: v for k, v in grouped.items() if k not in drop}


class Store:
    def __init__(self, path):
        self.cache_lock = threading.Lock()
        self.cache_version = 0
        self.record_cache = {}
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.owner = DatabaseOwner(path)
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,name TEXT,status TEXT,config TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS sites(run_id TEXT,host TEXT,organization TEXT,status TEXT DEFAULT 'queued',reason TEXT DEFAULT '',PRIMARY KEY(run_id,host),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS pages(id INTEGER PRIMARY KEY,run_id TEXT,host TEXT,url TEXT,depth INTEGER,priority INTEGER,status TEXT DEFAULT 'queued',note TEXT DEFAULT '',record_count INTEGER DEFAULT 0,elapsed REAL DEFAULT 0,UNIQUE(run_id,url),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS pages_queue ON pages(run_id,host,status,priority);
            CREATE TABLE IF NOT EXISTS observations(run_id TEXT,page_url TEXT,person_key TEXT,payload TEXT,PRIMARY KEY(run_id,page_url,person_key),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS observations_run ON observations(run_id);
            CREATE TABLE IF NOT EXISTS reviews(run_id TEXT,person_key TEXT,status TEXT,note TEXT,updated_at TEXT,PRIMARY KEY(run_id,person_key),FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,run_id TEXT,action TEXT,detail TEXT,created_at TEXT);
            """)
            db.execute("INSERT OR IGNORE INTO schema_versions VALUES(1,?)", (now(),))
            columns = {r[1] for r in db.execute("PRAGMA table_info(pages)")}
            if "updated_at" not in columns:
                db.execute("ALTER TABLE pages ADD COLUMN updated_at TEXT DEFAULT ''")
            db.execute(
                "CREATE INDEX IF NOT EXISTS pages_log ON pages(run_id,updated_at)"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
            if db.total_changes:
                with self.cache_lock:
                    self.cache_version += 1
                    self.record_cache.clear()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def recover(self):
        with self.owner, self.db() as db:
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
                    "SELECT * FROM sites WHERE run_id=? ORDER BY rowid", (rid,)
                )
            ]
            counts = {}
            for host, status, count in db.execute(
                "SELECT host,status,count(*) FROM pages WHERE run_id=? GROUP BY host,status",
                (rid,),
            ):
                counts.setdefault(host, {})[status] = count
            terminal = {"done", "error", "skipped"}
            for site in item["sites"]:
                c = counts.get(site["host"], {})
                site.update(
                    processed=sum(c.get(k, 0) for k in terminal),
                    queued=c.get("queued", 0),
                    errors=c.get("error", 0),
                    reading=c.get("fetching", 0),
                    successful=c.get("done", 0),
                    skipped=c.get("skipped", 0),
                )
            item["pages_processed"] = sum(s["processed"] for s in item["sites"])
            item["pages_queued"] = sum(s["queued"] for s in item["sites"])
            seeds = {s["url"] for s in item["config"]["sources"]}
            placeholders = ",".join("?" for _ in seeds)
            finished_seeds = db.execute(
                f"SELECT count(*) FROM pages WHERE run_id=? AND url IN ({placeholders}) AND status IN ('done','error','skipped')",
                (rid, *seeds),
            ).fetchone()[0]
            item["progress"] = {
                "starting_total": len(seeds),
                "starting_finished": finished_seeds,
                "successful": sum(s["successful"] for s in item["sites"]),
                "failed": sum(s["errors"] for s in item["sites"]),
                "skipped": sum(s["skipped"] for s in item["sites"]),
                "waiting": item["pages_queued"],
                "reading": [
                    r[0]
                    for r in db.execute(
                        "SELECT url FROM pages WHERE run_id=? AND status='fetching'",
                        (rid,),
                    )
                ],
                "discovered": sum(sum(c.values()) for c in counts.values()),
            }
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

    def runs(self, offset=0, limit=200):
        with self.db() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM runs ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
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
                    "UPDATE pages SET status='fetching',updated_at=? WHERE id=?",
                    (now(), row["id"]),
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
                "UPDATE pages SET status=?,note=?,record_count=?,elapsed=?,updated_at=? WHERE id=?",
                (
                    status,
                    note,
                    len((result or {}).get("records", [])),
                    elapsed,
                    now(),
                    page["id"],
                ),
            )
            db.execute(
                "UPDATE runs SET updated_at=? WHERE id=?", (now(), page["run_id"])
            )

    def retry_errors(self, rid):
        with self.db() as db:
            hosts = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT host FROM pages WHERE run_id=? AND status='error'",
                    (rid,),
                )
            ]
            if not hosts:
                raise ValueError("There are no failed pages to retry.")
            db.executemany(
                "UPDATE sites SET status='queued',reason='' WHERE run_id=? AND host=?",
                [(rid, h) for h in hosts],
            )
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

    def page_log(self, rid, offset=0, limit=100):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM pages WHERE run_id=? ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
                    (rid, limit, offset),
                )
            ]

    def history(self, offset=0, limit=50):
        with self.db() as db:
            total = db.execute("SELECT count(*) FROM runs").fetchone()[0]
            rows = db.execute(
                "SELECT r.*, (SELECT count(*) FROM sites s WHERE s.run_id=r.id) AS websites, (SELECT count(*) FROM pages p WHERE p.run_id=r.id AND p.status IN ('done','error','skipped')) AS pages_processed, (SELECT count(DISTINCT person_key) FROM observations o WHERE o.run_id=r.id) AS people FROM runs r ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            items = [dict(r) for r in rows]
            for item in items:
                item.pop("config", None)
        return {"total": total, "items": items}

    def delete(self, rid):
        self.get(rid)
        with self.db() as db:
            db.execute("DELETE FROM audit WHERE run_id=?", (rid,))
            db.execute("DELETE FROM runs WHERE id=?", (rid,))

    def records(self, rid=None):
        with self.cache_lock:
            version = self.cache_version
            cached = self.record_cache.get(rid)
        if cached is not None:
            return copy.deepcopy(cached)
        result = self._records(rid)
        with self.cache_lock:
            if version == self.cache_version:
                if len(self.record_cache) >= 4:
                    self.record_cache.clear()
                self.record_cache[rid] = copy.deepcopy(result)
        return result

    def known_urls(self, rid):
        with self.db() as db:
            return {
                r[0] for r in db.execute("SELECT url FROM pages WHERE run_id=?", (rid,))
            }

    def _records(self, rid=None):
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
                # Between two copies of one page, the one without a /es/ style prefix is the address to keep.
                translated = without_language(p["profile_url"]) != canonical_url(p["profile_url"]).rstrip("/")
                already_plain = without_language(old["profile_url"]) == canonical_url(old["profile_url"]).rstrip("/")
                if (p["is_profile"] or not old["is_profile"]) and not (
                    translated and already_plain and p["is_profile"] == old["is_profile"]
                ):
                    grouped[key] = p
                grouped[key]["contacts"] = list(
                    {
                        (c["kind"], c["value"], c["owner"], c["source_url"]): c
                        for c in combined
                    }.values()
                )
                grouped[key]["evidence_urls"] = evidence_urls
        grouped = _fold_listing_cards(grouped)
        owners = {}
        for (run, key), p in grouped.items():
            for c in p["contacts"]:
                if c["owner"] != "individual":
                    continue
                owners.setdefault(
                    (run, site_key(p["profile_url"]), c["kind"], c["value"]), set()
                ).add(key)
        latest = {}
        for (run, key), p in grouped.items():
            for c in p["contacts"]:
                if (
                    len(
                        owners.get(
                            (run, site_key(p["profile_url"]), c["kind"], c["value"]),
                            set(),
                        )
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
