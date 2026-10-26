import asyncio
import os
import subprocess
import sys
from unittest.mock import patch

import httpcore
import pytest
from fastapi.testclient import TestClient

from attorneyscraper.models import RunConfig
from attorneyscraper.security import BodyLimit
from attorneyscraper.store import Store
from attorneyscraper.transport import PublicBackend, PublicTransport
from attorneyscraper.web import create_app


def test_stream_limit_stops_reading_before_entire_upload():
    calls = []
    reached_app = []

    async def inner(scope, receive, send):
        reached_app.append(True)

    messages = iter(
        [
            {"type": "http.request", "body": b"x" * 8, "more_body": True},
            {"type": "http.request", "body": b"y" * 8, "more_body": True},
        ]
    )

    async def receive():
        calls.append(True)
        return next(messages)

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(
        BodyLimit(inner, limit=10)({"type": "http", "method": "POST"}, receive, send)
    )
    assert len(calls) == 2 and not reached_app
    assert sent[0]["status"] == 413


def test_small_chunked_upload_replayed():
    data = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            data.append(message["body"])
            if not message.get("more_body"):
                break

    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        pass

    asyncio.run(BodyLimit(app, 10)({"type": "http", "method": "POST"}, receive, send))
    assert b"".join(data) == b"abcdef"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "192.168.1.1",
        "::ffff:127.0.0.1",
    ],
)
def test_connection_backend_blocks_private_ip(address):
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]),
        patch.object(httpcore.SyncBackend, "connect_tcp") as connect,
    ):
        with pytest.raises(httpcore.ConnectError):
            PublicBackend().connect_tcp("evil.example", 443)
        connect.assert_not_called()


def test_connection_rebinding_after_preflight_is_blocked():
    from attorneyscraper.fetch import public_url

    answers = [[(2, 1, 6, "", ("8.8.8.8", 443))], [(2, 1, 6, "", ("127.0.0.1", 443))]]
    with patch("socket.getaddrinfo", side_effect=answers):
        public_url("https://evil.example/")
        with pytest.raises(httpcore.ConnectError):
            PublicBackend().connect_tcp("evil.example", 443)


def test_transport_preserves_host_and_tls_name():
    import httpx

    transport = PublicTransport()

    class Stream:
        def __iter__(self):
            yield b"ok"

        def close(self):
            pass

    response = httpcore.Response(200, content=Stream())
    with patch.object(
        transport.pool, "handle_request", return_value=response
    ) as handle:
        result = transport.handle_request(
            httpx.Request("GET", "https://firm.com/profile")
        )
        assert handle.call_args.args[0].url.host == b"firm.com"
        assert (b"Host", b"firm.com") in handle.call_args.args[0].headers
        assert result.read() == b"ok"
        result.close()
    transport.close()


def test_database_lock_across_processes_and_release(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    code = "from attorneyscraper.store import Store; import sys; s=Store(sys.argv[1]); s.recover()"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([os.getcwd(), env.get("PYTHONPATH", "")])
    with store.owner:
        result = subprocess.run(
            [sys.executable, "-c", code, store.path],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0 and "already in use" in result.stderr
    result = subprocess.run(
        [sys.executable, "-c", code, store.path],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_page_test_clone_history_filter_delete(tmp_path, factory):
    app = create_app(tmp_path / "api.db", factory)
    with TestClient(app) as client:
        headers = {"x-session-token": client.get("/api/session").json()["token"]}
        result = client.post(
            "/api/page-test",
            headers=headers,
            json={"url": "https://firm.com/people/jane"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["records"][0]["name"] == "Jane Smith"
        assert client.get("/api/directory").json()["total"] == 0
        rid = client.post(
            "/api/runs",
            headers=headers,
            json={"sources": [{"url": "https://firm.com/people/jane"}]},
        ).json()["id"]
        app.state.manager.thread.join(10)
        assert client.get("/api/history").json()["total"] == 1
        assert (
            client.get(
                "/api/directory", params={"q": "jsmith@firm.com", "has_phone": True}
            ).json()["total"]
            == 1
        )
        assert (
            client.get("/api/directory", params={"organization": "missing"}).json()[
                "total"
            ]
            == 0
        )
        clone = client.post(f"/api/runs/{rid}/clone", headers=headers)
        assert clone.status_code == 200, clone.text
        assert clone.json()["id"] != rid
        app.state.manager.thread.join(10)
        assert client.get("/api/history").json()["total"] == 2
        assert client.delete(f"/api/runs/{rid}", headers=headers).status_code == 200
        assert client.get(f"/api/runs/{rid}").status_code == 404
        assert client.get("/api/history").json()["total"] == 1
        assert (
            client.get("/api/summary").json()["firms"][0]["organization"] == "firm.com"
        )


def test_page_log_order_and_bound(tmp_path):
    s = Store(tmp_path / "log.db")
    rid = s.create(RunConfig(sources=[{"url": "https://firm.com/"}]))
    first = s.next_page(rid, "firm.com")
    s.finish_page(first, links=[{"url": "https://firm.com/other", "priority": 1}])
    other = s.next_page(rid, "firm.com")
    s.finish_page(other)
    s.finish_page(first)
    assert s.page_log(rid, limit=1)[0]["id"] == first["id"]


def test_retry_only_failed_site_and_no_failed_errors(tmp_path, factory):
    s = Store(tmp_path / "retry.db")
    rid = s.create(RunConfig(sources=[{"url": "https://firm.com/people/jane"}]))
    s.set_site(rid, "firm.com", "completed")
    with pytest.raises(ValueError, match="no failed"):
        s.retry_errors(rid)
    assert s.get(rid)["sites"][0]["status"] == "completed"
    with s.db() as db:
        db.execute("UPDATE pages SET status='error' WHERE run_id=?", (rid,))
    s.retry_errors(rid)
    assert s.get(rid)["sites"][0]["status"] == "queued"


def test_record_cache_invalidates_and_does_not_leak_mutation(tmp_path):
    from attorneyscraper.extract import extract

    store = Store(tmp_path / "cache.db")
    rid = store.create(RunConfig(sources=[{"url": "https://firm.com/"}]))
    assert store.records() == []
    page = store.next_page(rid, "firm.com")
    records = extract(
        "<h1>Jane Smith</h1><p>Attorney handles healthcare compliance.</p>",
        "https://firm.com/jane-smith",
    )["records"]
    store.finish_page(page, {"records": records})
    assert len(store.records()) == 1
    store.records()[0]["name"] = "Changed"
    assert store.records()[0]["name"] == "Jane Smith"
    store.delete(rid)
    assert store.records() == []


def test_https_transport_keeps_original_sni_and_host():
    import httpx

    calls = []

    class FakeStream:
        def __init__(self):
            self.data = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            assert ssl_context.check_hostname
            calls.append(("sni", server_hostname))
            return self

        def write(self, buffer, timeout=None):
            calls.append(("write", buffer))

        def read(self, max_bytes, timeout=None):
            data, self.data = self.data, b""
            return data

        def get_extra_info(self, info):
            return None

        def close(self):
            pass

    with patch.object(PublicBackend, "connect_tcp", return_value=FakeStream()):
        with httpx.Client(transport=PublicTransport(), trust_env=False) as client:
            assert client.get("https://firm.com/profile").text == "ok"
    assert ("sni", "firm.com") in calls
    assert any(b"Host: firm.com" in value for key, value in calls if key == "write")
