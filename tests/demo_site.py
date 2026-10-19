"""Serve the fictional test hospital at http://127.0.0.1:8765 so you can try the scraper offline.

    python tests/demo_site.py
    python -m oncoscraper scrape http://127.0.0.1:8765/ --allow-local --delay 0
"""
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import conftest  # noqa: E402

conftest.make_site.build()
print("Demo hospital site running at http://127.0.0.1:8765/  (Ctrl+C to stop)")
ThreadingHTTPServer(("127.0.0.1", 8765), conftest.Handler).serve_forever()
