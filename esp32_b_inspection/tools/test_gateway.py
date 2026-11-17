import argparse
import json
import os
import queue
import socket
import struct
import threading
import time


def frame(message):
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if not 0 < len(body) <= 1024:
        raise ValueError("frame length outside 1..1024")
    return struct.pack("!I", len(body)) + body


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("peer closed connection")
        data.extend(chunk)
    return bytes(data)


def receive(sock):
    (length,) = struct.unpack("!I", exact(sock, 4))
    if not 0 < length <= 1024:
        raise ValueError("invalid frame length")
    return json.loads(exact(sock, length))


class Peer:
    def __init__(self, sock, log, acknowledge_results=True):
        self.sock, self.log = sock, log
        self.session = int(time.time()) & 0xFFFFFFFF
        self.seq = 0
        self.lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.pending = []
        self.acknowledge_results = acknowledge_results
        self.inbox = queue.Queue()
        self.stopped = threading.Event()

    def record(self, direction, message):
        with self.log_lock:
            self.log.write(
                json.dumps(
                    {
                        "monotonic_s": time.monotonic(),
                        "direction": direction,
                        "message": message,
                    }
                )
                + "\n"
            )
            self.log.flush()
            if direction == "RX" and message.get("msg_type") == "INSPECTION_RESULT":
                os.fsync(self.log.fileno())

    def send(self, kind, fragmented=False, **fields):
        with self.lock:
            message = dict(
                schema_version=1,
                device_id=0,
                session_id=self.session,
                seq=self.seq,
                msg_type=kind,
                **fields,
            )
            self.seq += 1
            wire = frame(message)
            if fragmented:
                for chunk in (wire[:1], wire[1:3], wire[3:9], wire[9:]):
                    self.sock.sendall(chunk)
                    time.sleep(0.01)
            else:
                self.sock.sendall(wire)
            self.record("TX", message)

    def reader(self):
        try:
            while not self.stopped.is_set():
                message = receive(self.sock)
                if (
                    message.get("schema_version") != 1
                    or message.get("device_id") != 2
                    or message.get("session_id") != self.session
                ):
                    raise ValueError("invalid response envelope")
                self.record("RX", message)
                if (
                    message["msg_type"] == "INSPECTION_RESULT"
                    and self.acknowledge_results
                ):
                    self.send(
                        "ACCEPTED",
                        ref_msg_type="INSPECTION_RESULT",
                        job_id=message["job_id"],
                    )
                if message["msg_type"] != "HEARTBEAT":
                    self.inbox.put(message)
        except (OSError, ValueError, ConnectionError) as exc:
            self.inbox.put(exc)

    def heartbeats(self):
        count = 0
        while not self.stopped.wait(1):
            try:
                self.send("HEARTBEAT", heartbeat_seq=count, device_state="SAFE_IDLE")
                count += 1
            except OSError:
                return

    def wait(self, kind, timeout=8, **fields):
        deadline = time.monotonic() + timeout

        def matches(message):
            return message["msg_type"] == kind and all(
                message.get(k) == v for k, v in fields.items()
            )

        for index, message in enumerate(self.pending):
            if matches(message):
                return self.pending.pop(index)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"waiting for {kind} {fields}")
            try:
                message = self.inbox.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"waiting for {kind} {fields}") from exc
            if isinstance(message, Exception):
                raise message
            if matches(message):
                return message
            self.pending.append(message)


