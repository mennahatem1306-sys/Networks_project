import csv
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from export_trace import export_trace
from test_gateway import Peer, frame, receive


class ChunkSocket:
    def __init__(self, data, chunk=1):
        self.data = data
        self.chunk = chunk

    def recv(self, count):
        size = min(count, self.chunk)
        result, self.data = self.data[:size], self.data[size:]
        return result


class FramingTests(unittest.TestCase):
    def test_fragmented_and_coalesced(self):
        messages = [
            {"msg_type": "HELLO", "boot_id": 7},
            {"job_id": 12, "result": "PASS"},
        ]
        for chunk in (1, 3, 4096):
            peer = ChunkSocket(b"".join(frame(m) for m in messages), chunk)
            self.assertEqual([receive(peer), receive(peer)], messages)

    def test_invalid_lengths(self):
        for size in (0, 1025, 0xFFFFFFFF):
            with self.assertRaises(ValueError):
                receive(ChunkSocket(struct.pack("!I", size)))

    def test_closed_partial_frame(self):
        with self.assertRaises(ConnectionError):
            receive(ChunkSocket(b"\x00\x00"))

    def test_payload_limit(self):
        with self.assertRaises(ValueError):
            frame({"data": "x" * 1024})

    def test_wait_preserves_unmatched_messages(self):
        peer = Peer(None, io.StringIO())
        first = {"msg_type": "INSPECTION_RESULT", "job_id": 3}
        second = {"msg_type": "COMPLETED", "cmd_id": 9}
        peer.inbox.put(first)
        peer.inbox.put(second)
        self.assertEqual(peer.wait("COMPLETED", cmd_id=9), second)
        self.assertEqual(peer.wait("INSPECTION_RESULT", job_id=3), first)

    def test_wait_timeout(self):
        peer = Peer(None, io.StringIO())
        with self.assertRaises(TimeoutError):
            peer.wait("ACCEPTED", timeout=0.01)

    def test_csv_keeps_original_identifiers_and_units(self):
        event = {
            "monotonic_s": 12.5,
            "direction": "RX",
            "message": {
                "msg_type": "INSPECTION_RESULT",
                "job_id": 27,
                "part_id": 15,
                "inspection_elapsed_ms": 2001,
                "result": "PASS",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            source, target = (
                Path(directory) / "trace.jsonl",
                Path(directory) / "trace.csv",
            )
            source.write_text(json.dumps(event) + "\n", encoding="utf-8")
            export_trace(source, target)
            with target.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["job_id"], "27")
            self.assertEqual(rows[0]["inspection_elapsed_ms"], "2001")
            self.assertEqual(rows[0]["monotonic_s"], "12.5")


if __name__ == "__main__":
    unittest.main()
