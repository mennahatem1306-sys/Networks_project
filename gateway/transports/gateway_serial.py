"""Read Zeina's SLIP/ATR production messages over USB serial."""

import argparse
from binascii import crc_hqx
import json
from struct import Struct
from time import monotonic

END = 0xC0
ESC = 0xDB
ESCAPED = {0xDC: END, 0xDD: ESC}
HEADER = Struct(">HIH")
MAX_PAYLOAD = 512
MAX_RECORD = HEADER.size + MAX_PAYLOAD + 2


class SlipDecoder:
    def __init__(self):
        self.record = bytearray()
        self.in_frame = False
        self.escaped = False

    def feed(self, data):
        for byte in data:
            if byte == END:
                record = bytes(self.record) if self.in_frame and not self.escaped else b""
                self.record.clear()
                self.in_frame = True
                self.escaped = False
                if record:
                    yield record
                continue
            if not self.in_frame:
                continue
            if self.escaped:
                self.escaped = False
                if byte not in ESCAPED:
                    self.in_frame = False
                    continue
                byte = ESCAPED[byte]
            elif byte == ESC:
                self.escaped = True
                continue
            self.record.append(byte)
            if len(self.record) > MAX_RECORD:
                self.in_frame = False


def decode_record(record):
    if len(record) < HEADER.size + 2:
        raise ValueError("Incomplete ATR record")
    test_id, seq, payload_len = HEADER.unpack_from(record)
    if test_id != 0 or not 1 <= payload_len <= MAX_PAYLOAD:
        raise ValueError("Invalid production ATR header")
    if len(record) != HEADER.size + payload_len + 2:
        raise ValueError("ATR length mismatch")
    if crc_hqx(record[:-2], 0xFFFF) != int.from_bytes(record[-2:], "big"):
        raise ValueError("ATR CRC mismatch")
    message = json.loads(record[HEADER.size:-2].decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    return seq, message


def listen_for_hello(port_name, seconds):
    import serial

    decoder = SlipDecoder()
    with serial.Serial(port_name, 115200, timeout=0.25) as port:
        print(f"Listening on {port_name}. Press EN/RESET if needed.", flush=True)
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            for record in decoder.feed(port.read(port.in_waiting or 1)):
                try:
                    seq, message = decode_record(record)
                except ValueError as error:
                    print(f"Discarded frame: {error}", flush=True)
                    continue
                if (message.get("msg_type") == "HELLO"
                        and type(message.get("schema_version")) is int
                        and message["schema_version"] == 1
                        and type(message.get("device_id")) is int
                        and message["device_id"] == 1
                        and type(message.get("boot_id")) is int
                        and 0 <= message["boot_id"] <= 0xFFFFFFFF):
                    print(f"ATR seq={seq}: {json.dumps(message)}", flush=True)
                    return 0
    print("No valid ESP32 A HELLO received.", flush=True)
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--seconds", type=float, default=15)
    args = parser.parse_args()
    raise SystemExit(listen_for_hello(args.port, args.seconds))
