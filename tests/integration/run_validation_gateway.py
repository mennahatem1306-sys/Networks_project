"""One-shot validation harness, using the normal transports and receiver logic."""

from binascii import crc_hqx
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import socket
import sys
from threading import Thread

import uvicorn

sys.path.insert(0, str(Path.cwd()))
from gateway.transports.gateway_serial import HEADER, SlipDecoder
from gateway import bridge
from gateway.transports.inspection_link import InspectionLink
from gateway.transports.production_link import encode_frame

OUT = Path(sys.argv[1])
GATEWAY = os.environ["MCT431_INSPECTION_HOST"]
UPSTREAM = ("127.0.0.1", 15056)
LOG = logging.getLogger("uvicorn.error")
REPLAY = Queue()
OLD_FRAME = bytes.fromhex((OUT / "Original-Stale-Frame.hex").read_text())
OLD_RESULT = json.loads(OLD_FRAME[4:])


def evidence(event, **details):
    LOG.info("R056 %s %s %s", datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
             event, json.dumps(details, sort_keys=True))


def forward_to_station(core, station):
    try:
        while data := core.recv(8192):
            station.sendall(data)
    except OSError:
        pass
    finally:
        try:
            station.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def proxy(listener):
    while True:
        station, _ = listener.accept()
        try:
            with station, socket.create_connection(UPSTREAM, timeout=2) as core:
                core.setblocking(True)
                Thread(target=forward_to_station, args=(core, station), daemon=True).start()
                station.settimeout(0.1)
                buffer = bytearray()
                while True:
                    try:
                        data = station.recv(8192)
                        if not data:
                            break
                        buffer.extend(data)
                    except TimeoutError:
                        pass
                    while len(buffer) >= 4:
                        size = int.from_bytes(buffer[:4], "big") + 4
                        if len(buffer) < size:
                            break
                        core.sendall(buffer[:size])
                        del buffer[:size]
                    if not buffer:
                        try:
                            frame = REPLAY.get_nowait()
                        except Empty:
                            continue
                        evidence("C_TCP_REPLAY_SENT", payload=OLD_RESULT, wire_hex=frame.hex(" "))
                        core.sendall(frame)
        except OSError as exc:
            evidence("PROXY_CONNECTION_CLOSED", reason=str(exc))


class ValidationInspectionLink(InspectionLink):
    def receive(self, message, now):
        old = message == OLD_RESULT
        if old:
            evidence("C_TCP_RECEIVER_INPUT", live_session=self.session, old_session=message["session_id"],
                     last_live_seq=self.last_seq, replay_seq=message["seq"])
        super().receive(message, now)


class ValidationGateway(bridge.TeamGateway):
    seen = set()

    def tick(self):
        super().tick()
        requested = OUT / "request.txt"
        if not requested.exists() or not (self.serial.ready and self.tcp.ready):
            return
        test = requested.read_text(encoding="ascii").strip()
        if test in self.seen or test not in {"A", "B", "C"}:
            return
        snapshot = self.snapshot()
        if snapshot["state"] != "IDLE" or snapshot["start_pending"] or snapshot["config_updating"]:
            return
        self.seen.add(test)
        evidence("BEFORE_" + test, snapshot=snapshot)
        if test == "A":
            invalid = self.tcp.envelope("INSPECT_PART", part_id=277, batch_id=34,
                                        config_version=snapshot["config_version"])
            evidence("A_TCP_INJECT", missing="job_id", seq=self.tcp.seq, payload=invalid)
            self.tcp.send(invalid)
        elif test == "B":
            self.corrupt_serial_permit(snapshot)
        elif self.tcp.session != OLD_RESULT["session_id"] and self.tcp.last_seq < OLD_RESULT["seq"]:
            REPLAY.put(OLD_FRAME)
        else:
            evidence("C_NOT_SENT", reason="Replay would not reach the old-session check")

    def corrupt_serial_permit(self, snapshot):
        link = self.serial
        valid = link.envelope("PRODUCTION_PERMIT", cmd_id=4294967000, batch_id=34,
                              part_id=277, config_version=snapshot["config_version"])
        record = bytearray(next(SlipDecoder().feed(encode_frame(link.seq, valid))))
        original_crc = int.from_bytes(record[-2:], "big")
        position = record.index(b'"part_id":277', HEADER.size) + len(b'"part_id":27')
        record[position] = ord("8")
        calculated_crc = crc_hqx(record[:-2], 0xFFFF)
        assert original_crc != calculated_crc
        frame = b"\xc0" + record.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"
        seq = link.seq
        link.seq += 1
        written = link.port.write(frame)
        evidence("B_USB_INJECT", sent_bytes=written, frame_bytes=len(frame), seq=seq,
                 original_payload=valid, altered_part_id=278, received_crc=f"0x{original_crc:04X}",
                 expected_crc=f"0x{calculated_crc:04X}", wire_hex=frame.hex(" "))


bridge.InspectionLink = ValidationInspectionLink
bridge.TeamGateway = ValidationGateway
os.environ["MCT431_INSPECTION_HOST"] = UPSTREAM[0]
os.environ["MCT431_INSPECTION_PORT"] = str(UPSTREAM[1])
listener = socket.create_server((GATEWAY, 5001))
Thread(target=proxy, args=(listener,), daemon=True).start()
uvicorn.run("gateway.main:app", host=GATEWAY, port=8001)