def run(host, port, log_path, scenario="normal"):
    with socket.socket() as server, open(log_path, "w", encoding="utf-8") as log:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        print(f"Waiting for ESP32 B on {host}:{port} ...")
        sock, _address = server.accept()
        with sock:
            peer = Peer(sock, log, acknowledge_results=scenario != "result-retries")
            hello = receive(sock)
            assert hello["msg_type"] == "HELLO" and hello["device_id"] == 2, hello
            peer.record("RX", hello)
            threading.Thread(target=peer.reader, daemon=True).start()
            peer.send(
                "SYNC_SESSION",
                cmd_id=100,
                target_device_id=2,
                target_boot_id=hello["boot_id"],
                fragmented=True,
            )
            peer.wait("COMPLETED", cmd_id=100, result="READY")
            if scenario != "heartbeat-loss":
                threading.Thread(target=peer.heartbeats, daemon=True).start()
            try:
                if scenario == "heartbeat-loss":
                    started = time.monotonic()
                    try:
                        peer.wait("NEVER", timeout=6)
                    except ConnectionError:
                        elapsed = time.monotonic() - started
                        assert 2 <= elapsed <= 5, elapsed
                        print(
                            f"PASS: missing gateway heartbeat caused disconnect after {elapsed:.3f} s."
                        )
                        return
                    raise AssertionError(
                        "board did not disconnect after heartbeat loss"
                    )

                def config(cmd, version, n, k, ms=1000):
                    peer.send(
                        "SET_INSPECTION_CONFIG",
                        cmd_id=cmd,
                        config_version=version,
                        inspection_time_ms=ms,
                        rule_n=n,
                        rule_k=k,
                    )
                    peer.wait("COMPLETED", cmd_id=cmd, result="CONFIG_APPLIED")

                def job(jid, part, version, expected):
                    fields = {
                        "batch_id": 1,
                        "job_id": jid,
                        "part_id": part,
                        "config_version": version,
                    }
                    peer.send("INSPECT_PART", fragmented=True, **fields)
                    peer.wait("ACCEPTED", job_id=jid)
                    peer.send("INSPECT_PART", **fields)
                    result = peer.wait("INSPECTION_RESULT", job_id=jid)
                    assert result["result"] == expected and result["part_id"] == part, (
                        result
                    )
                    peer.send("INSPECT_PART", **fields)
                    replay = peer.wait("INSPECTION_RESULT", job_id=jid)
                    for key in (
                        "result",
                        "part_id",
                        "config_version",
                        "inspection_elapsed_ms",
                    ):
                        assert result[key] == replay[key], (result, replay)
                    peer.send("INSPECT_PART", **dict(fields, part_id=part + 1))
                    peer.wait(
                        "REJECTED", job_id=jid, error_code="DUPLICATE_ID_CONFLICT"
                    )

                config(101, 1, 3, 1)
                if scenario == "result-retries":
                    peer.send(
                        "INSPECT_PART",
                        batch_id=1,
                        job_id=1,
                        part_id=2,
                        config_version=1,
                    )
                    reports = [
                        peer.wait("INSPECTION_RESULT", job_id=1) for _ in range(4)
                    ]
                    for report in reports:
                        assert report["result"] == "FAIL", report
                        assert (
                            report["inspection_elapsed_ms"]
                            == reports[0]["inspection_elapsed_ms"]
                        ), report
                    peer.wait("ERROR", error_code="RETRY_EXHAUSTED")
                    try:
                        peer.wait("NEVER", timeout=4)
                    except ConnectionError:
                        print(
                            "PASS: four identical result reports, RETRY_EXHAUSTED, safe disconnect."
                        )
                        return
                    raise AssertionError(
                        "board did not disconnect after exhausted retries"
                    )
                job(1, 2, 1, "FAIL")
                job(2, 3, 1, "PASS")

                peer.send(
                    "INSPECT_PART", batch_id=1, job_id=3, part_id=5, config_version=1
                )
                peer.wait("ACCEPTED", job_id=3)
                config(102, 2, 1, -2147483648, 5000)
                old = peer.wait("INSPECTION_RESULT", job_id=3)
                assert old["config_version"] == 1 and old["result"] == "FAIL", old
                job(4, 4294967295, 2, "FAIL")
                config(103, 3, 3, -1)
                job(5, 1, 3, "FAIL")
                peer.send(
                    "SET_INSPECTION_CONFIG",
                    cmd_id=104,
                    config_version=4,
                    inspection_time_ms=1000,
                    rule_n=0,
                    rule_k=0,
                )
                peer.wait("REJECTED", cmd_id=104, error_code="INVALID_RULE_N")
                print(
                    "PASS: handshake, fragmentation, rules, duplicates, conflict, live snapshot, invalid N."
                )
                print(
                    "Closing connection: firmware must abandon old session and reconnect safely."
                )
            finally:
                peer.stopped.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ESP32 B hardware integration test gateway"
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--log", default="gateway_trace.jsonl")
    parser.add_argument(
        "--scenario",
        choices=("normal", "heartbeat-loss", "result-retries"),
        default="normal",
    )
    args = parser.parse_args()
    run(args.host, args.port, args.log, args.scenario)
