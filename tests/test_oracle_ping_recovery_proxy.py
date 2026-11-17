from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.acceptance.oracle_ping_recovery_proxy import (
    ACCEPT,
    BREAK,
    DATA,
    INTERRUPT,
    MARKER,
    RESET,
    Frame,
    FrameReader,
    PingRecoveryRelay,
    ProtocolRejected,
)
from scripts.acceptance.verify_oracle_sys_timeout import (
    CALL_MS,
    ping_error,
    ping_verdict,
    timed_ping,
)


def packet(kind: int, payload: bytes, *, negotiated: bool = True, flags: int = 0) -> bytes:
    size = len(payload) + 8
    length = size.to_bytes(4, "big") if negotiated else size.to_bytes(2, "big") + bytes(2)
    return length + bytes((kind, flags, 0, 0)) + payload


def marker(code: int) -> bytes:
    return packet(MARKER, bytes((1, 0, code)))


def test_reader_distinguishes_fragmented_markers_from_data_and_switches_after_accept() -> None:
    reader = FrameReader(from_server=True)
    stream = packet(ACCEPT, (319).to_bytes(2, "big"), negotiated=False)
    stream += marker(BREAK) + marker(RESET) + packet(DATA, marker(INTERRUPT)) + marker(INTERRUPT)
    frames = [frame for byte in stream for frame in reader.feed(bytes((byte,)))]
    assert [f.control for f in frames] == [319, BREAK, RESET, None, INTERRUPT]
    assert reader.at_boundary
    assert list(reader.feed(marker(RESET) + marker(INTERRUPT))) == [
        Frame(MARKER, 11, RESET), Frame(MARKER, 11, INTERRUPT),
    ]


@pytest.mark.parametrize("frame", [marker(4), packet(MARKER, b"\x00\x00\x03"),
                                  packet(MARKER, b"\x01\x00\x03\x00"), packet(5, b"redirect")])
def test_unsupported_control_frames_are_rejected(frame: bytes) -> None:
    reader = FrameReader()
    reader.negotiated = True
    with pytest.raises(ProtocolRejected):
        list(reader.feed(frame))


@pytest.mark.parametrize("flag, suffix", [(0, b""), (0x20, b""), (0, b"padding")])
def test_server_recovery_marker_uses_reader_rules_not_client_writer_rules(flag: int, suffix: bytes) -> None:
    reader = FrameReader(from_server=True)
    accept = packet(ACCEPT, (319).to_bytes(2, "big"), negotiated=False)
    response = packet(MARKER, b"\x02\x00\x02" + suffix, flags=flag)
    frames = list(reader.feed(accept + response[:5]))
    assert frames == [Frame(ACCEPT, 10, 319, length_field_bits=16)]
    assert not reader.at_boundary
    assert list(reader.feed(response[5:] + packet(DATA, b"next"))) == [
        Frame(MARKER, len(response), RESET, flag), Frame(DATA, 12),
    ]
    client_reader = FrameReader()
    client_reader.negotiated = True
    with pytest.raises(ProtocolRejected):
        list(client_reader.feed(response))


@pytest.mark.parametrize("header, reason, length, kind", [
    (b"\x00\x00\x00\x07\x06\x00\x00\x00", "invalid_tns_frame_length", 7, DATA),
    (b"\x00\x00\x00\x0b\x63\x20\x00\x00", "unsupported_tns_frame_type", 11, 99),
])
def test_rejected_header_keeps_only_framing_metadata(header: bytes, reason: str, length: int, kind: int) -> None:
    relay = PingRecoveryRelay(1521, "release")
    try:
        reader = relay.from_server
        reader.negotiated = True
        assert list(relay._frames(reader, header[:4])) == []
        with pytest.raises(ProtocolRejected, match=reason) as raised:
            list(relay._frames(reader, header[4:] + b"payload-canary"))
        assert raised.value.metadata == {
            "direction": "upstream", "frame_type": kind, "header_length": 8, "frame_length": length,
            "length_field_bits": 32, "packet_flags": header[5], "framing_state": "post_accept",
            "read_size": 18, "completed_frames_in_read": 0, "read_started_at_boundary": 0,
        }
        assert "payload-canary" not in str(raised.value.metadata)
    finally:
        relay.listener.close()


