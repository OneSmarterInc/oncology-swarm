"""Command line: python -m oncoscraper <command> ..."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ScrapeConfig
from .crawl import Crawler
from .export import write
from .extract import extract_page
from .fetch import Fetcher
from .models import Page


def _config(a) -> ScrapeConfig:
    return ScrapeConfig(
        max_pages=a.max_pages, max_depth=a.depth, delay_seconds=a.delay, render=a.render,
        oncology_only=not a.all_specialties, use_sitemaps=not a.no_sitemap, include_subdomains=a.subdomains,
        save_html_dir=a.save_html, extra_profile_patterns=a.pattern or [],
        **({"user_agent": a.user_agent} if a.user_agent else {}),
        **({"allow_private_hosts": True} if a.allow_local else {}),
        show_browser=a.show_browser,
    ).validate()


def _print_progress(e: dict) -> None:
    if e["event"] == "page":
        mark = {"profile": "P", "listing": "L", "other": ".", "error": "x"}.get(e["type"], "?")
        extra = f"  ({e['error']})" if e.get("error") else (f"  +{e['doctors_on_page']}" if e["doctors_on_page"] else "")
        print(f"[{e['fetched']:>4}] {mark} {e['url'][:110]}{extra}   | doctors {e['doctors_total']}, oncologists {e['oncologists']}",
              flush=True)
    elif e["event"] == "sitemap":
        print(f"sitemap: {e['urls']} URLs listed", flush=True)


def _doc_lines(pr) -> None:
    print(f"\nPage type: {pr.page_type}   signals: {json.dumps(pr.signals)}")
    for d in pr.doctors:
        data = {k: v for k, v in d.to_dict().items() if v not in (None, [], {}, "")}
        print(json.dumps(data, ensure_ascii=False, indent=2))
    if pr.profile_links:
        print(f"\nProfile links ({len(pr.profile_links)}):", *pr.profile_links[:25], sep="\n  ")
    if pr.pagination_links:
        print("Next pages:", *pr.pagination_links, sep="\n  ")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="oncoscraper", description="Find oncologists on hospital websites (no API key needed).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--max-pages", type=int, default=200, help="page budget per run (default 200)")
        sp.add_argument("--depth", type=int, default=4, help="max link hops from the start URL (default 4)")
        sp.add_argument("--delay", type=float, default=1.5, help="seconds between requests to one site (default 1.5)")
        sp.add_argument("--render", choices=["never", "auto", "always"], default="auto",
                        help="use a headless browser for JavaScript pages (needs Playwright; default auto)")
        sp.add_argument("--all-specialties", action="store_true", help="keep non-oncology doctors in the main output")
        sp.add_argument("--no-sitemap", action="store_true", help="do not read sitemap.xml")
        sp.add_argument("--subdomains", action="store_true", help="also crawl other subdomains of the same site")
        sp.add_argument("--save-html", metavar="DIR", help="save every fetched page to DIR (for debugging)")
        sp.add_argument("--pattern", action="append", help="extra profile URL fragment, e.g. --pattern /consultant/")
        sp.add_argument("--user-agent", help="override the User-Agent header")
        sp.add_argument("--show-browser", action="store_true", help="open a visible browser window (if a site blocks headless)")
        sp.add_argument("--allow-local", action="store_true", help="allow localhost/intranet URLs (demo site, internal sites)")

    s = sub.add_parser("scrape", help="crawl a site (or start from any page) and export doctors")
    s.add_argument("urls", nargs="+", help="start URL(s): home page, find-a-doctor page, department page or one profile")
    s.add_argument("--out", default="oncologists.xlsx", help="output file: .xlsx, .csv or .json (default oncologists.xlsx)")
    common(s)

    b = sub.add_parser("batch", help="crawl every hospital in a CSV list and combine the results")
    b.add_argument("csv", help="hospital list, e.g. sources/india_hospitals.csv")
    b.add_argument("--out", default="india_oncologists.xlsx", help="combined Excel file (default india_oncologists.xlsx)")
    b.add_argument("--pages-per-site", type=int, default=150, help="page budget for each hospital (default 150)")
    b.add_argument("--only", help="only hospitals whose name contains this text, e.g. --only Apollo")
    b.add_argument("--no-resume", action="store_true", help="re-crawl hospitals that already finished")
    common(b)

    pg = sub.add_parser("page", help="fetch ONE page and print what was extracted (no crawling)")
    pg.add_argument("url")
    common(pg)

    f = sub.add_parser("file", help="extract from a saved .html file (offline)")
    f.add_argument("path")
    f.add_argument("--url", default="https://example.invalid/page", help="the page's original URL (helps relative links)")

    sv = sub.add_parser("serve", help="start the local web UI")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1")

    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles choke on ₹, – and similar
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if a.cmd == "scrape":
        cfg = _config(a)
        print(f"Crawling {', '.join(a.urls)}  (max {cfg.max_pages} pages, {cfg.delay_seconds}s delay, render={cfg.render})")
        crawler = Crawler(cfg, _print_progress)
        result = crawler.run(a.urls)  # Ctrl+C stops early and keeps what was found
        if result.stats.get("stopped_early"):
            print("\nStopped early; saving what was found so far.")
        path = write(result, a.out, cfg.oncology_only)
        st = result.stats
        print(f"\nDone in {st['seconds']}s: {st['pages_fetched']} pages, {st['doctors_found']} doctors, "
              f"{st['oncologists']} oncologists ({st['profiles_fetched']} with full profile pages).")
        print(f"Saved: {path.resolve()}")
        return 0
    if a.cmd == "batch":
        from .batch import read_sources, run_batch
        sources = read_sources(a.csv)
        if a.only:
            sources = [s for s in sources if a.only.lower() in s["hospital"].lower()]
        if not sources:
            print("No hospitals with a URL found in", a.csv)
            return 1
        print(f"{len(sources)} hospitals, up to {a.pages_per_site} pages each. Finished sites are saved and skipped on re-run.")

        def show(e):
            if e["event"] == "site_start":
                print(f"\n=== [{e['index']}/{e['total']}] {e['hospital']}  {' '.join(e['urls'])}", flush=True)
            elif e["event"] == "site_done":
                print(f"--- {e['hospital']}: {e['status']}, {e['pages']} pages, {e['doctors']} doctors, "
                      f"{e['oncologists']} oncologists, {e['with_email']} with email  {e['note']}", flush=True)
            else:
                _print_progress(e)
        path = run_batch(sources, a.out, _config(a), a.pages_per_site, resume=not a.no_resume, progress=show)
        print(f"\nSaved combined file: {path.resolve()}")
        return 0
    if a.cmd == "page":
        cfg = _config(a)
        fetcher = Fetcher(cfg)
        try:
            page = fetcher.get(a.url)
        finally:
            fetcher.close()
        print(f"HTTP {page.status}  final URL: {page.final_url}  rendered: {page.rendered}  {page.error or ''}")
        for origin, st in fetcher.robots_status.items():
            print(f"robots.txt at {origin}: HTTP {st}")
        if fetcher.browser_hosts:
            print(f"Fetched with a real browser (site blocks plain HTTP): {', '.join(sorted(fetcher.browser_hosts))}")
        _doc_lines(extract_page(page))
        return 0 if not page.error else 1
    if a.cmd == "file":
        html = Path(a.path).read_text(encoding="utf-8", errors="replace")
        url = a.url
        if html.startswith("<!-- source: "):
            url = html.split("-->", 1)[0][13:].strip()
        _doc_lines(extract_page(Page(url, url, 200, html)))
        return 0
    if a.cmd == "serve":
        import uvicorn
        print(f"Open http://{a.host}:{a.port}/ in your browser. Press Ctrl+C to stop.")
        uvicorn.run("oncoscraper.web:app", host=a.host, port=a.port, log_level="warning")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
