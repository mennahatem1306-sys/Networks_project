"""Receive length-prefixed JSON messages from the inspection station."""

import argparse
from itertools import count
import json
import socket

MAX_PAYLOAD_BYTES = 1024


def encode_message(message):
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if not 1 <= len(payload) <= MAX_PAYLOAD_BYTES:
        raise ValueError("JSON payload exceeds 1024 bytes")
    return len(payload).to_bytes(4, "big") + payload


def read_exact(connection, size, *, allow_eof=False):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            if allow_eof and not data:
                return None
            raise ValueError("Connection closed mid-frame")
        data.extend(chunk)
    return bytes(data)


def receive_message(connection):
    header = read_exact(connection, 4, allow_eof=True)
    if header is None:
        return None
    size = int.from_bytes(header, "big")
    if not 1 <= size <= MAX_PAYLOAD_BYTES:
        raise ValueError(f"Invalid payload length: {size}")
    payload = read_exact(connection, size)
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    return message


def is_uint32(value):
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


def expect_message(connection, expected):
    message = receive_message(connection)
    if message is None:
        raise ValueError(f"Disconnected while waiting for {expected['msg_type']}")
    for field, value in expected.items():
        if type(message.get(field)) is not type(value) or message[field] != value:
            raise ValueError(f"Expected {expected['msg_type']}: invalid {field}")
    if not is_uint32(message.get("seq")):
        raise ValueError("Invalid TCP sequence number")
    return message


def synchronize(connection, allocate_ids):
    connection.settimeout(3)
    station = {"schema_version": 1, "device_id": 2}
    hello = expect_message(connection, {**station, "msg_type": "HELLO"})
    if ("session_id" in hello or not is_uint32(hello.get("boot_id"))
            or hello.get("device_state") not in ("SAFE_IDLE", "ARMED", "BUSY", "FAULT")):
        raise ValueError("Invalid HELLO payload")

    session_id, cmd_id = allocate_ids()
    sync = {
        "schema_version": 1,
        "msg_type": "SYNC_SESSION",
        "device_id": 0,
        "seq": 0,
        "session_id": session_id,
        "cmd_id": cmd_id,
        "target_device_id": hello["device_id"],
        "target_boot_id": hello["boot_id"],
    }
    connection.sendall(encode_message(sync))

    reference = {
        **station,
        "session_id": session_id,
        "cmd_id": cmd_id,
        "ref_msg_type": "SYNC_SESSION",
    }
    for stage, timeout in (("DELIVERY_ACK", 1), ("ACCEPTED", 2), ("COMPLETED", 2)):
        connection.settimeout(timeout)
        expected = {**reference, "msg_type": stage}
        if stage == "COMPLETED":
            expected["result"] = "READY"
        expect_message(connection, expected)
        print(f"{stage} for SYNC_SESSION cmd_id={cmd_id}, session_id={session_id}", flush=True)
    return session_id, hello["boot_id"]


def log_ready(session_id, boot_id):
    print(f"ESP32 B is ready (session_id={session_id}, boot_id={boot_id})", flush=True)


def serve(host, port, allocate_ids, on_ready=log_ready):
    with socket.create_server((host, port)) as server:
        print(f"Gateway listening on {host}:{port}", flush=True)
        while True:
            connection, address = server.accept()
            with connection:
                print(f"Connected: {address}", flush=True)
                try:
                    session_id, boot_id = synchronize(connection, allocate_ids)
                    on_ready(session_id, boot_id)
                except (OSError, ValueError) as error:
                    print(f"Closing connection: {error}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    try:
        session_ids = count(1)
        command_ids = count(100)
        serve(args.host, args.port, lambda: (next(session_ids), next(command_ids)))
    except KeyboardInterrupt:
        print("\nGateway stopped.")
