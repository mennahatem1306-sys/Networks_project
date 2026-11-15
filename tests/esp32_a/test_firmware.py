"""Test the firmware C++ core through the native serial driver."""

import argparse
import binascii
import json
import pathlib
import struct
import subprocess
import sys
import unittest
from datetime import datetime, timezone

EXECUTABLE = None


def frame(payload, seq=0, corrupt=False):
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    record = struct.pack(">HIH", 0, seq, len(payload)) + payload
    crc = binascii.crc_hqx(record, 0xFFFF) ^ int(corrupt)
    record += struct.pack(">H", crc)
    return b"\xc0" + record.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"


def decode(data):
    assert data[0] == data[-1] == 0xC0
    record = data[1:-1].replace(b"\xdb\xdc", b"\xc0").replace(b"\xdb\xdd", b"\xdb")
    test_id, seq, length = struct.unpack(">HIH", record[:8])
    assert test_id == 0 and len(record) == length + 10
    assert binascii.crc_hqx(record[:-2], 0xFFFF) == struct.unpack(">H", record[-2:])[0]
    result = json.loads(record[8:-2])
    assert "seq" not in result
    result["_wire_seq"] = seq
    return result


class FirmwareClient:
    def __init__(self, invalid_boot=False):
        self.process = subprocess.Popen(
            [str(EXECUTABLE)] + (["invalid-boot"] if invalid_boot else []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.seq = 0
        self.heartbeat_seq = 0
        self.now = 0
        self.sid = 9
        self.messages = []
        self.logs = []
        self.trace = []
        self._read()

    def _read(self):
        result = []
        while True:
            line = self.process.stdout.readline().strip()
            if line == "DONE":
                return result
            if not line:
                raise RuntimeError("Driver exited: " + self.process.stderr.read())
            if line.startswith("TX "):
                wire = bytes.fromhex(line[3:])
                self.trace.append(dict(direction="A_TO_GW", time_ms=self.now, raw_hex=wire.hex()))
                message = decode(wire)
                self.messages.append(message)
                result.append(message)
            elif line.startswith("LOG "):
                self.logs.append(line)
                self.trace.append(
                    dict(direction="FIRMWARE_DIAGNOSTIC", time_ms=self.now, diagnostic=line)
                )
            elif line.startswith("STATE ") or line.startswith("CRC "):
                result.append(line)

    def operation(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return self._read()

    def raw(self, data):
        self.trace.append(dict(direction="GW_TO_A", time_ms=self.now, raw_hex=data.hex()))
        return self.operation(f"RX {self.now} {data.hex()}")

    def send(self, msg_type, **fields):
        payload = dict(schema_version=1, device_id=0, session_id=self.sid, msg_type=msg_type)
        payload.update(fields)
        data = frame(payload, self.seq)
        self.seq += 1
        return self.raw(data)

    def sync(self, sid=9, cmd=100):
        self.sid = sid
        return self.send("SYNC_SESSION", cmd_id=cmd, target_device_id=1, target_boot_id=7)

    def configure(self, cmd=101, version=1, cycle=100):
        return self.send(
            "SET_PRODUCTION_CONFIG", cmd_id=cmd, config_version=version, cycle_time_ms=cycle
        )

    def start(self, target=2, cmd=102, batch=1):
        return self.send("START_PRODUCTION", cmd_id=cmd, batch_id=batch, target_parts=target)

    def permit(self, cmd=103, part=15, batch=1, version=1):
        return self.send(
            "PRODUCTION_PERMIT", cmd_id=cmd, batch_id=batch, part_id=part, config_version=version
        )

    def tick(self, now):
        self.now = now & 0xFFFFFFFF
        return self.operation(f"TICK {self.now}")

    def heartbeat(self):
        self.heartbeat_seq += 1
        return self.send("HEARTBEAT", heartbeat_seq=self.heartbeat_seq, device_state="BUSY")

    def advance(self, now):
        # Keep connectivity alive while advancing a long virtual operation.
        while self.now + 500 < now:
            self.now += 500
            self.heartbeat()
            self.tick(self.now)
        self.now = now
        self.heartbeat()
        return self.tick(now)

    def ack(self, ref="PART_PRODUCED", cmd=103):
        return self.send("DELIVERY_ACK", cmd_id=cmd, ref_msg_type=ref)

    def state(self):
        return self.operation("STATE")[0].split()

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.station = FirmwareClient()
        self.addCleanup(self.station.close)
        self.station.sync()
        self.station.configure()
        self.station.start()

    def types(self, messages):
        return [m["msg_type"] for m in messages]

    def produced(self, messages):
        return [m for m in messages if m["msg_type"] == "PART_PRODUCED"]

    def test_crc_reference(self):
        self.assertEqual(self.station.operation("CRC"), ["CRC 10673"])

    def test_start_only_arms_and_no_permit_means_no_part(self):
        self.station.advance(1500)
        self.assertEqual(self.station.state()[1:3], ["ARMED", "0"])

    def test_one_permit_one_full_cycle_three_phases(self):
        self.assertEqual(self.types(self.station.permit()), ["DELIVERY_ACK", "ACCEPTED"])
        self.assertEqual(self.produced(self.station.tick(99)), [])
        report = self.produced(self.station.tick(100))[0]
        self.assertEqual(
            (report["cmd_id"], report["part_id"], report["batch_id"], report["config_version"]),
            (103, 15, 1, 1),
        )
        self.assertEqual(self.station.state()[2], "1")

    def test_duplicate_in_progress_does_not_restart_timer(self):
        self.station.permit()
        self.station.tick(50)
        self.station.permit()
        self.assertEqual(len(self.produced(self.station.tick(100))), 1)

    def test_duplicate_completed_replays_without_increment(self):
        self.station.permit()
        self.station.tick(100)
        self.station.ack()
        self.assertEqual(len(self.produced(self.station.permit())), 1)
        self.assertEqual(self.station.state()[2], "1")

    def test_conflicting_id_and_reused_part_are_rejected(self):
        self.station.permit()
        self.assertEqual(self.station.permit(part=16)[-1]["error_code"], "DUPLICATE_ID_CONFLICT")
        self.station.tick(100)
        self.station.ack()
        self.assertEqual(self.station.permit(cmd=104)[-1]["error_code"], "DUPLICATE_ID_CONFLICT")

    def test_lost_report_ack_retries_same_ids_new_sequences(self):
        self.station.permit()
        original = self.produced(self.station.tick(100))[0]
        retry = self.produced(self.station.advance(1100))[0]
        self.assertGreater(retry["_wire_seq"], original["_wire_seq"])
        original.pop("_wire_seq")
        retry.pop("_wire_seq")
        self.assertEqual(original, retry)
        self.assertEqual(self.station.state()[2], "1")

    def test_report_retry_exhaustion_faults(self):
        self.station.permit()
        self.station.tick(100)
        self.station.advance(4200)
        self.assertEqual(self.station.state()[1], "FAULT")
        self.assertEqual(len(self.produced(self.station.messages)), 4)
        self.assertTrue(any("RETRY_EXHAUSTED" in line for line in self.station.logs))

    def test_ack_ref_must_match_and_ack_is_not_acked(self):
        self.station.permit()
        self.station.tick(100)
        self.assertEqual(self.station.ack(ref="PRODUCTION_PERMIT"), [])
        self.assertEqual(len(self.produced(self.station.advance(1100))), 1)
        self.assertEqual(self.station.ack(), [])
        self.assertEqual(self.produced(self.station.advance(2100)), [])

    def test_queue_block_is_absence_of_permits(self):
        self.station.permit()
        self.station.tick(100)
        self.station.ack()
        self.station.advance(2000)
        self.assertEqual(self.station.state()[1:3], ["ARMED", "1"])

    def test_pause_drains_current_part_resume_needs_new_permit(self):
        self.station.permit()
        self.station.tick(50)
        pause = self.station.send("PAUSE_PRODUCTION", cmd_id=104, batch_id=1)
        self.assertEqual(self.types(pause), ["DELIVERY_ACK", "ACCEPTED"])
        self.assertEqual(self.station.permit(cmd=105, part=16)[-1]["error_code"], "INVALID_STATE")
        finish = self.station.tick(100)
        self.assertEqual(len(self.produced(finish)), 1)
        self.assertTrue(any(m.get("result") == "PAUSED" for m in finish))
        self.station.ack()
        self.station.send("RESUME_PRODUCTION", cmd_id=106, batch_id=1)
        self.assertEqual(self.produced(self.station.tick(1000)), [])
        self.station.permit(cmd=107, part=16)
        self.assertEqual(self.produced(self.station.tick(1099)), [])
        self.assertEqual(len(self.produced(self.station.tick(1100))), 1)

    def test_stop_finishes_active_then_refuses_new_work(self):
        self.station.permit()
        self.station.tick(50)
        self.station.send("STOP_PRODUCTION", cmd_id=104, batch_id=1)
        finish = self.station.tick(100)
        self.assertEqual(len(self.produced(finish)), 1)
        self.assertTrue(any(m.get("result") == "STOPPED" for m in finish))
        self.assertEqual(self.station.state()[1], "SAFE_IDLE")
        self.assertEqual(self.station.permit(cmd=105, part=16)[-1]["error_code"], "INVALID_STATE")

    def test_live_config_keeps_active_cycle_snapshot(self):
        self.station.permit()
        self.station.tick(50)
        self.station.configure(cmd=104, version=2, cycle=300)
        self.assertEqual(self.produced(self.station.tick(100))[0]["config_version"], 1)
        self.station.ack()
        self.station.permit(cmd=105, part=16, version=2)
        self.assertEqual(self.produced(self.station.tick(399)), [])
        self.assertEqual(self.produced(self.station.tick(400))[0]["config_version"], 2)

    def test_invalid_config_leaves_previous_config(self):
        self.assertEqual(
            self.station.configure(cmd=104, version=2, cycle=0)[-1]["error_code"], "INVALID_VALUE"
        )
        self.station.permit()
        self.assertEqual(len(self.produced(self.station.tick(100))), 1)

    def test_wrong_version_rejected(self):
        self.assertEqual(
            self.station.permit(version=2)[-1]["error_code"], "CONFIG_VERSION_CONFLICT"
        )

    def test_full_target_finishes_production_only(self):
        self.station.permit()
        self.station.tick(100)
        self.station.ack()
        self.station.permit(cmd=104, part=16)
        messages = self.station.tick(200)
        finished = [m for m in messages if m["msg_type"] == "PRODUCTION_FINISHED"][0]
        self.assertEqual((finished["cmd_id"], finished["produced_total"]), (102, 2))
        self.assertNotIn("BATCH_COMPLETED", self.types(self.station.messages))
        self.assertEqual(self.station.state()[2], "2")

    def test_stop_after_all_parts_produced_while_inspections_may_remain(self):
        self.station.permit()
        self.station.tick(100)
        self.station.ack()
        self.station.permit(cmd=104, part=16)
        self.station.tick(200)
        self.assertEqual(
            self.station.send("STOP_PRODUCTION", cmd_id=105, batch_id=1)[-1]["result"], "STOPPED"
        )

    def test_heartbeat_loss_abandons_active_work(self):
        self.station.configure(cmd=104, version=2, cycle=5000)
        self.station.permit(version=2)
        self.station.tick(3000)
        self.assertEqual(self.station.state()[1], "FAULT")
        self.assertEqual(self.produced(self.station.tick(6000)), [])

    def test_heartbeat_continues_during_long_cycle(self):
        self.station.configure(cmd=104, version=2, cycle=5000)
        self.station.permit(version=2)
        self.station.advance(5000)
        self.assertEqual(len(self.produced(self.station.messages)), 1)
        self.assertGreaterEqual(self.types(self.station.messages).count("HEARTBEAT"), 5)

    def test_busy_reconnect_discards_old_timer_and_old_session(self):
        self.station.permit()
        self.station.tick(50)
        self.station.sync(sid=10, cmd=200)
        self.assertEqual(self.station.state()[1:3], ["SAFE_IDLE", "0"])
        self.assertEqual(self.produced(self.station.tick(100)), [])
        self.assertEqual(self.station.sync(sid=9, cmd=100)[-1]["error_code"], "STALE_MESSAGE")

    def test_same_handshake_retry_does_not_reset_active_timer(self):
        self.station.permit()
        self.station.tick(50)
        self.station.sync()
        self.assertEqual(len(self.produced(self.station.tick(100))), 1)

    def test_gateway_loss_requires_fresh_session_even_before_commands(self):
        other = FirmwareClient()
        self.addCleanup(other.close)
        other.sync(sid=0)
        other.tick(3000)
        self.assertEqual(other.sync(sid=0)[-1]["error_code"], "STALE_MESSAGE")

    def test_bad_crc_cannot_execute_and_next_frame_recovers(self):
        payload = dict(
            schema_version=1,
            device_id=0,
            session_id=9,
            msg_type="PRODUCTION_PERMIT",
            cmd_id=103,
            batch_id=1,
            part_id=15,
            config_version=1,
        )
        messages = self.station.raw(frame(payload, self.station.seq, corrupt=True))
        self.station.seq += 1
        self.assertEqual(messages[-1]["error_code"], "CRC_ERROR")
        self.assertNotIn("ACCEPTED", self.types(messages))
        self.station.tick(100)
        self.assertEqual(self.station.state()[2], "0")
        self.assertEqual(self.types(self.station.permit()), ["DELIVERY_ACK", "ACCEPTED"])

    def test_malformed_json_duplicate_keys_and_wrong_primitives(self):
        prefix = b'{"schema_version":1,"device_id":0,"session_id":9,"msg_type":"PRODUCTION_PERMIT",'
        bad = [
            prefix + b'"cmd_id":103,"cmd_id":104}',
            prefix + b'"cmd_id":103,}',
            prefix + b'"cmd_id":01}',
            prefix + b'"cmd_id":103}garbage',
            prefix + b'"cmd_id":103,"x":"\\u0000"}',
            prefix + b'"cmd_id":103,"x":"\xff"}',
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                result = self.station.raw(frame(payload, self.station.seq))
                self.station.seq += 1
                self.assertEqual(result[-1]["error_code"], "INVALID_JSON")
        self.assertEqual(self.station.permit(cmd=True)[-1]["error_code"], "MISSING_FIELD")
        self.assertEqual(self.station.permit(cmd=103.0)[-1]["error_code"], "MISSING_FIELD")
        self.assertEqual(self.station.state()[2], "0")

    def test_fragmented_frame_bad_escape_and_oversize_resync(self):
        payload = dict(
            schema_version=1,
            device_id=0,
            session_id=9,
            msg_type="PRODUCTION_PERMIT",
            cmd_id=103,
            batch_id=1,
            part_id=15,
            config_version=1,
        )
        packet = frame(payload, self.station.seq)
        self.station.seq += 1
        for byte in packet[:-1]:
            self.assertEqual(self.station.raw(bytes([byte])), [])
        self.assertIn("ACCEPTED", self.types(self.station.raw(packet[-1:])))
        self.station.raw(b"\xc0\xdb\x00\xc0")
        self.station.raw(b"\xc0" + b"a" * 600 + b"\xc0")
        self.station.tick(100)
        self.assertEqual(self.station.state()[2], "1")

    def test_embedded_serial_seq_is_rejected(self):
        self.assertEqual(
            self.station.send(
                "PRODUCTION_PERMIT", cmd_id=103, batch_id=1, part_id=15, config_version=1, seq=99
            )[-1]["error_code"],
            "INVALID_VALUE",
        )

    def test_time_wraparound(self):
        # Fresh session timestamp near millis() wrap; elapsed arithmetic must wrap safely.
        self.station.now = 0xFFFFFF00
        self.station.sync(sid=10, cmd=200)
        self.station.configure(cmd=201, cycle=500)
        self.station.start(cmd=202)
        self.station.permit(cmd=203)
        self.assertEqual(self.produced(self.station.tick(243)), [])
        self.assertEqual(len(self.produced(self.station.tick(244))), 1)

    def test_invalid_boot_counter_refuses_sync(self):
        other = FirmwareClient(invalid_boot=True)
        self.addCleanup(other.close)
        self.assertEqual(other.sync()[-1]["error_code"], "BOOT_COUNTER_ERROR")
        self.assertEqual(other.state()[1], "FAULT")

    def test_slip_escaping_in_transport_header(self):
        self.station.seq = 0xC0DB
        self.assertEqual(self.types(self.station.permit()), ["DELIVERY_ACK", "ACCEPTED"])
        self.assertEqual(len(self.produced(self.station.tick(100))), 1)

    def test_maximum_batch_retains_earliest_permit(self):
        self.station.send("STOP_PRODUCTION", cmd_id=104, batch_id=1)
        self.station.sync(sid=10, cmd=200)
        self.station.configure(cmd=201, cycle=1)
        self.station.start(cmd=202, target=1000, batch=2)
        for i in range(1000):
            self.station.permit(cmd=203 + i, part=5000 + i, batch=2)
            self.station.tick(i + 1)
            self.station.ack(cmd=203 + i)
            if i % 250 == 0:
                self.station.heartbeat()
        self.assertEqual(self.station.state()[2], "1000")
        replay = self.station.permit(cmd=203, part=5000, batch=2)
        self.assertEqual(len(self.produced(replay)), 1)
        self.assertEqual(self.station.state()[2], "1000")

    def test_terminal_report_ack_allows_next_batch(self):
        self.station.permit()
        self.station.tick(100)
        self.station.ack()
        self.station.permit(cmd=104, part=16)
        self.station.tick(200)
        self.station.ack(cmd=104)
        self.assertEqual(self.station.start(cmd=105, batch=2)[-1]["error_code"], "INVALID_STATE")
        self.station.ack(ref="PRODUCTION_FINISHED", cmd=102)
        self.assertEqual(self.station.start(cmd=106, batch=2)[-1]["result"], "ARMED")

    def test_malformed_negative_ack_does_not_fault_or_cancel_report(self):
        self.station.permit()
        self.station.tick(100)
        result = self.station.send("REJECTED", cmd_id=103, ref_msg_type="PART_PRODUCED")
        self.assertEqual(result[-1]["error_code"], "MISSING_FIELD")
        self.assertEqual(self.station.state()[1], "ARMED")
        self.assertEqual(len(self.produced(self.station.advance(1100))), 1)

    def test_ack_with_wrong_optional_type_does_not_confirm_report(self):
        self.station.permit()
        self.station.tick(100)
        result = self.station.send("ACCEPTED", cmd_id=103, ref_msg_type="PART_PRODUCED", result=4)
        self.assertEqual(result[-1]["error_code"], "INVALID_TYPE")
        self.assertEqual(len(self.produced(self.station.advance(1100))), 1)

    def test_valid_negative_ack_faults_without_acknowledging_ack(self):
        self.station.permit()
        self.station.tick(100)
        result = self.station.send(
            "REJECTED",
            cmd_id=103,
            ref_msg_type="PART_PRODUCED",
            error_code="DATABASE_ERROR",
            reason="Result could not be committed",
        )
        self.assertEqual(self.station.state()[1], "FAULT")
        self.assertEqual(self.types(result), ["ERROR"])

    def test_failed_final_part_transmit_cannot_emit_finished_or_paused(self):
        self.station.send("STOP_PRODUCTION", cmd_id=104, batch_id=1)
        self.station.sync(sid=10, cmd=200)
        self.station.configure(cmd=201)
        self.station.start(cmd=202, target=1)
        self.station.permit(cmd=203)
        self.station.send("PAUSE_PRODUCTION", cmd_id=204, batch_id=1)
        self.station.operation("FAIL_AFTER 0")
        result = self.station.tick(100)
        self.assertEqual(result, [])
        self.assertEqual(self.station.state()[1], "FAULT")
        self.assertEqual(self.station.state()[2], "1")  # Work finished; transport failed.

    def test_acceptance_transmit_failure_cannot_start_production(self):
        self.station.operation("FAIL_AFTER 1")  # Delivery ACK succeeds; ACCEPTED cannot be queued.
        self.assertEqual(self.types(self.station.permit()), ["DELIVERY_ACK"])
        self.assertEqual(self.station.state()[1], "FAULT")
        self.assertEqual(self.produced(self.station.tick(100)), [])
        self.assertEqual(self.station.state()[2], "0")

    def test_replayed_heartbeat_does_not_extend_health_deadline(self):
        self.station.heartbeat()
        self.station.tick(2000)
        self.station.heartbeat_seq -= 1
        self.assertEqual(self.station.heartbeat()[-1]["error_code"], "STALE_MESSAGE")
        self.station.tick(3000)
        self.assertEqual(self.station.state()[1], "FAULT")

    def test_config_duplicate_replay_does_not_revert_newer_setting(self):
        self.station.configure(cmd=104, version=2, cycle=300)
        self.assertEqual(self.station.configure()[-1]["result"], "CONFIG_APPLIED")
        self.station.permit(version=2)
        self.assertEqual(self.produced(self.station.tick(100)), [])
        self.assertEqual(len(self.produced(self.station.tick(300))), 1)

    def test_command_cache_full_never_evicts_accepted_permit(self):
        self.station.permit()
        self.station.tick(100)
        self.station.ack()
        for i in range(1533):  # config + start + permit already occupy three slots.
            result = self.station.configure(cmd=1000 + i)
            self.assertEqual(result[-1]["result"], "CONFIG_APPLIED")
        self.assertEqual(self.station.configure(cmd=4000)[-1]["error_code"], "CACHE_FULL")
        self.assertEqual(len(self.produced(self.station.permit())), 1)
        self.assertEqual(self.station.state()[2], "1")

    def test_target_and_cycle_bounds_are_checked(self):
        self.assertEqual(
            self.station.configure(cmd=104, version=2, cycle=3600001)[-1]["error_code"],
            "INVALID_VALUE",
        )
        self.assertEqual(
            self.station.configure(cmd=105, version=2, cycle=3600000)[-1]["result"],
            "CONFIG_APPLIED",
        )
        self.assertEqual(
            self.station.start(cmd=106, target=0, batch=2)[-1]["error_code"], "INVALID_VALUE"
        )
        self.assertEqual(
            self.station.start(cmd=107, target=1001, batch=2)[-1]["error_code"], "INVALID_VALUE"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path)
    args, remaining = parser.parse_known_args()
    EXECUTABLE = args.executable.resolve()
    program = unittest.main(argv=[sys.argv[0]] + remaining, verbosity=2, exit=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                dict(
                    recorded_at_utc=datetime.now(timezone.utc).isoformat(),
                    hardware_test=False,
                    executable=EXECUTABLE.name,
                    tests_run=program.result.testsRun,
                    failures=len(program.result.failures),
                    errors=len(program.result.errors),
                    skipped=len(program.result.skipped),
                    passed=program.result.wasSuccessful(),
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    sys.exit(0 if program.result.wasSuccessful() else 1)
