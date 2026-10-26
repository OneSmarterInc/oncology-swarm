"""Run the attorney scraper from the command line: CSV/XLSX of firms in, Excel out.

    python attorney_batch.py firms.csv --out attorney-real.xlsx [--max-pages 60] [--delay 1.5]

Same pipeline as the portal upload (parse_sources -> RunConfig -> Manager -> workbook_bytes).
"""
import argparse
import json
import sys
from pathlib import Path

from attorneyscraper.engine import Manager
from attorneyscraper.export import parse_sources, workbook_bytes
from attorneyscraper.models import RunConfig
from attorneyscraper.store import Store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", help="firms list (.csv or .xlsx): Organization, URL, City, State")
    ap.add_argument("--out", default="attorney-batch.xlsx")
    ap.add_argument("--db", default="results/attorneys.db")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    src = Path(args.sources)
    sources = parse_sources(src.name, src.read_bytes())
    print(f"{len(sources)} firms: " + ", ".join(s.organization or s.url for s in sources))
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    with store.owner:
        store.recover()
        config = RunConfig(name=f"cli {src.name}", sources=[s.model_dump() for s in sources],
                           max_pages=args.max_pages, delay=args.delay)
        rid = store.create(config)
        manager = Manager(store)
        manager.start(rid)
        try:
            manager.thread.join()
        except KeyboardInterrupt:
            manager.close()
        run = store.get(rid)
        records = store.records(rid)
        Path(args.out).write_bytes(workbook_bytes(records, run, store.pages(rid), store.audit(rid)))
    print(json.dumps({"run": rid, "status": run["status"], "people": len(records),
                      "sites": [(s["host"], s["status"], s.get("reason", "")) for s in run["sites"]]}, indent=2))
    print(f"Excel: {Path(args.out).resolve()}")


if __name__ == "__main__":
    sys.exit(main())
