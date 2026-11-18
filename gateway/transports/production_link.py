"""USB link for Zeina's production firmware."""

from binascii import crc_hqx
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from time import monotonic

import serial

from .gateway_serial import HEADER, MAX_PAYLOAD, SlipDecoder, decode_record

LOG = logging.getLogger("uvicorn.error")
LOG_RAW_USB = os.environ.get("MCT431_LOG_RAW_USB") == "1"
STATES = {"SAFE_IDLE", "ARMED", "BUSY", "FAULT"}
FATAL_ERRORS = {"BOOT_COUNTER_ERROR", "TIMEOUT", "RETRY_EXHAUSTED", "TX_QUEUE_FULL",
                "RESULT_REJECTED", "SEQUENCE_EXHAUSTED"}


def encode_frame(seq, message):
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if not 1 <= len(payload) <= MAX_PAYLOAD:
        raise ValueError("Serial JSON exceeds the ATR payload limit")
    record = HEADER.pack(0, seq, len(payload)) + payload
    record += crc_hqx(record, 0xFFFF).to_bytes(2, "big")
    return b"\xc0" + record.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"


@dataclass
class Transaction:
    message: dict
    result: str
    timeout: float
    first_sent: float
    deadline: float
    attempts: int = 1
    accepted_at: float | None = None
    late_result: dict | None = None


