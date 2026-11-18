"""Persistent TCP connection to Aya's ESP32 B."""

import json
import logging
import socket

from .gateway_tcp import MAX_PAYLOAD_BYTES, encode_message, is_uint32
from .production_link import STATES, Transaction

LOG = logging.getLogger("uvicorn.error")


class InspectionLink:
    def __init__(self, address, application, clock):
        self.listener = socket.create_server(address)
        self.listener.setblocking(False)
        self.application = application
        self.clock = clock
        self.connection = None
        self.ready = False
        self.session = None
        self.pending = {}

    def disconnect(self):
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.ready = False
        self.session = None
        self.pending.clear()

    def close(self):
        self.disconnect()
        self.listener.close()

    def fault(self, reason):
        notify = self.ready
        self.disconnect()
        LOG.warning("ESP32 B: %s", reason)
        if notify:
            self.application.fault(2, reason)

    def send(self, message):
        frame = encode_message({**message, "seq": self.seq})
        if len(self.output) + len(frame) > 8192:
            raise OSError("ESP32 B transmit buffer exceeded")
        self.output.extend(frame)
        logger = LOG.debug if message["msg_type"] == "HEARTBEAT" else LOG.info
        logger("TCP TX seq=%s %s", self.seq, json.dumps(message, separators=(",", ":")))
        self.seq += 1

    def envelope(self, kind, **fields):
        return dict(schema_version=1, device_id=0, session_id=self.session, msg_type=kind, **fields)

    def send_request(self, message, result, timeout=2):
        id_field = "job_id" if message["msg_type"] == "INSPECT_PART" else "cmd_id"
        now = self.clock()
        self.pending[(id_field, message[id_field])] = Transaction(message, result, timeout, now, now + 1)
        self.track_send(message)

    def track_send(self, message, retry_index=None):
        if message["msg_type"] != "SYNC_SESSION":
            self.application.sent(2, message, self.seq, retry_index)
        self.send(message)

    def retry(self, transaction, now):
        if transaction.attempts >= 4:
            self.fault(f"RETRY_EXHAUSTED: {transaction.message}")
            return
        transaction.attempts += 1
        transaction.deadline = now + 1
        self.track_send(transaction.message, transaction.attempts - 1)

    def receive(self, message, now):
        if not isinstance(message, dict):
            raise ValueError("Expected a JSON object")
        if any(type(message.get(key)) is not int or message[key] != value
               for key, value in (("schema_version", 1), ("device_id", 2))):
            raise ValueError("Invalid inspection-station envelope")
        seq = message.get("seq")
        if not is_uint32(seq) or seq <= self.last_seq:
            return
        kind = message.get("msg_type")
        if self.session is None:
            if (kind != "HELLO" or not is_uint32(message.get("boot_id"))
                    or "session_id" in message or message.get("device_state") not in STATES):
                raise ValueError("Expected ESP32 B HELLO")
            self.last_seq = seq
            self.boot_id = message["boot_id"]
            self.session, cmd_id = self.application.open_session(2)
            sync = self.envelope("SYNC_SESSION", cmd_id=cmd_id, target_device_id=2, target_boot_id=self.boot_id)
            self.send_request(sync, "READY")
            return
        if not is_uint32(message.get("session_id")) or message["session_id"] != self.session:
            LOG.info("Ignored old TCP session message: %s", kind)
            return
        self.last_seq = seq
        if kind == "HEARTBEAT":
            hb = message.get("heartbeat_seq")
            if (is_uint32(hb) and hb > self.last_hb and message.get("boot_id") == self.boot_id
                    and message.get("device_state") in STATES):
                self.last_hb = hb
                self.last_seen = now
                if message["device_state"] == "FAULT":
                    raise ValueError("Inspection station reported FAULT")
            return

        id_field = "job_id" if "job_id" in message else "cmd_id"
        work_id = message.get(id_field)
        if not is_uint32(work_id):
            return
        key = (id_field, work_id)
        if kind == "INSPECTION_RESULT":
            self.receive_result(key, message, now)
            return
        transaction = self.pending.get(key)
        if not transaction or transaction.message["msg_type"] != "SYNC_SESSION":
            outcome = self.application.receive(message, seq)
            if not outcome.success:
                return
        if transaction is None or message.get("ref_msg_type") != transaction.message["msg_type"]:
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
            raise ValueError(f"{transaction.message['msg_type']} rejected: {message.get('error_code')}")
        elif (kind == "COMPLETED" and message.get("result") == transaction.result
              and transaction.message["msg_type"] != "INSPECT_PART"):
            self.pending.pop(key)
            if transaction.message["msg_type"] == "SYNC_SESSION":
                self.ready = True
                self.last_seen = now
                self.next_heartbeat = now
                self.application.online(2, self.session)
                LOG.info("ESP32 B READY")

    def receive_result(self, key, message, now):
        outcome = self.application.receive(message, message["seq"])
        if outcome.data.get("acknowledge"):
            self.pending.pop(key, None)
            self.acknowledge(message)
        elif outcome.status == "LATE_UNVERIFIED" and key in self.pending:
            self.retry(self.pending[key], now)

    def acknowledge(self, report):
        self.send(self.envelope("DELIVERY_ACK", job_id=report["job_id"],
                                ref_msg_type="INSPECTION_RESULT"))

    def tick(self, device_state):
        now = self.clock()
        try:
            if self.connection is None:
                try:
                    self.connection, address = self.listener.accept()
                except BlockingIOError:
                    return
                self.connection.setblocking(False)
                self.input = bytearray()
                self.output = bytearray()
                self.seq = self.heartbeat_seq = 0
                self.last_seq = self.last_hb = -1
                self.last_seen = now
                LOG.info("ESP32 B TCP connected from %s", address)
            try:
                data = self.connection.recv(8192)
            except BlockingIOError:
                data = None
            if data == b"":
                raise OSError("TCP disconnected")
            if data:
                self.input.extend(data)
                self.read_frames(now)
                if self.connection is None:
                    return
            if now - self.last_seen >= 3:
                raise OSError("Handshake or heartbeat timed out")
            if self.ready and now >= self.next_heartbeat:
                self.send(self.envelope("HEARTBEAT", heartbeat_seq=self.heartbeat_seq, device_state=device_state))
                self.next_heartbeat = now + 1
                self.heartbeat_seq += 1
            for transaction in tuple(self.pending.values()):
                if now >= transaction.deadline:
                    self.retry(transaction, now)
                    if self.connection is None:
                        return
            if self.output:
                try:
                    count = self.connection.send(self.output)
                except BlockingIOError:
                    return
                if count == 0:
                    raise OSError("TCP did not accept outgoing data")
                del self.output[:count]
        except (OSError, ValueError) as error:
            self.fault(str(error))

    def read_frames(self, now):
        while len(self.input) >= 4:
            size = int.from_bytes(self.input[:4], "big")
            if not 1 <= size <= MAX_PAYLOAD_BYTES:
                raise ValueError(f"Invalid TCP frame size {size}")
            end = 4 + size
            if len(self.input) < end:
                return
            message = json.loads(self.input[4:end].decode("utf-8"))
            del self.input[:end]
            logger = LOG.debug if isinstance(message, dict) and message.get("msg_type") == "HEARTBEAT" else LOG.info
            logger("TCP RX %s", message)
            self.receive(message, now)
            if self.connection is None:
                return
