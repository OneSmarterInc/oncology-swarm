"""Resolve once at connect time; connect only to the validated numeric IP.

HTTP Host and TLS SNI remain the original host because HTTPcore owns TLS.
No environment proxies are used: they could resolve outside this boundary.
"""

import ipaddress
import socket
import ssl
from contextlib import contextmanager

import httpcore
import httpx


@contextmanager
def mapped_errors():
    try:
        yield
    except (
        httpcore.TimeoutException,
        httpcore.NetworkError,
        httpcore.ProtocolError,
    ) as exc:
        cls = getattr(httpx, type(exc).__name__, httpx.TransportError)
        raise cls(str(exc)) from exc


class PublicBackend(httpcore.SyncBackend):
    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        try:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise httpcore.ConnectError("DNS resolution failed") from exc
        addresses = list(dict.fromkeys(a[4][0] for a in answers))
        if not addresses or any(
            (
                not ipaddress.ip_address(ip).is_global
                or ipaddress.ip_address(ip).is_multicast
            )
            for ip in addresses
        ):
            raise httpcore.ConnectError("Host resolves to a non-public address")
        # Numeric addresses cannot be rebound on the second getaddrinfo inside connect.
        return super().connect_tcp(
            addresses[0], port, timeout, local_address, socket_options
        )


class ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        with mapped_errors():
            yield from self.stream

    def close(self):
        self.stream.close()


class PublicTransport(httpx.BaseTransport):
    def __init__(self):
        self.pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PublicBackend(),
            max_connections=8,
        )

    def handle_request(self, request):
        with mapped_errors():
            response = self.pool.handle_request(
                httpcore.Request(
                    method=request.method,
                    url=httpcore.URL(
                        scheme=request.url.raw_scheme,
                        host=request.url.raw_host,
                        port=request.url.port,
                        target=request.url.raw_path,
                    ),
                    headers=request.headers.raw,
                    content=request.stream,
                    extensions=request.extensions,
                )
            )
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=ResponseStream(response.stream),
            extensions=response.extensions,
        )

    def close(self):
        self.pool.close()