class ProductionLink:
    def __init__(self, port, application, *,
                 open_port=serial.Serial, clock=monotonic):
        self.port_name = port
        self.application = application
        self.open_port = open_port
        self.clock = clock
        self.port = None
        self.ready = False
        self.session = None
        self.boot_id = None
        self.reopen_at = 0
        self.pending = {}

    def close(self):
        if self.port is not None:
            self.port.close()
        self.port = None
        self.ready = False
        self.session = None
        self.boot_id = None
        self.pending.clear()

    def fault(self, reason):
        LOG.warning("ESP32 A: %s", reason)
        self.close()
        self.reopen_at = self.clock() + 1
        self.application.fault(1, reason)

    def send(self, message):
        frame = encode_frame(self.seq, message)
        logger = LOG.debug if message["msg_type"] == "HEARTBEAT" else LOG.info
        logger("USB TX seq=%s %s", self.seq, json.dumps(message, separators=(",", ":")))
        self.seq += 1
        if self.port.write(frame) != len(frame):
            raise serial.SerialTimeoutException("Incomplete USB write")
        if LOG_RAW_USB:
            LOG.info("USB RAW TX %s %s",
                     datetime.now(timezone.utc).isoformat(), frame.hex(" "))

    def envelope(self, msg_type, **fields):
        return dict(schema_version=1, device_id=0, session_id=self.session,
                    msg_type=msg_type, **fields)

    def send_request(self, message, result, timeout=2):
        now = self.clock()
        self.pending[message["cmd_id"]] = Transaction(message, result, timeout, now, now + 1)
        self.track_send(message)

    def track_send(self, message, retry_index=None):
        if message["msg_type"] != "SYNC_SESSION":
            self.application.sent(1, message, self.seq, retry_index)
        self.send(message)

    def acknowledge(self, report):
        self.send(self.envelope("DELIVERY_ACK", cmd_id=report["cmd_id"],
                                ref_msg_type=report["msg_type"]))

    def retry(self, transaction, now):
        if transaction.attempts >= 4:
            self.fault(f"RETRY_EXHAUSTED: {transaction.message['msg_type']} "
                       f"cmd_id={transaction.message['cmd_id']}")
            return False
        transaction.attempts += 1
        transaction.deadline = now + 1
        self.track_send(transaction.message, transaction.attempts - 1)
        return True

    def receive(self, seq, message):
        if type(message.get("device_id")) is not int or message["device_id"] != 1:
            return
        if type(message.get("schema_version")) is not int or message["schema_version"] != 1:
            return
        now = self.clock()
        kind = message.get("msg_type")
        if kind == "HELLO" and "session_id" not in message:
            boot_id = message.get("boot_id")
            if type(boot_id) is not int or not 0 <= boot_id <= 0xFFFFFFFF:
                return
            if self.session is not None:
                if not self.ready and self.boot_id == boot_id:
                    return
                self.fault("Board restarted or requested a new session")
                return
            self.boot_id = boot_id
            self.session, command_id = self.application.open_session(1)
            self.seq = 0
            self.last_rx_seq = None
            self.last_heartbeat = now
            self.next_heartbeat = now
            self.heartbeat_seq = 0
            self.last_heartbeat_seq = -1
            sync = self.envelope("SYNC_SESSION", cmd_id=command_id, target_device_id=1, target_boot_id=boot_id)
            self.send_request(sync, "READY")
            return
        if self.session is None or type(message.get("session_id")) is not int or message["session_id"] != self.session:
            LOG.info("Ignored message outside the current USB session: %s", kind)
            return
        if self.last_rx_seq is not None and seq <= self.last_rx_seq:
            return
        self.last_rx_seq = seq
        if kind == "HEARTBEAT":
            heartbeat_seq = message.get("heartbeat_seq")
            if (message.get("boot_id") == self.boot_id and message.get("device_state") in STATES
                    and type(heartbeat_seq) is int and self.last_heartbeat_seq < heartbeat_seq <= 0xFFFFFFFF):
                self.last_heartbeat_seq = heartbeat_seq
                self.last_heartbeat = now
                if message["device_state"] == "FAULT":
                    self.fault("Production station reported FAULT")
            return
        if kind == "ERROR":
            LOG.warning("Station error: %s", message)
            if message.get("error_code") in FATAL_ERRORS:
                self.fault(str(message.get("error_code")))
            return

        cmd_id = message.get("cmd_id")
        if type(cmd_id) is not int:
            return
        transaction = self.pending.get(cmd_id)
        if kind in {"PART_PRODUCED", "PRODUCTION_FINISHED"}:
            outcome = self.application.receive(message, seq)
            if outcome.data.get("acknowledge"):
                if kind == "PART_PRODUCED":
                    self.pending.pop(cmd_id, None)
                self.acknowledge(message)
            elif outcome.status == "LATE_UNVERIFIED" and transaction:
                self.retry(transaction, now)
            return

        if not transaction or transaction.message["msg_type"] != "SYNC_SESSION":
            outcome = self.application.receive(message, seq)
            if not outcome.success:
                return
        if not transaction:
            return
        request = transaction.message
        if message.get("ref_msg_type") != request["msg_type"]:
            return
        if kind == "DELIVERY_ACK":
            transaction.deadline = now + (2 if transaction.accepted_at is None else 1)
        elif kind == "ACCEPTED":
            if transaction.accepted_at is None:
                transaction.accepted_at = now
                transaction.deadline = now + transaction.timeout
            else:
                transaction.deadline = max(transaction.accepted_at + transaction.timeout, now + 1)
        elif kind == "REJECTED":
            self.fault(f"{request['msg_type']} rejected: {message.get('error_code')}")
        elif kind == "COMPLETED" and message.get("result") == transaction.result:
            self.pending.pop(cmd_id)
            if request["msg_type"] == "SYNC_SESSION":
                self.ready = True
                self.last_heartbeat = now
                self.application.online(1, self.session)
                LOG.info("ESP32 A READY on %s", self.port_name)

    def tick(self, device_state):
        now = self.clock()
        if self.port is None:
            if now < self.reopen_at:
                return
            try:
                self.port = self.open_port(self.port_name, 115200, timeout=0, write_timeout=0.25)
                self.decoder = SlipDecoder()
            except (OSError, serial.SerialException):
                self.reopen_at = now + 1
                return

        try:
            data = self.port.read(min(max(self.port.in_waiting, 1), 8192))
            if LOG_RAW_USB and data:
                LOG.info("USB RAW RX %s %s",
                         datetime.now(timezone.utc).isoformat(), data.hex(" "))
            for record in self.decoder.feed(data):
                try:
                    seq, message = decode_record(record)
                except ValueError as error:
                    LOG.warning("Discarded USB frame: %s", error)
                    continue
                logger = LOG.debug if message.get("msg_type") == "HEARTBEAT" else LOG.info
                logger("USB RX seq=%s %s", seq, json.dumps(message, separators=(",", ":")))
                self.receive(seq, message)
                if self.port is None:
                    return
            if self.session is None:
                return
            if self.ready and now - self.last_heartbeat >= 3:
                self.fault("ESP32 A heartbeat timed out")
                return
            if self.ready and now >= self.next_heartbeat:
                self.send(self.envelope("HEARTBEAT", heartbeat_seq=self.heartbeat_seq,
                                        device_state=device_state))
                self.heartbeat_seq += 1
                self.next_heartbeat = now + 1
            for transaction in tuple(self.pending.values()):
                if now >= transaction.deadline and not self.retry(transaction, now):
                    return
        except (OSError, ValueError) as error:
            self.fault(str(error))
