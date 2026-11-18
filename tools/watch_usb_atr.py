"""Decode ATR records from the gateway's timestamped USB byte log."""

from binascii import crc_hqx
import json
from pathlib import Path
import re
import sys
from time import monotonic, sleep

sys.path.insert(0, str(Path.cwd()))
from gateway.transports.gateway_serial import HEADER, SlipDecoder, decode_record


def main():
    decoders = {direction: SlipDecoder() for direction in ("TX", "RX")}
    raw_line = re.compile(r"USB RAW (TX|RX) (\S+) ([0-9a-f ]+)$")
    print(json.dumps({"status": "waiting for gateway USB frames"}), flush=True)
    deadline = monotonic() + 1800
    path = Path(sys.argv[1])
    with path.open(encoding="utf-8", errors="replace") as gateway_log:
        while monotonic() < deadline:
            start = gateway_log.tell()
            line = gateway_log.readline()
            if not line.endswith("\n"):
                gateway_log.seek(start)
                sleep(0.15)
                continue
            match = raw_line.search(line.rstrip())
            if not match:
                continue
            direction, timestamp, hex_bytes = match.groups()
            for record in decoders[direction].feed(bytes.fromhex(hex_bytes)):
                event = dict(direction=direction, timestamp_utc=timestamp, atr_hex=record.hex(" "))
                if len(record) >= HEADER.size + 2:
                    test_id, seq, payload_bytes = HEADER.unpack_from(record)
                    event.update(test_id=test_id, seq=seq, payload_bytes=payload_bytes,
                                 header_hex=record[:HEADER.size].hex(" "),
                                 crc_received=f"0x{int.from_bytes(record[-2:], 'big'):04X}",
                                 crc_calculated=f"0x{crc_hqx(record[:-2], 0xFFFF):04X}")
                try:
                    _, event["payload"] = decode_record(record)
                except ValueError as exc:
                    event.update(valid=False, error=str(exc))
                else:
                    event["valid"] = True
                print(json.dumps(event), flush=True)


if __name__ == "__main__":
    main()
