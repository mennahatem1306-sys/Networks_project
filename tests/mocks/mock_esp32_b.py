"""Simulate the ESP32 B session handshake over local TCP."""

import argparse
import socket

from gateway.transports.gateway_tcp import encode_message, expect_message, is_uint32

HELLO = {
    "schema_version": 1,
    "msg_type": "HELLO",
    "device_id": 2,
    "seq": 0,
    "boot_id": 42,
    "device_state": "SAFE_IDLE",
}


def run_handshake(port):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(encode_message(HELLO))
        sync = expect_message(connection, {
            "schema_version": 1,
            "msg_type": "SYNC_SESSION",
            "device_id": 0,
            "target_device_id": HELLO["device_id"],
            "target_boot_id": HELLO["boot_id"],
        })
        if not all(is_uint32(sync.get(field)) for field in ("session_id", "cmd_id")):
            raise ValueError("Invalid SYNC_SESSION identifiers")

        reply = {
            "schema_version": 1,
            "device_id": HELLO["device_id"],
            "session_id": sync["session_id"],
            "cmd_id": sync["cmd_id"],
            "ref_msg_type": "SYNC_SESSION",
        }
        for seq, stage in enumerate(("DELIVERY_ACK", "ACCEPTED", "COMPLETED")):
            message = {**reply, "seq": seq, "msg_type": stage}
            if stage == "COMPLETED":
                message["result"] = "READY"
            connection.sendall(encode_message(message))
            print(f"Sent {stage} for cmd_id={sync['cmd_id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5001)
    run_handshake(parser.parse_args().port)
