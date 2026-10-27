"""Run the attorney scraper from the command line: CSV/XLSX of firms in, Excel out.

    python attorney_batch.py firms.csv --out attorney-real.xlsx [--max-pages 60] [--delay 1.5]

Same pipeline as the portal upload (parse_sources -> RunConfig -> Manager -> workbook_bytes).
"""
import argparse
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
        pages = store.pages(rid)
        Path(args.out).write_bytes(workbook_bytes(records, run, pages, store.audit(rid)))
    print(f"\nRun {rid}: {run['status']} — {len(records)} people")
    failures = {}
    for page in pages:
        if page["status"] == "error" and page["host"] not in failures:
            failures[page["host"]] = f"{page['note']}  ({page['url']})"
    for site in run["sites"]:
        line = f"  {site['host']:<28} {site['status']:<10} {site.get('reason', '')}"
        if site["status"] in {"error", "limited"} and site["host"] in failures:
            line += f"\n      first error: {failures[site['host']]}"
        print(line)
    print(f"\nExcel: {Path(args.out).resolve()}")


if __name__ == "__main__":
    sys.exit(main())
