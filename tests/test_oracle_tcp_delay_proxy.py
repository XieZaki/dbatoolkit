"""Offline tests for the bounded Oracle TCP delay relay."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.acceptance.oracle_tcp_delay_proxy import (
    OracleTcpDelayProxy,
    ProxyState,
    ProxyStateError,
)


class LocalServer:
    def __init__(self, handler: Callable[[socket.socket], None]) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.host, self.port = self.listener.getsockname()
        self._handler = handler
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with client:
                client.settimeout(0.2)
                try:
                    self._handler(client)
                except (OSError, socket.timeout):
                    pass

    def close(self) -> None:
        self._stop.set()
        self.listener.close()
        self._thread.join(1.0)


def echo_handler(sock: socket.socket) -> None:
    while True:
        try:
            data = sock.recv(8192)
        except socket.timeout:
            continue
        if not data:
            return
        sock.sendall(data)


def close_handler(sock: socket.socket) -> None:
    sock.close()


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_proxy(handler: Callable[[socket.socket], None], **kwargs: object) -> tuple[
    LocalServer, OracleTcpDelayProxy,
]:
    server = LocalServer(handler)
    proxy = OracleTcpDelayProxy("127.0.0.1", int(server.port), **kwargs)
    proxy.start()
    return server, proxy


def test_transparent_bidirectional_transfer_and_large_segmenting() -> None:
    server, proxy = make_proxy(echo_handler)
    payload = bytes(range(256)) * 1024
    client = socket.create_connection(proxy.address, timeout=2.0)
    try:
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        received = bytearray()
        while True:
            data = client.recv(16384)
            if not data:
                break
            received.extend(data)
        assert bytes(received) == payload
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING), proxy.snapshot()
        trace = proxy.snapshot()
        assert trace["totals"]["client_to_server_bytes"] == len(payload)
        assert trace["totals"]["server_to_client_received_bytes"] == len(payload)
        assert trace["totals"]["server_to_client_forwarded_bytes"] == len(payload)
    finally:
        client.close()
        assert proxy.close()
        server.close()
    assert proxy.state == ProxyState.CLOSED
    assert proxy.snapshot()["cleanup"]["thread_joined"] is True
    assert proxy.snapshot()["cleanup"]["port_released"] is True


def test_gate_holds_only_server_response_and_release_restores_exact_bytes() -> None:
    server, proxy = make_proxy(echo_handler)
    client = socket.create_connection(proxy.address, timeout=2.0)
    client.settimeout(0.2)
    try:
        assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
        proxy.arm_gate("Execute")
        payload = b"reviewed-probe-response" * 100
        client.sendall(payload)
        assert wait_for(lambda: proxy.state == ProxyState.GATE_HOLDING)
        trace = proxy.snapshot()
        gate = trace["gate"]
        assert gate["request_bytes"] > 0
        assert gate["response_bytes"] > 0
        assert gate["held_bytes"] == gate["response_bytes"]
        assert gate["buffer_peak_bytes"] <= gate["buffer_limit_bytes"]
        with pytest.raises(socket.timeout):
            client.recv(1)
        proxy.release_gate(reason="offline-test")
        received = bytearray()
        while len(received) < len(payload):
            received.extend(client.recv(8192))
        assert bytes(received) == payload
        client.shutdown(socket.SHUT_WR)
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING)
    finally:
        client.close()
        assert proxy.close()
        server.close()


def test_controlled_release_waits_for_post_hold_client_bytes_and_minimum_delay() -> None:
    server, proxy = make_proxy(echo_handler, max_gate_hold_seconds=0.7)
    client = socket.create_connection(proxy.address, timeout=2.0)
    client.settimeout(2.0)
    try:
        assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
        proxy.arm_gate(
            "Fetch",
            release_after_post_hold_client_bytes=1,
            minimum_hold_seconds=0.3,
        )
        gate_armed_at = proxy.snapshot()["gate"]["armed_monotonic_ns"]
        client.sendall(b"first")
        assert wait_for(lambda: proxy.state == ProxyState.GATE_HOLDING)
        client.sendall(b"x")
        assert wait_for(
            lambda: proxy.state == ProxyState.GATE_RELEASED
            and proxy.snapshot()["gate"]["release_trigger_satisfied"],
        )
        released_at = proxy.snapshot()["gate"]["released_monotonic_ns"]
        assert released_at - gate_armed_at >= 300_000_000
        received = bytearray()
        while len(received) < 6:
            received.extend(client.recv(6 - len(received)))
        assert bytes(received) == b"firstx"
        assert wait_for(lambda: proxy.snapshot()["gate"]["forwarded_after_release_bytes"] == 6)
        gate = proxy.snapshot()["gate"]
        assert gate["forwarded_before_release_bytes"] == 0
        assert gate["held_response_forwarded_bytes"] == 5
        assert gate["armed_monotonic_ns"] <= gate["response_held_monotonic_ns"] < released_at
        assert gate["post_hold_client_bytes"] == 1
        assert gate["release_mode"] == "forward"
        assert gate["release_reason"] == "post_hold_client_bytes_and_minimum_delay"
        assert gate["timeout_observed"] is False
        proxy.mark_timeout_observed({"codes": ["DPY-4024"]})
        gate = proxy.snapshot()["gate"]
        assert proxy.state == ProxyState.TIMEOUT_OBSERVED
        assert gate["timeout_observed"] is True
        assert gate["timeout_driver_evidence"]["codes"] == ["DPY-4024"]
        events = proxy.snapshot()["events"]
        release = next(e for e in events if e["event"] == "gate_released")
        observed = next(e for e in events if e["event"] == "driver_timeout_observed_after_response_release")
        assert release["sequence"] < observed["sequence"]
    finally:
        client.close()
        assert proxy.close()
        server.close()


def test_driver_timeout_can_be_recorded_after_proxy_connection_closed() -> None:
    server, proxy = make_proxy(echo_handler)
    client = socket.create_connection(proxy.address, timeout=2.0)
    try:
        assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
        proxy.arm_gate("Execute")
        client.sendall(b"request")
        assert wait_for(lambda: proxy.state == ProxyState.GATE_HOLDING)
        client.close()
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING)
        proxy.mark_timeout_observed({"codes": ["DPY-4024"]})
        gate = proxy.snapshot()["gates"][-1]
        assert proxy.state == ProxyState.LISTENING
        assert gate["timeout_observed"] is True
        assert any(
            event.get("event") == "driver_timeout_observed_after_connection_closed"
            for event in proxy.snapshot()["events"]
        )
    finally:
        client.close()
        assert proxy.close()
        server.close()


def test_timeout_evidence_drops_held_response_and_keeps_server_stop_unconfirmed() -> None:
    server, proxy = make_proxy(echo_handler)
    client = socket.create_connection(proxy.address, timeout=2.0)
    client.settimeout(1.0)
    try:
        assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
        proxy.arm_gate("Fetch")
        client.sendall(b"fetch-round-trip")
        assert wait_for(lambda: proxy.state == ProxyState.GATE_HOLDING)
        proxy.mark_timeout_observed({"codes": ["DPY-4024"], "exception_type": "DatabaseError"})
        proxy.release_gate(discard_response=True, reason="driver-returned")
        with pytest.raises(socket.timeout):
            client.recv(1)
        client.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING)
        gate = proxy.snapshot()["gates"][0]
        assert gate["timeout_observed"] is True
        assert gate["discarded_bytes"] > 0
        assert gate["release_mode"] == "discard"
        assert proxy.snapshot()["cleanup"]["port_released"] is None
    finally:
        client.close()
        assert proxy.close()
        server.close()
    assert proxy.snapshot()["state"] == ProxyState.CLOSED


def test_response_buffer_limit_fails_closed_and_never_reports_closed_success() -> None:
    server, proxy = make_proxy(echo_handler, max_buffer_bytes=32)
    client = socket.create_connection(proxy.address, timeout=2.0)
    try:
        assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
        proxy.arm_gate("Fetch")
        client.sendall(b"x" * 256)
        assert wait_for(lambda: proxy.state == ProxyState.FAILED)
        trace = proxy.snapshot()
        assert trace["gate"]["buffer_limit_exceeded"] is True
        assert trace["failure"]["reason"] == "response_buffer_limit_exceeded"
    finally:
        client.close()
        assert not proxy.close()
        server.close()
    assert proxy.state == ProxyState.FAILED
    assert proxy.snapshot()["cleanup"]["thread_joined"] is True


def test_gate_watchdog_releases_response_and_records_no_driver_timeout() -> None:
    server, proxy = make_proxy(echo_handler, max_gate_hold_seconds=0.5)
    client = socket.create_connection(proxy.address, timeout=2.0)
    client.settimeout(2.0)
    try:
        assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
        proxy.arm_gate("Execute")
        client.sendall(b"watchdog-release")
        assert wait_for(lambda: proxy.state == ProxyState.GATE_HOLDING)
        assert client.recv(64) == b"watchdog-release"
        gate = proxy.snapshot()["gates"][0]
        assert gate["timeout_observed"] is False
        assert gate["release_mode"] == "forward"
        assert wait_for(
            lambda: proxy.snapshot()["gates"][0]["forwarded_bytes"] == len(b"watchdog-release"),
        )
        gate = proxy.snapshot()["gates"][0]
        assert gate["forwarded_bytes"] == len(b"watchdog-release")
        client.shutdown(socket.SHUT_WR)
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING)
    finally:
        client.close()
        assert proxy.close()
        server.close()


def test_close_with_active_socket_joins_worker_and_releases_listener() -> None:
    server, proxy = make_proxy(echo_handler)
    client = socket.create_connection(proxy.address, timeout=2.0)
    assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
    assert proxy.close()
    client.close()
    server.close()
    trace = proxy.snapshot()
    assert trace["state"] == ProxyState.CLOSED
    assert trace["cleanup"]["thread_joined"] is True
    assert trace["cleanup"]["port_released"] is True


def test_client_disconnect_is_recorded_and_proxy_accepts_recovery_connection() -> None:
    server, proxy = make_proxy(echo_handler)
    client = socket.create_connection(proxy.address, timeout=2.0)
    assert wait_for(lambda: proxy.state == ProxyState.TRANSPARENT)
    client.close()
    assert wait_for(lambda: proxy.state == ProxyState.LISTENING)
    assert any(event.get("side") == "client" for event in proxy.snapshot()["events"])

    recovered = socket.create_connection(proxy.address, timeout=2.0)
    try:
        recovered.sendall(b"recovery")
        assert recovered.recv(8) == b"recovery"
        recovered.shutdown(socket.SHUT_WR)
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING)
    finally:
        recovered.close()
        assert proxy.close()
        server.close()


def test_server_disconnect_is_recorded_and_cleanup_releases_listener() -> None:
    server, proxy = make_proxy(close_handler)
    client = socket.create_connection(proxy.address, timeout=2.0)
    try:
        assert client.recv(1) == b""
        client.shutdown(socket.SHUT_WR)
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING), proxy.snapshot()
        assert any(event.get("side") == "upstream" for event in proxy.snapshot()["events"])
    finally:
        client.close()
        assert proxy.close()
        server.close()
    assert proxy.snapshot()["cleanup"]["port_released"] is True


def test_upstream_failure_remains_failed_after_cleanup() -> None:
    reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reserved.bind(("127.0.0.1", 0))
    port = int(reserved.getsockname()[1])
    reserved.close()
    proxy = OracleTcpDelayProxy("127.0.0.1", port, connect_timeout_seconds=0.2)
    proxy.start()
    client = socket.create_connection(proxy.address, timeout=2.0)
    try:
        assert wait_for(lambda: proxy.state == ProxyState.FAILED)
        assert proxy.snapshot()["failure"]["reason"] == "upstream_or_relay_failed"
    finally:
        client.close()
        assert not proxy.close()
    assert proxy.state == ProxyState.FAILED
    assert proxy.snapshot()["cleanup"]["port_released"] is True


def test_upstream_must_be_exact_loopback_and_gate_stage_is_restricted() -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        OracleTcpDelayProxy("127.0.0.2", 1521)
    with pytest.raises(ValueError, match="IP address"):
        OracleTcpDelayProxy("localhost", 1521)
    server, proxy = make_proxy(echo_handler)
    try:
        with pytest.raises(ValueError, match="Execute and Fetch"):
            proxy.arm_gate("Connect")
        with pytest.raises(ProxyStateError, match="active transparent connection"):
            proxy.arm_gate("Fetch")
    finally:
        assert proxy.close()
        server.close()


def test_trace_records_counts_without_persisting_payload(tmp_path: Path) -> None:
    server, proxy = make_proxy(echo_handler, run_id="offline-run")
    client = socket.create_connection(proxy.address, timeout=2.0)
    payload = b"sensitive-oracle-net-payload"
    try:
        client.sendall(payload)
        assert client.recv(len(payload)) == payload
        client.shutdown(socket.SHUT_WR)
        assert wait_for(lambda: proxy.state == ProxyState.LISTENING)
    finally:
        client.close()
        assert proxy.close()
        server.close()

    trace_path = tmp_path / "oracle_proxy_trace.json"
    proxy.write_trace(trace_path)
    trace = trace_path.read_text(encoding="utf-8")
    assert payload.decode("ascii") not in trace
    parsed = json.loads(trace)
    assert parsed["run_id"] == "offline-run"
    assert parsed["totals"]["client_to_server_bytes"] == len(payload)
    assert parsed["totals"]["server_to_client_received_bytes"] == len(payload)
    with pytest.raises(FileExistsError):
        proxy.write_trace(trace_path)