def receive(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        assert part
        data.extend(part)
    return bytes(data)


@pytest.mark.parametrize("scenario", [
    "transparent", "release", "hold", "wrong_marker", "upstream_eof", "watchdog", "buffer_limit", "unknown_frame",
])
def test_relay_uses_target_direction_and_releases_only_after_forwarding(
    scenario: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if scenario == "watchdog":
        monkeypatch.setattr("scripts.acceptance.oracle_ping_recovery_proxy.GATE_SECONDS", 0.2)
    elif scenario == "buffer_limit":
        monkeypatch.setattr("scripts.acceptance.oracle_ping_recovery_proxy.BUFFER_LIMIT", 64)
    with socket.socket() as upstream_listener:
        upstream_listener.bind(("127.0.0.1", 0))
        upstream_listener.listen(1)
        upstream_listener.settimeout(2)
        mode = scenario if scenario in {"transparent", "release", "hold"} else "release"
        with PingRecoveryRelay(upstream_listener.getsockname()[1], mode) as relay:
            with socket.create_connection(("127.0.0.1", relay.local_port), timeout=2) as client:
                upstream, _ = upstream_listener.accept()
                with upstream:
                    upstream.settimeout(2)
                    initial = packet(ACCEPT, (319).to_bytes(2, "big"), negotiated=False)
                    upstream.sendall(initial)
                    assert receive(client, len(initial)) == initial
                    relay.arm()
                    request = packet(DATA, marker(INTERRUPT))
                    client.sendall(request)
                    assert receive(upstream, len(request)) == request
                    result = packet(DATA, b"response")
                    # A server-direction marker is never a release trigger.
                    unknown = packet(99, b"unknown")
                    tail = {"release": marker(INTERRUPT), "unknown_frame": unknown[:4]}.get(scenario, b"")
                    upstream.sendall(result + tail)
                    if scenario == "transparent":
                        assert receive(client, len(result)) == result
                    else:
                        client.settimeout(0.05)
                        with pytest.raises(TimeoutError):
                            client.recv(1)
                        client.settimeout(2)
                        if scenario == "upstream_eof":
                            upstream.shutdown(socket.SHUT_WR)
                            assert not client.recv(1)
                        elif scenario == "watchdog":
                            assert not client.recv(1)
                        elif scenario == "buffer_limit":
                            upstream.sendall(packet(DATA, bytes(55)))
                            assert not client.recv(1)
                        elif scenario == "wrong_marker":
                            client.sendall(marker(BREAK))
                            assert not client.recv(1)
                        else:
                            interrupt = marker(INTERRUPT)
                            client.sendall(interrupt[:4])
                            client.sendall(interrupt[4:])
                            assert receive(upstream, len(interrupt)) == interrupt
                            if scenario == "release":
                                expected = result + marker(INTERRUPT)
                                assert receive(client, len(expected)) == expected
                                recovery = packet(MARKER, b"\x02\x00\x02", flags=0x20)
                                next_frames = recovery + marker(RESET) + packet(DATA, b"rewarmed")
                                upstream.sendall(next_frames[:6])
                                upstream.sendall(next_frames[6:])
                                assert receive(client, len(next_frames)) == next_frames
                            elif scenario == "hold":
                                client.settimeout(0.05)
                                with pytest.raises(TimeoutError):
                                    client.recv(1)
                            else:
                                upstream.sendall(unknown[4:])
                                assert not client.recv(1)
                assert relay.finished.wait(2)
        assert not relay.worker.is_alive()
        assert relay.listener.fileno() == -1
        event_names = [e["event"] for e in relay.events]
        assert ("interrupt_forwarded" in event_names) is (scenario in {"release", "hold", "unknown_frame"})
        assert ("response_released" in event_names) is (scenario == "release")
        if scenario == "release":
            assert event_names.index("interrupt_forwarded") < event_names.index("response_released")
        if scenario in {"watchdog", "buffer_limit"}:
            failure = next(e for e in relay.events if e["event"] == "relay_failed")
            assert failure["reason"] == ("watchdog" if scenario == "watchdog" else "unexpected_or_excess_response")
        if scenario == "unknown_frame":
            failure = next(e for e in relay.events if e["event"] == "relay_failed")
            assert failure["frame_type"] == 99
            assert failure["reason"] == "unsupported_tns_frame_type"


def event(name: str, stamp: int, **fields: str) -> dict[str, str | int]:
    return {"event": name, "ns": stamp, **fields}


def test_hold_verdict_needs_recovery_reason_and_clean_transport_evidence() -> None:
    call: dict[str, Any] = {
        "readback_ms": CALL_MS, "healthy": False, "started_ns": 1, "ended_ns": 8, "exception_ns": 7,
        "exception": {"full_code": "DPY-4011", "message": "socket timed out while recovering from previous socket timeout"},
    }
    events = [event("interrupt_forwarded", 4), event("eof", 6, side="client"),
              event("transport_ended_before_cleanup", 9)]
    assert ping_verdict("hold", call, events, None) == "RECOVERY_WAIT_TIMEOUT"
    for invalid in (event("response_forwarded", 5), event("eof", 5, side="upstream"),
                    event("relay_failed", 5)):
        assert ping_verdict("hold", call, [*events, invalid], None) == "INCONCLUSIVE"
    assert ping_verdict("hold", call, events[:-1], None) == "INCONCLUSIVE"
    call["exception"]["message"] = "the database or network closed the connection"
    assert ping_verdict("hold", call, events, None) == "INCONCLUSIVE"


def test_release_verdict_distinguishes_followup_and_preserves_redacted_exception() -> None:
    error = RuntimeError(SimpleNamespace(full_code="DPY-4024", message="password=secret-canary", code=0))
    error.__context__ = OSError(104, "unrecorded payload")
    evidence = ping_error(error)
    assert "secret-canary" not in str(evidence)
    assert "unrecorded payload" not in str(evidence)
    assert evidence["is_session_dead"] is None
    assert evidence["chain"][0]["errno"] == 104
    call = {"readback_ms": CALL_MS, "healthy": True, "started_ns": 1, "ended_ns": 8, "exception_ns": 7,
            "exception": evidence}
    events = [event("interrupt_forwarded", 4), event("response_released", 5)]
    assert ping_verdict("release", call, events, None) == "RECOVERY_RECEIVE_COMPLETED_CONNECTION_UNVERIFIED"
    assert ping_verdict("release", call, events, {"exception": None, "healthy": True}) == (
        "RECOVERY_RECEIVE_COMPLETED_CONNECTION_USABLE"
    )
    assert ping_verdict("release", call, list(reversed(events))[:1], None) == "INCONCLUSIVE"


def test_timed_ping_reads_timeout_and_does_not_probe_a_dead_connection() -> None:
    calls = []

    def ping() -> None:
        calls.append("ping")
        raise RuntimeError(SimpleNamespace(full_code="DPY-4011", is_session_dead=True))

    connection = SimpleNamespace(call_timeout=CALL_MS, ping=ping, is_healthy=lambda: False)
    result = timed_ping(connection)
    assert calls == ["ping"]
    assert result["readback_ms"] == CALL_MS
    assert result["started_ns"] <= result["exception_ns"] <= result["ended_ns"]
    assert result["healthy"] is False
    assert result["exception"]["is_session_dead"] is True


def test_partial_interrupt_forward_failure_cannot_arm_release() -> None:
    relay = PingRecoveryRelay(1521, "release")
    try:
        relay.from_client.negotiated = True
        relay.armed_at = 1
        relay.request_seen = True
        relay.held.extend(packet(DATA, b"response"))
        sent = []

        def sendall(data: bytes) -> None:
            sent.append(data[:4])
            raise BrokenPipeError("internal payload canary")

        with pytest.raises(ProtocolRejected, match="forward_failed") as raised:
            relay._client_data(marker(INTERRUPT), SimpleNamespace(sendall=sendall))
        assert sent == [marker(INTERRUPT)[:4]]
        assert not relay.interrupt_forwarded
        assert not relay.released
        assert raised.value.metadata == {"destination": "upstream", "attempted_bytes": 11,
                                         "socket_error": "BrokenPipeError"}
    finally:
        relay.listener.close()
