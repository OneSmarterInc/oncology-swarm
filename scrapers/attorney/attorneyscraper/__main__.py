import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Healthcare Attorney Scraper")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8002)
    serve.add_argument("--db", default="results/attorneys.db")
    page = sub.add_parser("file")
    page.add_argument("html")
    page.add_argument("--url", required=True)
    page.add_argument("--organization", default="")
    crawl = sub.add_parser("crawl")
    crawl.add_argument("config")
    crawl.add_argument("--db", default="results/attorneys.db")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)
    elif args.command == "file":
        from .extract import extract

        print(
            json.dumps(
                extract(
                    Path(args.html).read_text(encoding="utf-8"),
                    args.url,
                    args.organization,
                ),
                indent=2,
            )
        )
    else:
        from .engine import Manager
        from .models import RunConfig
        from .store import Store

        store = Store(args.db)
        with store.owner:
            store.recover()
            config = RunConfig(**json.loads(Path(args.config).read_text()))
            rid = store.create(config)
            manager = Manager(store)
            manager.start(rid)
            try:
                manager.thread.join()
            except KeyboardInterrupt:
                manager.close()
            print(json.dumps(store.get(rid), indent=2))


if __name__ == "__main__":
    main()
