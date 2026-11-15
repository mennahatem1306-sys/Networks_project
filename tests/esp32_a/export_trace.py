"""Export native firmware traces with a virtual clock; no physical USB capture."""

import argparse
import binascii
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

import test_firmware as firmware


def annotate(row):
    result = dict(row, hardware_test=False)
    wire = bytes.fromhex(row.get("raw_hex", ""))
    if not wire:
        return result
    try:
        if wire[0] != 0xC0 or wire[-1] != 0xC0:
            raise ValueError("Not a complete SLIP frame")
        record = wire[1:-1].replace(b"\xdb\xdc", b"\xc0").replace(b"\xdb\xdd", b"\xdb")
        test_id, seq, length = struct.unpack(">HIH", record[:8])
        result.update(
            test_id=test_id,
            seq=seq,
            payload_len=length,
            crc_valid=binascii.crc_hqx(record[:-2], 0xFFFF) == struct.unpack(">H", record[-2:])[0],
        )
        result["message"] = json.loads(record[8:-2])
    except (ValueError, struct.error, IndexError) as exc:
        result["decode_error"] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    firmware.EXECUTABLE = args.executable.resolve()
    station = firmware.FirmwareClient()
    try:

        def record_scenario(name):
            station.trace.append(dict(direction="SCENARIO", time_ms=station.now, name=name))

        record_scenario("boot_and_setup")
        station.tick(0)
        station.sync()
        station.configure(cycle=100)
        station.start(target=3)
        record_scenario("in_progress_duplicate_and_lost_report_confirmation")
        station.permit()
        station.tick(50)
        station.permit()
        station.tick(100)
        station.advance(1100)
        station.ack()
        record_scenario("completed_duplicate_replays_without_increment")
        station.permit()
        station.ack()
        record_scenario("pause_drains_active_work_then_resume")
        station.permit(cmd=104, part=16)
        station.tick(1150)
        station.send("PAUSE_PRODUCTION", cmd_id=105, batch_id=1)
        station.tick(1200)
        station.ack(cmd=104)
        station.send("RESUME_PRODUCTION", cmd_id=106, batch_id=1)
        record_scenario("corrupted_frame_is_rejected_then_original_permit_succeeds")
        payload = dict(
            schema_version=1,
            device_id=0,
            session_id=9,
            msg_type="PRODUCTION_PERMIT",
            cmd_id=107,
            batch_id=1,
            part_id=17,
            config_version=1,
        )
        station.raw(firmware.frame(payload, station.seq, corrupt=True))
        station.seq += 1
        station.permit(cmd=107, part=17)
        station.tick(1300)
        station.ack(cmd=107)
        station.ack(ref="PRODUCTION_FINISHED", cmd=102)
        record_scenario("gateway_heartbeat_loss_abandons_long_cycle")
        station.heartbeat()
        station.configure(cmd=108, version=2, cycle=5000)
        station.start(cmd=109, batch=2)
        station.permit(cmd=110, part=18, batch=2, version=2)
        station.tick(4300)
        record_scenario("new_session_safe_idle_and_old_session_rejection")
        station.sync(sid=10, cmd=200)
        station.send(
            "PRODUCTION_PERMIT", session_id=9, cmd_id=110, batch_id=2, part_id=18, config_version=2
        )
        assert station.state()[1:3] == ["SAFE_IDLE", "0"]
        rows = [annotate(row) for row in station.trace]
    finally:
        station.close()
    root = Path(__file__).resolve().parents[2]
    sources = {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((root / "esp32_a_production").glob("*"))
        if path.suffix in (".cpp", ".h", ".ino")
    }
    metadata = dict(
        direction="METADATA",
        hardware_test=False,
        source="native execution of the actual portable Arduino C++ sources",
        clock="virtual monotonic milliseconds",
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),
        source_sha256=sources,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in [metadata] + rows) + "\n",
        encoding="utf-8",
    )
    print(
        f"Saved {len(rows)} software trace events to {args.output}; no physical hardware was used."
    )


if __name__ == "__main__":
    main()
