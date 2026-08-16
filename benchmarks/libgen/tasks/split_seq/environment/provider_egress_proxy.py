from __future__ import annotations

import json
import select
import socket
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path


class Policy:
    def __init__(self, path: Path) -> None:
        document = json.loads(path.read_text(encoding="utf-8"))
        self.provider_hosts = frozenset(
            _host(item) for item in document["provider_hosts"]
        )
        self.setup_hosts = frozenset(_host(item) for item in document["setup_hosts"])
        self._agent_phase = False
        self._lock = threading.Lock()

    def authorize(self, host: str, port: int) -> tuple[bool, str]:
        host = _host(host)
        with self._lock:
            if port != 443:
                return False, "only HTTPS port 443 is allowed"
            if host in self.provider_hosts:
                self._agent_phase = True
                return True, "provider"
            if not self._agent_phase and host in self.setup_hosts:
                return True, "setup"
            return False, "host is not allowed"

    @property
    def phase(self) -> str:
        with self._lock:
            return "agent" if self._agent_phase else "setup"


def _host(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not normalized or any(item in normalized for item in ("/", "@", "[", "]")):
        raise ValueError(f"invalid exact hostname: {value!r}")
    return normalized


class ProxyHandler(BaseHTTPRequestHandler):
    policy: Policy
    timeout = 30

    def do_CONNECT(self) -> None:  # noqa: N802 - HTTP method spelling
        try:
            host, port_text = self.path.rsplit(":", 1)
            port = int(port_text)
        except (ValueError, TypeError):
            self.send_error(400, "CONNECT target must be host:port")
            return
        allowed, reason = self.policy.authorize(host, port)
        if not allowed:
            self.log_message(
                "denied CONNECT %s phase=%s reason=%s",
                self.path,
                self.policy.phase,
                reason,
            )
            self.send_error(403, reason)
            return
        try:
            upstream = socket.create_connection(
                (_host(host), port), timeout=self.timeout
            )
        except OSError as error:
            self.send_error(502, f"provider connection failed: {error}")
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        self.close_connection = True
        self.log_message(
            "allowed CONNECT %s class=%s phase=%s",
            self.path,
            reason,
            self.policy.phase,
        )
        try:
            _tunnel(self.connection, upstream)
        finally:
            upstream.close()

    def do_GET(self) -> None:  # noqa: N802 - HTTP method spelling
        self.send_error(403, "plaintext HTTP proxying is disabled")

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET


def _tunnel(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    while True:
        readable, _, exceptional = select.select(sockets, [], sockets, 30)
        if exceptional or not readable:
            return
        for source in readable:
            destination = right if source is left else left
            data = source.recv(65536)
            if not data:
                return
            destination.sendall(data)


class ThreadingProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: provider_egress_proxy.py POLICY.json")
    ProxyHandler.policy = Policy(Path(sys.argv[1]))
    with ThreadingProxy(("0.0.0.0", 3128), ProxyHandler) as server:
        print("provider egress proxy ready", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
