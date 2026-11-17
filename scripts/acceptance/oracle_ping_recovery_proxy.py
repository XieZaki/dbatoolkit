"""One-shot, loopback-only Oracle Net relay for the PING recovery experiment."""

from __future__ import annotations

import select
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

BREAK, RESET, INTERRUPT = 1, 2, 3
ACCEPT, DATA, MARKER = 2, 6, 12
HEADER_SIZE = 8
MARKER_FIELDS_SIZE = 3
BUFFER_LIMIT = 64 * 1024
GATE_SECONDS = 6
RUN_SECONDS = 15


class ProtocolRejected(Exception):
    """The run cannot support a protocol verdict."""

    def __init__(self, reason: str, **metadata: int | str) -> None:
        super().__init__(reason)
        self.metadata = metadata


@dataclass(frozen=True)
class Frame:
    kind: int
    size: int
    control: int | None = None
    packet_flags: int = 0
    length_field_bits: int = 32


class FrameReader:
    """Keep only a header and the leading fields of ACCEPT and MARKER."""

    def __init__(self, *, from_server: bool = False) -> None:
        self.from_server = from_server
        self.negotiated = False
        self.header = bytearray()
        self.prefix = bytearray()
        self.remaining = 0

    @property
    def at_boundary(self) -> bool:
        return not self.header

    def reject(self, reason: str) -> ProtocolRejected:
        """Record parsed header metadata only, including on rejection."""
        length_bits = 32 if self.negotiated else 16
        return ProtocolRejected(
            reason,
            direction="upstream" if self.from_server else "client",
            frame_type=self.header[4],
            header_length=HEADER_SIZE,
            frame_length=int.from_bytes(self.header[:length_bits // 8], "big"),
            length_field_bits=length_bits,
            packet_flags=self.header[5],
            framing_state="post_accept" if self.negotiated else "pre_accept",
        )

    def feed(self, data: bytes) -> Iterator[Frame]:
        pos = 0
        while pos < len(data):
            if len(self.header) < HEADER_SIZE:
                size = min(HEADER_SIZE - len(self.header), len(data) - pos)
                self.header.extend(data[pos:pos + size])
                pos += size
                if len(self.header) < HEADER_SIZE:
                    return
                length = int.from_bytes(self.header[:4 if self.negotiated else 2], "big")
                kind = self.header[4]
                if not HEADER_SIZE <= length <= BUFFER_LIMIT:
                    raise self.reject("invalid_tns_frame_length")
                if kind not in {1, ACCEPT, DATA, MARKER, 14}:
                    raise self.reject("unsupported_tns_frame_type")
                if ((kind == 1 and (self.from_server or self.negotiated))
                        or (kind == DATA and length < 10)
                        or (kind == ACCEPT and (not self.from_server or self.negotiated or length < 10))):
                    raise self.reject("invalid_tns_frame_state_or_length")
                if kind == MARKER:
                    self._validate_marker_header(length)
                self.remaining = length - HEADER_SIZE
            kind = self.header[4]
            size = min(self.remaining, len(data) - pos)
            prefix_size = {ACCEPT: 2, MARKER: MARKER_FIELDS_SIZE}.get(kind, 0)
            copy_size = min(prefix_size - len(self.prefix), size)
            self.prefix.extend(data[pos:pos + copy_size])
            self.remaining -= size
            pos += size
            if self.remaining:
                continue
            control: int | None = None
            if kind == ACCEPT:
                control = int.from_bytes(self.prefix, "big")
                if not 315 <= control <= 319:
                    raise self.reject("unsupported_tns_version")
                self.negotiated = True
            elif kind == MARKER:
                control = self.prefix[2]
                if control not in {BREAK, RESET, INTERRUPT} or (
                    not self.from_server and self.prefix[:2] != b"\x01\x00"
                ):
                    raise self.reject("invalid_tns_marker")
            length_bits = 32 if self.negotiated and kind != ACCEPT else 16
            length = int.from_bytes(self.header[:length_bits // 8], "big")
            flags = self.header[5]
            self.header.clear()
            self.prefix.clear()
            yield Frame(kind, length, control, flags, length_bits)

    def _validate_marker_header(self, length: int) -> None:
        # Thin writes a fixed client marker. Its server reader treats flags
        # as separate header fields and reads the subtype after two bytes.
        minimum = HEADER_SIZE + MARKER_FIELDS_SIZE
        if not self.negotiated or length < minimum:
            raise self.reject("invalid_tns_marker_header")
        if not self.from_server and (length != minimum or self.header[5:] != bytes(3)):
            raise self.reject("invalid_tns_client_marker_header")


class PingRecoveryRelay:
    """Relay opaque bytes in FIFO order and classify only complete control frames."""

    def __init__(self, port: int, scenario: str) -> None:
        if not 1 <= port <= 65535 or scenario not in {"transparent", "release", "hold"}:
            raise ValueError("Invalid experiment endpoint or scenario")
        self.port = port
        self.scenario = scenario
        self.events: list[dict[str, int | str]] = []
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.listener = socket.socket()
        self.client: socket.socket | None = None
        self.upstream: socket.socket | None = None
        self.from_client = FrameReader()
        self.from_server = FrameReader(from_server=True)
        self.held = bytearray()
        self.armed_at: int | None = None
        self.request_seen = False
        self.interrupt_forwarded = False
        self.released = False
        self.worker = threading.Thread(target=self._run, name="oracle-ping-recovery")

    def __enter__(self) -> PingRecoveryRelay:
        try:
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(1)
            self.listener.settimeout(0.1)
            self.worker.start()
        except BaseException:
            self.listener.close()
            raise
        return self

    @property
    def local_port(self) -> int:
        return int(self.listener.getsockname()[1])

    def record(self, event: str, **details: int | str) -> None:
        with self.lock:
            if len(self.events) >= 8192:
                raise ProtocolRejected("event_limit")
            self.events.append({"event": event, "ns": time.monotonic_ns(), **details})

    def arm(self) -> None:
        with self.lock:
            if (self.armed_at is not None or self.finished.is_set()
                    or not all(r.negotiated and r.at_boundary for r in (self.from_client, self.from_server))):
                raise ProtocolRejected("gate_without_established_frame_boundary")
            self.armed_at = time.monotonic_ns()
            self.record("gate_armed")

    def _client_data(self, data: bytes, upstream: socket.socket) -> None:
        trigger = False
        for frame in self._frames(self.from_client, data):
            if frame.kind == MARKER:
                self._record_marker(frame, "client")
            if self.armed_at is None:
                continue
            if not self.request_seen:
                if frame.kind != DATA:
                    raise ProtocolRejected("unexpected_ping_request")
                self.request_seen = True
                self.record("request_seen")
            elif self.scenario != "transparent" and not self.released:
                if (frame.kind != MARKER or frame.control != INTERRUPT
                        or not self.held or trigger or self.interrupt_forwarded):
                    raise ProtocolRejected("interrupt_outside_recovery_window")
                trigger = True
        self._forward(upstream, data, "upstream")
        if trigger:
            self.interrupt_forwarded = True
            self.record("interrupt_forwarded")

    def _server_data(self, data: bytes, client: socket.socket) -> None:
        for frame in self._frames(self.from_server, data):
            if frame.kind == ACCEPT:
                self.from_client.negotiated = True
                self.record("accepted", version=frame.control or 0)
            elif frame.kind == MARKER:
                self._record_marker(frame, "upstream")
        if self.scenario != "transparent" and self.armed_at is not None and not self.released:
            if not self.request_seen or len(self.held) + len(data) > BUFFER_LIMIT:
                raise ProtocolRejected("unexpected_or_excess_response")
            self.held.extend(data)
            self.record("response_held", size=len(data), total=len(self.held))
        else:
            self._forward(client, data, "client")
            if self.armed_at is not None:
                self.record("response_forwarded", size=len(data))

    def _record_marker(self, frame: Frame, direction: str) -> None:
        self.record("marker_seen", direction=direction, marker=frame.control or 0, size=frame.size,
                    header_length=HEADER_SIZE, length_field_bits=frame.length_field_bits,
                    packet_flags=frame.packet_flags)

    def _frames(self, reader: FrameReader, data: bytes) -> Iterator[Frame]:
        complete = 0
        initial_boundary = reader.at_boundary
        try:
            for frame in reader.feed(data):
                complete += 1
                yield frame
        except ProtocolRejected as exc:
            exc.metadata.update(read_size=len(data), completed_frames_in_read=complete,
                                read_started_at_boundary=int(initial_boundary))
            raise

    def _forward(self, destination: socket.socket, data: bytes | bytearray, side: str) -> None:
        try:
            destination.sendall(data)
        except OSError as exc:
            # sendall cannot report how much was sent before an error.
            raise ProtocolRejected("forward_failed", destination=side, attempted_bytes=len(data),
                                   socket_error=type(exc).__name__) from exc

    def _run(self) -> None:
        deadline = time.monotonic() + RUN_SECONDS
        side = "listener"
        try:
            while not self.stop.is_set() and time.monotonic() < deadline:
                try:
                    client, _ = self.listener.accept()
                    self.client = client
                    break
                except TimeoutError:
                    continue
            else:
                return
            side = "upstream"
            upstream = socket.create_connection(("127.0.0.1", self.port), timeout=2)
            self.upstream = upstream
            client.settimeout(0.5)
            upstream.settimeout(0.5)
            self.record("connected")
            while not self.stop.is_set():
                if time.monotonic() >= deadline or (
                    self.armed_at is not None and not self.released and self.scenario != "transparent"
                    and time.monotonic_ns() - self.armed_at >= GATE_SECONDS * 1_000_000_000
                ):
                    raise ProtocolRejected("watchdog")
                side = "relay"
                readable, _, _ = select.select([self.listener, upstream, client], [], [], 0.025)
                if self.listener in readable:
                    with self.listener.accept()[0]:
                        raise ProtocolRejected("additional_client")
                # Observe upstream EOF before recording a client EOF in the same cycle.
                for source, side in ((upstream, "upstream"), (client, "client")):
                    if source not in readable:
                        continue
                    data = source.recv(8192)
                    if not data:
                        self.record("eof", side=side)
                        return
                    with self.lock:
                        if source is upstream:
                            self._server_data(data, client)
                        else:
                            self._client_data(data, upstream)
                        if (self.scenario == "release" and self.interrupt_forwarded
                                and not self.released and self.from_server.at_boundary):
                            side = "client"
                            self._forward(client, self.held, "client")
                            self.released = True
                            self.record("response_released", size=len(self.held))
                            self.held.clear()
        except (OSError, ValueError, ProtocolRejected) as exc:
            if not self.stop.is_set():
                with self.lock:
                    self.events.append({"event": "relay_failed", "ns": time.monotonic_ns(), "side": side,
                                        "reason": str(exc) if isinstance(exc, ProtocolRejected)
                                        else type(exc).__name__,
                                        **(exc.metadata if isinstance(exc, ProtocolRejected) else {})})
        finally:
            self.held.clear()
            for label, stream in (("client", self.client), ("upstream", self.upstream), ("listener", self.listener)):
                if stream is not None:
                    stream.close()
                    with self.lock:
                        self.events.append({"event": "socket_closed", "ns": time.monotonic_ns(), "side": label})
            self.finished.set()

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.worker.join(3)
        if self.worker.is_alive():
            raise RuntimeError("Relay cleanup did not finish")
