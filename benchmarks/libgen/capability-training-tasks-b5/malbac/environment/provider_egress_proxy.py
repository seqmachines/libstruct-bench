from __future__ import annotations

import json
import select
import socket
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import NamedTuple


EVENT_LOG_PATH = Path("/logs/network/provider-egress.jsonl")
_EVENT_LOG_LOCK = threading.Lock()


class TunnelResult(NamedTuple):
    reason: str
    duration_sec: float
    bytes_client_to_upstream: int
    bytes_upstream_to_client: int
    error_type: str | None = None
    error_message: str | None = None


def _write_event(event: str, **fields: object) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    line = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with _EVENT_LOG_LOCK:
            EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with EVENT_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError as error:
        # Diagnostics must never become a new source of provider failures.
        print(
            json.dumps(
                {
                    "event": "event_log_error",
                    "original_event": event,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


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
    # StreamRequestHandler applies this while reading the initial proxy request.
    timeout = 30.0
    # Do not let a slow or unreachable provider hold setup open indefinitely.
    connect_timeout = 30.0
    # Long model prefills may legitimately produce no tunnel traffic for more
    # than 30 seconds. Keep established CONNECT tunnels alive independently of
    # the short request/connect timeouts above.
    tunnel_idle_timeout = 900.0

    def do_CONNECT(self) -> None:  # noqa: N802 - HTTP method spelling
        try:
            host, port_text = self.path.rsplit(":", 1)
            port = int(port_text)
        except (ValueError, TypeError):
            self.send_error(400, "CONNECT target must be host:port")
            return
        allowed, reason = self.policy.authorize(host, port)
        if not allowed:
            _write_event(
                "connect_denied",
                target=self.path,
                phase=self.policy.phase,
                reason=reason,
            )
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
                (_host(host), port), timeout=self.connect_timeout
            )
        except OSError as error:
            _write_event(
                "upstream_connect_error",
                target=self.path,
                phase=self.policy.phase,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            self.send_error(502, f"provider connection failed: {error}")
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        self.close_connection = True
        self.log_message(
            "allowed CONNECT %s class=%s phase=%s tunnel_idle_timeout_sec=%.0f",
            self.path,
            reason,
            self.policy.phase,
            self.tunnel_idle_timeout,
        )
        _write_event(
            "tunnel_opened",
            target=self.path,
            connection_class=reason,
            phase=self.policy.phase,
            tunnel_idle_timeout_sec=self.tunnel_idle_timeout,
        )
        try:
            # socket.create_connection retains its connect timeout, and
            # StreamRequestHandler sets the same timeout on the client socket.
            # Once CONNECT is established, select() owns idle detection; stale
            # socket-level timeouts must not abort a valid streaming response.
            self.connection.settimeout(None)
            upstream.settimeout(None)
            result = _tunnel(
                self.connection,
                upstream,
                idle_timeout=self.tunnel_idle_timeout,
            )
        finally:
            upstream.close()
        event = {
            "target": self.path,
            "connection_class": reason,
            "phase": self.policy.phase,
            "reason": result.reason,
            "duration_sec": result.duration_sec,
            "bytes_client_to_upstream": result.bytes_client_to_upstream,
            "bytes_upstream_to_client": result.bytes_upstream_to_client,
            "error_type": result.error_type,
            "error_message": result.error_message,
        }
        _write_event("tunnel_closed", **event)
        self.log_message(
            "closed CONNECT %s class=%s phase=%s reason=%s duration_sec=%.3f "
            "bytes_client_to_upstream=%d bytes_upstream_to_client=%d "
            "error_type=%s error_message=%s",
            self.path,
            reason,
            self.policy.phase,
            result.reason,
            result.duration_sec,
            result.bytes_client_to_upstream,
            result.bytes_upstream_to_client,
            result.error_type or "-",
            result.error_message or "-",
        )

    def do_GET(self) -> None:  # noqa: N802 - HTTP method spelling
        self.send_error(403, "plaintext HTTP proxying is disabled")

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET


def _tunnel(
    left: socket.socket,
    right: socket.socket,
    *,
    idle_timeout: float,
) -> TunnelResult:
    started = time.monotonic()
    sockets = [left, right]
    bytes_client_to_upstream = 0
    bytes_upstream_to_client = 0

    def finish(reason: str, error: Exception | None = None) -> TunnelResult:
        return TunnelResult(
            reason=reason,
            duration_sec=round(time.monotonic() - started, 3),
            bytes_client_to_upstream=bytes_client_to_upstream,
            bytes_upstream_to_client=bytes_upstream_to_client,
            error_type=type(error).__name__ if error is not None else None,
            error_message=str(error) if error is not None else None,
        )

    while True:
        try:
            readable, _, exceptional = select.select(
                sockets, [], sockets, idle_timeout
            )
        except (OSError, ValueError) as error:
            return finish("select_error", error)
        if exceptional:
            sides = [
                "client" if item is left else "upstream" for item in exceptional
            ]
            return finish("socket_exception:" + ",".join(sides))
        if not readable:
            return finish("idle_timeout")
        for source in readable:
            destination = right if source is left else left
            source_side = "client" if source is left else "upstream"
            destination_side = "upstream" if source is left else "client"
            try:
                data = source.recv(65536)
            except OSError as error:
                return finish(f"{source_side}_read_error", error)
            if not data:
                return finish(f"{source_side}_eof")
            try:
                destination.sendall(data)
            except OSError as error:
                return finish(f"{destination_side}_write_error", error)
            if source is left:
                bytes_client_to_upstream += len(data)
            else:
                bytes_upstream_to_client += len(data)


class ThreadingProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: provider_egress_proxy.py POLICY.json")
    ProxyHandler.policy = Policy(Path(sys.argv[1]))
    _write_event(
        "proxy_started",
        listen_host="0.0.0.0",
        listen_port=3128,
        request_timeout_sec=ProxyHandler.timeout,
        connect_timeout_sec=ProxyHandler.connect_timeout,
        tunnel_idle_timeout_sec=ProxyHandler.tunnel_idle_timeout,
    )
    with ThreadingProxy(("0.0.0.0", 3128), ProxyHandler) as server:
        print("provider egress proxy ready", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
