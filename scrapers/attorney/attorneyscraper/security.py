"""Bounded request bodies and exclusive, crash-released database ownership."""

import os
import threading
from pathlib import Path

from starlette.responses import JSONResponse


class BodyLimit:
    def __init__(self, app, limit=3_000_000):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                return await JSONResponse(
                    {"detail": "Request too large"}, status_code=413
                )(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(chunks)

        async def bounded_receive():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()

        await self.app(scope, bounded_receive, send)


class DatabaseOwner:
    """OS lock: Linux flock / Windows byte lock, released on process exit."""

    def __init__(self, path):
        self.path = Path(str(Path(path).resolve()) + ".lock")
        self.guard, self.file, self.count = threading.Lock(), None, 0

    def acquire(self):
        with self.guard:
            if self.count:
                self.count += 1
                return
            handle = self.path.open("a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    if not handle.read(1):
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise ValueError(
                    "This database is already in use by another server or crawl. Close it first or use a different --db path."
                ) from exc
            self.file, self.count = handle, 1

    def release(self):
        with self.guard:
            self.count -= 1
            if self.count == 0:
                if os.name == "nt":
                    import msvcrt

                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
                self.file.close()
                self.file = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
