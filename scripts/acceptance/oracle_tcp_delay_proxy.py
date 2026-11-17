"""Bounded loopback TCP relay used only by the Oracle acceptance harness.

The relay never parses or persists Oracle Net payloads. It can hold one bounded
server-to-client chunk after an explicitly armed acceptance operation.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import select
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class ProxyStateError(RuntimeError):
    """Raised when a proxy operation is invalid for its current state."""


class ProxyState:
    CREATED = "CREATED"
    LISTENING = "LISTENING"
    CONNECTED = "CONNECTED"
    TRANSPARENT = "TRANSPARENT"
    GATE_ARMED = "GATE_ARMED"
    GATE_HOLDING = "GATE_HOLDING"
    TIMEOUT_OBSERVED = "TIMEOUT_OBSERVED"
    GATE_RELEASED = "GATE_RELEASED"
    CLEANING = "CLEANING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    ProxyState.CREATED: frozenset({ProxyState.LISTENING, ProxyState.CLEANING, ProxyState.FAILED}),
    ProxyState.LISTENING: frozenset({ProxyState.CONNECTED, ProxyState.CLEANING, ProxyState.FAILED}),
    ProxyState.CONNECTED: frozenset({ProxyState.TRANSPARENT, ProxyState.CLEANING, ProxyState.FAILED}),
    ProxyState.TRANSPARENT: frozenset({
        ProxyState.GATE_ARMED, ProxyState.LISTENING, ProxyState.CLEANING, ProxyState.FAILED,
    }),
    ProxyState.GATE_ARMED: frozenset({
        ProxyState.GATE_HOLDING, ProxyState.TIMEOUT_OBSERVED, ProxyState.GATE_RELEASED,
        ProxyState.LISTENING, ProxyState.CLEANING, ProxyState.FAILED,
    }),
    ProxyState.GATE_HOLDING: frozenset({
        ProxyState.TIMEOUT_OBSERVED, ProxyState.GATE_RELEASED,
        ProxyState.LISTENING, ProxyState.CLEANING, ProxyState.FAILED,
    }),
    ProxyState.TIMEOUT_OBSERVED: frozenset({
        ProxyState.GATE_RELEASED, ProxyState.LISTENING, ProxyState.CLEANING, ProxyState.FAILED,
    }),
    ProxyState.GATE_RELEASED: frozenset({
        ProxyState.GATE_ARMED, ProxyState.TIMEOUT_OBSERVED, ProxyState.LISTENING, ProxyState.CONNECTED,
        ProxyState.CLEANING, ProxyState.FAILED,
    }),
    ProxyState.CLEANING: frozenset({ProxyState.CLOSED, ProxyState.FAILED}),
    ProxyState.CLOSED: frozenset(),
    ProxyState.FAILED: frozenset(),
}


class OracleTcpDelayProxy:
    """One-client-at-a-time TCP relay with a controllable response gate.

    Only 127.0.0.1 is accepted as an upstream. The listener also binds only to
    127.0.0.1. The proxy keeps one bounded response chunk in memory at a time;
    all other traffic is forwarded as an opaque byte stream.
    """

    LISTEN_HOST = "127.0.0.1"
    MAX_CHUNK_BYTES = 16 * 1024
    MAX_TRACE_EVENTS = 20_000
    SELECT_INTERVAL_SECONDS = 0.05

    def __init__(
        self,
        upstream_host: str,
        upstream_port: int,
        *,
        max_buffer_bytes: int = 1_048_576,
        connect_timeout_seconds: float = 2.0,
        max_gate_hold_seconds: float = 6.0,
        run_id: str | None = None,
    ) -> None:
        try:
            address = ipaddress.ip_address(upstream_host)
        except ValueError as exc:
            raise ValueError("Oracle TCP proxy upstream must be an IP address.") from exc
        if str(address) != "127.0.0.1":
            raise ValueError("Oracle TCP proxy upstream must be 127.0.0.1.")
        if type(upstream_port) is not int or not 1 <= upstream_port <= 65535:
            raise ValueError("Oracle TCP proxy upstream port is invalid.")
        if type(max_buffer_bytes) is not int or max_buffer_bytes < 1:
            raise ValueError("Oracle TCP proxy response buffer limit must be positive.")
        if not 0.1 <= connect_timeout_seconds <= 5.0:
            raise ValueError("Oracle TCP proxy connect timeout is outside its safe bound.")
        if not 0.5 <= max_gate_hold_seconds <= 30.0:
            raise ValueError("Oracle TCP proxy gate duration is outside its safe bound.")

        self.upstream_host = str(address)
        self.upstream_port = upstream_port
        self.max_buffer_bytes = max_buffer_bytes
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_gate_hold_seconds = max_gate_hold_seconds
        self.run_id = run_id or uuid.uuid4().hex

        self._lock = threading.RLock()
        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._upstream: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state = ProxyState.CREATED
        self._listen_port: int | None = None
        self._failure: dict[str, str] | None = None
        self._cleanup: dict[str, Any] = {
            "listener_closed": False,
            "client_closed": False,
            "upstream_closed": False,
            "thread_joined": False,
            "port_released": None,
            "success": False,
        }
        self._totals = {
            "client_to_server_bytes": 0,
            "server_to_client_received_bytes": 0,
            "server_to_client_forwarded_bytes": 0,
            "server_to_client_discarded_bytes": 0,
        }
        self._gate: dict[str, Any] | None = None
        self._gates: list[dict[str, Any]] = []
        self._held_response = bytearray()
        self._expected_connection_abort = False
        self._events: list[dict[str, Any]] = []
        self._events_truncated = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def listen_port(self) -> int:
        with self._lock:
            if self._listen_port is None:
                raise ProxyStateError("The Oracle TCP proxy has not started.")
            return self._listen_port

    @property
    def address(self) -> tuple[str, int]:
        return self.LISTEN_HOST, self.listen_port

    def _event_locked(self, event: str, **fields: Any) -> None:
        if len(self._events) >= self.MAX_TRACE_EVENTS:
            self._events_truncated = True
            return
        self._events.append({
            "sequence": len(self._events) + 1,
            "monotonic_ns": time.monotonic_ns(),
            "state": self._state,
            "event": event,
            **fields,
        })

    def _transition_locked(self, state: str, event: str, **fields: Any) -> None:
        if state == self._state:
            self._event_locked(event, **fields)
            return
        if state not in _ALLOWED_TRANSITIONS[self._state]:
            self._failure = {"exception_type": "ProxyStateError", "reason": "invalid_transition"}
            self._state = ProxyState.FAILED
            self._stop.set()
            self._event_locked("state_transition_rejected", requested_state=state, **fields)
            raise ProxyStateError(f"Invalid proxy state transition to {state}.")
        previous = self._state
        self._state = state
        self._event_locked(event, previous_state=previous, **fields)

    def start(self) -> None:
        with self._lock:
            if self._state != ProxyState.CREATED:
                raise ProxyStateError("The Oracle TCP proxy can only be started once.")
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind((self.LISTEN_HOST, 0))
                listener.listen(1)
                listener.settimeout(self.SELECT_INTERVAL_SECONDS)
                self._listener = listener
                self._listen_port = int(listener.getsockname()[1])
                self._transition_locked(
                    ProxyState.LISTENING,
                    "listener_started",
                    listen_host=self.LISTEN_HOST,
                    listen_port=self._listen_port,
                    upstream_host=self.upstream_host,
                    upstream_port=self.upstream_port,
                )
                self._thread = threading.Thread(
                    target=self._accept_loop,
                    name=f"oracle-tcp-delay-proxy-{self.run_id[:8]}",
                    daemon=True,
                )
                self._thread.start()
            except Exception as exc:
                listener.close()
                self._cleanup["listener_closed"] = True
                self._failure = {"exception_type": type(exc).__name__, "reason": "listener_start_failed"}
                self._transition_locked(ProxyState.FAILED, "listener_start_failed")
                raise

    def wait_quiescent(
        self, *, quiet_period_seconds: float = 0.1, timeout_seconds: float = 0.5,
    ) -> dict[str, Any]:
        if not 0.01 <= quiet_period_seconds <= 0.5 or not quiet_period_seconds <= timeout_seconds <= 2.0:
            raise ValueError("Oracle proxy quiescence bounds are invalid.")
        started = time.monotonic()
        stable_since = started
        previous: tuple[int, int, int] | None = None
        readable_at_end = False
        while time.monotonic() - started <= timeout_seconds:
            with self._lock:
                upstream = self._upstream
                totals = (
                    self._totals["client_to_server_bytes"],
                    self._totals["server_to_client_received_bytes"],
                    self._totals["server_to_client_forwarded_bytes"],
                )
                eligible = self._state == ProxyState.TRANSPARENT and not self._held_response
            if not eligible or upstream is None:
                return {
                    "confirmed": False,
                    "reason": "connection_not_transparent_or_response_buffered",
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "upstream_readable_at_end": None,
                }
            try:
                readable, _, exceptional = select.select([upstream], [], [upstream], 0)
            except (OSError, ValueError):
                readable, exceptional = [upstream], []
            if exceptional or readable:
                readable_at_end = True
                stable_since = time.monotonic()
                previous = totals
            elif totals != previous:
                previous = totals
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= quiet_period_seconds:
                return {
                    "confirmed": True,
                    "reason": "proxy_socket_quiet_window_only",
                    "scope": "local_socket_observation_only",
                    "not_proof_of_no_inflight_or_server_work": True,
                    "quiet_period_ms": quiet_period_seconds * 1000,
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "upstream_readable_at_end": False,
                    "byte_totals": {
                        "client_to_server": totals[0],
                        "server_to_client_received": totals[1],
                        "server_to_client_forwarded": totals[2],
                    },
                }
            else:
                readable_at_end = False
            time.sleep(0.01)
        return {
            "confirmed": False,
            "reason": "quiescence_timeout",
            "scope": "local_socket_observation_only",
            "not_proof_of_no_inflight_or_server_work": True,
            "quiet_period_ms": quiet_period_seconds * 1000,
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "upstream_readable_at_end": readable_at_end,
        }

    def arm_gate(
        self,
        stage: str,
        *,
        quiescence: dict[str, Any] | None = None,
        release_after_post_hold_client_bytes: int | None = None,
        minimum_hold_seconds: float = 0.0,
    ) -> None:
        if stage not in {"Execute", "Fetch"}:
            raise ValueError("Only Execute and Fetch gates are supported.")
        if release_after_post_hold_client_bytes is not None and (
            type(release_after_post_hold_client_bytes) is not int
            or not 1 <= release_after_post_hold_client_bytes <= 16_384
        ):
            raise ValueError("The controlled release byte threshold is invalid.")
        if not 0.0 <= minimum_hold_seconds < self.max_gate_hold_seconds:
            raise ValueError("The controlled release delay is outside the gate lifetime.")
        if release_after_post_hold_client_bytes is None and minimum_hold_seconds != 0:
            raise ValueError("A minimum release delay requires a client-byte threshold.")
        with self._lock:
            if self._state not in {ProxyState.TRANSPARENT, ProxyState.GATE_RELEASED}:
                raise ProxyStateError("The response gate requires an active transparent connection.")
            if self._gate is not None and self._gate.get("armed"):
                raise ProxyStateError("A response gate is already armed.")
            self._held_response.clear()
            armed_at = time.monotonic_ns()
            self._gate = {
                "stage": stage,
                "armed": True,
                "pre_gate_quiescence": copy.deepcopy(quiescence),
                "armed_monotonic_ns": armed_at,
                "released_monotonic_ns": None,
                "release_mode": None,
                "release_reason": None,
                "release_after_post_hold_client_bytes": release_after_post_hold_client_bytes,
                "minimum_hold_ms": minimum_hold_seconds * 1000,
                "minimum_release_monotonic_ns": (
                    armed_at + int(minimum_hold_seconds * 1_000_000_000)
                ),
                "post_hold_client_bytes": 0,
                "release_trigger_satisfied": False,
                "timeout_observed": False,
                "timeout_driver_evidence": None,
                "request_bytes": 0,
                "response_bytes": 0,
                "held_bytes": 0,
                "forwarded_bytes": 0,
                "forwarded_before_release_bytes": 0,
                "forwarded_after_release_bytes": 0,
                "held_response_forwarded_bytes": 0,
                "response_held_monotonic_ns": None,
                "discarded_bytes": 0,
                "buffer_peak_bytes": 0,
                "buffer_limit_bytes": self.max_buffer_bytes,
                "buffer_limit_exceeded": False,
                "hold_deadline_monotonic_ns": (
                    time.monotonic_ns() + int(self.max_gate_hold_seconds * 1_000_000_000)
                ),
            }
            self._gates.append(self._gate)
            self._transition_locked(ProxyState.GATE_ARMED, "gate_armed", stage=stage)

    def mark_timeout_observed(self, driver_error: dict[str, Any]) -> None:
        with self._lock:
            gate = self._gate
            if gate is None and self._gates:
                gate = self._gates[-1]
            if gate is None or self._state not in {
                ProxyState.GATE_ARMED, ProxyState.GATE_HOLDING,
                ProxyState.GATE_RELEASED, ProxyState.LISTENING,
            }:
                raise ProxyStateError("No response gate is available for timeout evidence.")
            if gate.get("release_mode") == "discard":
                raise ProxyStateError("A discarded response gate cannot confirm a later driver timeout.")
            gate["timeout_observed"] = True
            gate["timeout_observed_monotonic_ns"] = time.monotonic_ns()
            gate["timeout_driver_evidence"] = copy.deepcopy(driver_error)
            if self._state == ProxyState.GATE_RELEASED:
                self._transition_locked(
                    ProxyState.TIMEOUT_OBSERVED,
                    "driver_timeout_observed_after_response_release",
                    stage=gate["stage"],
                    driver_codes=driver_error.get("codes", []),
                )
            elif self._state in {ProxyState.GATE_ARMED, ProxyState.GATE_HOLDING}:
                self._transition_locked(
                    ProxyState.TIMEOUT_OBSERVED,
                    "driver_timeout_observed",
                    stage=gate["stage"],
                    driver_codes=driver_error.get("codes", []),
                )
            elif self._state == ProxyState.LISTENING:
                self._event_locked(
                    "driver_timeout_observed_after_connection_closed",
                    stage=gate["stage"],
                    driver_codes=driver_error.get("codes", []),
                )

    def release_gate(self, *, discard_response: bool = False, reason: str = "harness") -> None:
        with self._lock:
            if self._gate is None or not self._gate.get("armed"):
                raise ProxyStateError("No armed response gate can be released.")
            if self._state == ProxyState.FAILED:
                raise ProxyStateError("A failed proxy cannot release its response gate.")
            held = len(self._held_response)
            self._gate["armed"] = False
            self._gate["released_monotonic_ns"] = time.monotonic_ns()
            self._gate["release_mode"] = "discard" if discard_response else "forward"
            self._gate["release_reason"] = reason
            self._gate["discard_responses_after_release"] = discard_response
            self._gate["discarded_bytes"] += held if discard_response else 0
            if discard_response:
                self._totals["server_to_client_discarded_bytes"] += held
                self._held_response.clear()
            self._transition_locked(
                ProxyState.GATE_RELEASED,
                "gate_released",
                stage=self._gate["stage"],
                release_mode=self._gate["release_mode"],
                buffered_bytes=held,
                reason=reason,
            )
    def _close_active_pair(self, *, expected: bool) -> None:
        with self._lock:
            client, upstream = self._client, self._upstream
            self._client = None
            self._upstream = None
            if expected:
                self._expected_connection_abort = True
                self._event_locked("test_connection_aborted_after_driver_return")
        for label, sock in (("client", client), ("upstream", upstream)):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError as exc:
                self._fail(exc, f"{label}_close_failed")
            else:
                with self._lock:
                    self._cleanup[f"{label}_closed"] = True

    def _fail(self, exc: BaseException, reason: str) -> None:
        with self._lock:
            if self._state in {ProxyState.CLOSED, ProxyState.FAILED}:
                return
            self._failure = {"exception_type": type(exc).__name__, "reason": reason}
            previous = self._state
            self._state = ProxyState.FAILED
            self._stop.set()
            self._event_locked("proxy_failed", previous_state=previous, **self._failure)
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self._close_active_pair(expected=False)

    def _record_bytes(self, direction: str, size: int) -> None:
        with self._lock:
            if direction == "CLIENT_TO_SERVER":
                self._totals["client_to_server_bytes"] += size
                if self._gate is not None and self._gate["armed"]:
                    self._gate["request_bytes"] += size
                    if self._held_response:
                        self._gate["post_hold_client_bytes"] += size
                        self._event_locked(
                            "client_bytes_after_response_held",
                            direction=direction,
                            byte_count=size,
                            stage=self._gate["stage"],
                        )
            else:
                self._totals["server_to_client_received_bytes"] += size
                if self._gate is not None and self._gate.get("request_bytes", 0) > 0:
                    self._gate["response_bytes"] += size
            self._event_locked("bytes", direction=direction, byte_count=size,
                               gate_stage=self._gate.get("stage") if self._gate else None)

    def _serve_connection(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=self.connect_timeout_seconds,
            )
            client.settimeout(1.0)
            upstream.settimeout(1.0)
            with self._lock:
                if self._stop.is_set():
                    return
                self._client = client
                self._upstream = upstream
                self._expected_connection_abort = False
                self._cleanup["client_closed"] = False
                self._cleanup["upstream_closed"] = False
                self._transition_locked(ProxyState.CONNECTED, "connection_established")
                self._transition_locked(ProxyState.TRANSPARENT, "transparent_forwarding_started")
            self._relay(client, upstream)
        except Exception as exc:
            with self._lock:
                expected_abort = self._expected_connection_abort
            if not self._stop.is_set() and not expected_abort:
                self._fail(exc, "upstream_or_relay_failed")
        finally:
            for label, sock in (("client", client), ("upstream", upstream)):
                if sock is None:
                    continue
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError as exc:
                    if not self._stop.is_set():
                        self._fail(exc, f"{label}_close_failed")
                else:
                    with self._lock:
                        self._cleanup[f"{label}_closed"] = True
                        if self._client is sock:
                            self._client = None
                        if self._upstream is sock:
                            self._upstream = None
            with self._lock:
                if self._state not in {ProxyState.FAILED, ProxyState.CLEANING, ProxyState.CLOSED}:
                    self._held_response.clear()
                    self._gate = None
                    self._expected_connection_abort = False
                    self._transition_locked(ProxyState.LISTENING, "connection_closed")

    def _relay(self, client: socket.socket, upstream: socket.socket) -> None:
        client_open = True
        upstream_open = True
        while not self._stop.is_set() and (client_open or upstream_open):
            with self._lock:
                held = bool(self._held_response)
                hold_deadline = (
                    int(self._gate["hold_deadline_monotonic_ns"])
                    if self._gate and self._gate.get("armed") else None
                )
                release_mode = (
                    self._gate.get("release_mode")
                    if self._gate and not self._gate.get("armed") else None
                )
                gate_stage = self._gate.get("stage") if self._gate else None
                release_threshold = (
                    self._gate.get("release_after_post_hold_client_bytes")
                    if self._gate and self._gate.get("armed") else None
                )
                release_threshold_met = (
                    isinstance(release_threshold, int)
                    and self._gate is not None
                    and self._gate.get("post_hold_client_bytes", 0) >= release_threshold
                )
                minimum_release_at = (
                    int(self._gate["minimum_release_monotonic_ns"])
                    if self._gate and self._gate.get("armed") else None
                )
                controlled_release_due = (
                    held and release_threshold_met and minimum_release_at is not None
                    and time.monotonic_ns() >= minimum_release_at
                )
            if controlled_release_due:
                with self._lock:
                    if self._gate is not None and self._gate.get("armed") and not self._stop.is_set():
                        self._gate["release_trigger_satisfied"] = True
                        self.release_gate(
                            discard_response=False,
                            reason="post_hold_client_bytes_and_minimum_delay",
                        )
                continue
            if held and release_mode == "discard":
                return
            if held and release_mode == "forward":
                with self._lock:
                    payload = bytes(self._held_response)
                    self._held_response.clear()
                self._send_payload(client, payload, "SERVER_TO_CLIENT", held_response=True)
                continue
            if hold_deadline is not None and time.monotonic_ns() >= hold_deadline:
                with self._lock:
                    if self._gate is not None and self._gate.get("armed"):
                        self._gate["armed"] = False
                        self._gate["released_monotonic_ns"] = time.monotonic_ns()
                        self._gate["release_mode"] = "forward"
                        self._gate["release_reason"] = "max_gate_hold_elapsed"
                        self._transition_locked(
                            ProxyState.GATE_RELEASED,
                            "gate_watchdog_released",
                            stage=gate_stage,
                            reason="max_gate_hold_elapsed",
                        )
                continue

            readable: list[socket.socket] = []
            if client_open:
                readable.append(client)
            if upstream_open and not held:
                readable.append(upstream)
            if not readable:
                return
            try:
                ready, _, exceptional = select.select(
                    readable, [], readable, self.SELECT_INTERVAL_SECONDS,
                )
            except (OSError, ValueError):
                with self._lock:
                    if self._stop.is_set() or self._expected_connection_abort:
                        return
                raise
            if exceptional:
                raise OSError("Socket exception reported by select.")
            if not ready:
                continue
            for source in ready:
                if self._stop.is_set():
                    return
                if source is client:
                    direction = "CLIENT_TO_SERVER"
                    destination = upstream
                    try:
                        payload = client.recv(self.MAX_CHUNK_BYTES)
                    except socket.timeout:
                        continue
                    if not payload:
                        client_open = False
                        self._event("socket_closed", side="client")
                        try:
                            upstream.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue
                else:
                    direction = "SERVER_TO_CLIENT"
                    destination = client
                    try:
                        payload = upstream.recv(min(self.MAX_CHUNK_BYTES, self.max_buffer_bytes + 1))
                    except socket.timeout:
                        continue
                    if not payload:
                        upstream_open = False
                        self._event("socket_closed", side="upstream")
                        try:
                            client.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue
                self._record_bytes(direction, len(payload))
                if direction == "SERVER_TO_CLIENT":
                    with self._lock:
                        discard_response = (
                            self._gate is not None
                            and bool(self._gate.get("discard_responses_after_release"))
                            and self._gate.get("request_bytes", 0) > 0
                        )
                        if discard_response and self._gate is not None:
                            self._gate["discarded_bytes"] += len(payload)
                            self._totals["server_to_client_discarded_bytes"] += len(payload)
                            self._event_locked(
                                "server_response_discarded_after_driver_return",
                                direction=direction,
                                byte_count=len(payload),
                            )
                    if discard_response:
                        continue
                if direction == "SERVER_TO_CLIENT":
                    with self._lock:
                        if self._gate is not None and self._gate.get("armed") \
                                and self._gate.get("request_bytes", 0) > 0:
                            if len(payload) > self.max_buffer_bytes:
                                self._gate["buffer_limit_exceeded"] = True
                                self._fail_locked("response_buffer_limit_exceeded")
                                return
                            self._held_response.extend(payload)
                            size = len(self._held_response)
                            self._gate["held_bytes"] += len(payload)
                            self._gate["response_held_monotonic_ns"] = time.monotonic_ns()
                            self._gate["buffer_peak_bytes"] = max(
                                self._gate["buffer_peak_bytes"], size,
                            )
                            self._transition_locked(
                                ProxyState.GATE_HOLDING,
                                "server_response_held",
                                direction=direction,
                                byte_count=len(payload),
                                buffered_bytes=size,
                            )
                            continue
                self._send_payload(destination, payload, direction)

    def _send_payload(
        self, destination: socket.socket, payload: bytes, direction: str, *, held_response: bool = False,
    ) -> None:
        if not payload:
            return
        with self._lock:
            gate = self._gate if direction == "SERVER_TO_CLIENT" else None
            before_release = gate is not None and gate["released_monotonic_ns"] is None
            if gate is not None and before_release:
                # Count attempts conservatively, including a possible partial send on failure.
                gate["forwarded_before_release_bytes"] += len(payload)
        destination.sendall(payload)
        with self._lock:
            if direction == "SERVER_TO_CLIENT":
                self._totals["server_to_client_forwarded_bytes"] += len(payload)
                if gate is not None:
                    gate["forwarded_bytes"] += len(payload)
                    if not before_release:
                        gate["forwarded_after_release_bytes"] += len(payload)
                    if held_response:
                        gate["held_response_forwarded_bytes"] += len(payload)
            self._event_locked("bytes_forwarded", direction=direction, byte_count=len(payload))

    def _event(self, event: str, **fields: Any) -> None:
        with self._lock:
            self._event_locked(event, **fields)

    def _fail_locked(self, reason: str) -> None:
        self._failure = {"exception_type": "ProxyBufferLimitError", "reason": reason}
        previous = self._state
        self._state = ProxyState.FAILED
        self._stop.set()
        self._event_locked("proxy_failed", previous_state=previous, **self._failure)

    def _accept_loop(self) -> None:
        listener = self._listener
        if listener is None:
            self._fail(RuntimeError("Listener missing."), "listener_missing")
            return
        while not self._stop.is_set():
            try:
                client, _peer = listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self._fail(exc, "listener_accept_failed")
                return
            with self._lock:
                if self._state not in {ProxyState.LISTENING, ProxyState.GATE_RELEASED}:
                    client.close()
                    self._fail(RuntimeError("Unexpected concurrent client."), "concurrent_client_rejected")
                    return
            self._serve_connection(client)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy({
                "schema_version": 1,
                "run_id": self.run_id,
                "state": self._state,
                "listen": {"host": self.LISTEN_HOST, "port": self._listen_port},
                "upstream": {"host": self.upstream_host, "port": self.upstream_port},
                "totals": self._totals,
                "gate": self._gate,
                "gates": self._gates,
                "held_response_bytes": len(self._held_response),
                "failure": self._failure,
                "cleanup": self._cleanup,
                "events": self._events,
                "events_truncated": self._events_truncated,
            })

    def write_trace(self, path: Path) -> None:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(self.snapshot(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")

    def close(self, *, join_timeout_seconds: float = 3.0) -> bool:
        with self._lock:
            if self._state == ProxyState.CLOSED:
                return bool(self._cleanup["success"])
            failed_before_cleanup = self._state == ProxyState.FAILED
            if not failed_before_cleanup:
                try:
                    self._transition_locked(ProxyState.CLEANING, "cleanup_started")
                except ProxyStateError:
                    failed_before_cleanup = True
            self._stop.set()
            self._held_response.clear()
            listener = self._listener
            thread = self._thread
        self._close_active_pair(expected=True)
        if listener is not None:
            try:
                listener.close()
            except OSError as exc:
                self._fail(exc, "listener_close_failed")
            else:
                with self._lock:
                    self._cleanup["listener_closed"] = True
        if thread is not None:
            thread.join(join_timeout_seconds)
        joined = thread is None or not thread.is_alive()
        port_released = self._verify_port_released()
        with self._lock:
            self._cleanup["thread_joined"] = joined
            self._cleanup["port_released"] = port_released
            success = joined and port_released and self._failure is None
            self._cleanup["success"] = success
            if not joined or not port_released:
                self._failure = self._failure or {
                    "exception_type": "ProxyCleanupError",
                    "reason": "thread_or_port_cleanup_failed",
                }
            if success and not failed_before_cleanup and self._state == ProxyState.CLEANING:
                self._transition_locked(ProxyState.CLOSED, "cleanup_completed", success=True)
            elif not success and self._state != ProxyState.FAILED:
                self._state = ProxyState.FAILED
                self._event_locked("cleanup_failed", thread_joined=joined, port_released=port_released)
            return success and self._state == ProxyState.CLOSED

    def _verify_port_released(self) -> bool:
        port = self._listen_port
        if port is None:
            return True
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.bind((self.LISTEN_HOST, port))
            return True
        except OSError:
            return False
        finally:
            probe.close()


class ProxyEndpointDriver:
    """Driver decorator routing only the Adapter target through the proxy."""

    def __init__(self, driver: Any, proxy: OracleTcpDelayProxy) -> None:
        self.driver = driver
        self.proxy = proxy

    def connect(self, **kwargs: Any) -> Any:
        if (kwargs.get("host"), kwargs.get("port")) != (
            self.proxy.upstream_host, self.proxy.upstream_port,
        ):
            raise RuntimeError("Oracle Adapter target does not match the verified proxy upstream.")
        routed = dict(kwargs)
        routed["host"] = self.proxy.LISTEN_HOST
        routed["port"] = self.proxy.listen_port
        return self.driver.connect(**routed)
