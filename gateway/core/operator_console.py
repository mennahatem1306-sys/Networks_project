"""Run one simulated batch using the public application interfaces."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from . import GatewayController
from .config import DEFAULTS, LIMITS, integer


def require(outcome):
    if not outcome.success:
        raise RuntimeError(outcome.as_dict())
    return outcome


def run_batch(path, verdicts):
    core = GatewayController(path)
    for device in (1, 2):
        session = require(core.open_device_session(device)).data["session_id"]
        require(core.on_device_status_changed(device, session, True))

    def operator(kind, **fields):
        cmd_id = require(core.reserve_command_id()).data["cmd_id"]
        return require(core.submit(dict(schema_version=1, msg_type=kind, cmd_id=cmd_id, **fields)))

    def response(request, device, kind, **fields):
        return require(core.on_device_event(dict(request, msg_type=kind, device_id=device, **fields)))

    def control(request, device, result):
        ids = {key: request[key] for key in ("schema_version", "cmd_id", "session_id", "msg_type")}
        for event in ("DELIVERY_ACK", "ACCEPTED"):
            response(ids, device, event, ref_msg_type=request["msg_type"])
        response(ids, device, "COMPLETED", ref_msg_type=request["msg_type"], result=result)

    if core.get_status_snapshot()["state"] == "FAULT":
        operator("RESET_FAULT")
    operator("SELECT_MODE", operating_mode="PRODUCTION")
    setup = operator("SET_BATCH_CONFIG", **(DEFAULTS | {"target_parts": len(verdicts)}))
    for device, request in enumerate(setup.outbound, 1):
        control(request, device, "CONFIG_APPLIED")
    started = operator("START_BATCH", target_parts=len(verdicts))
    arm, = started.outbound
    control(arm, 1, "ARMED")

    for verdict in verdicts:
        permit, = require(core.request_production_permit()).outbound
        require(core.record_transport(1, permit, "SENT"))
        outcome = response(permit, 1, "PART_PRODUCED", result="PRODUCED")
        print(f"Part {permit['part_id']} recorded and queued as job {outcome.data['job_id']}")
        job, = require(core.next_inspection_job()).outbound
        require(core.record_transport(2, job, "SENT"))
        response(job, 2, "INSPECTION_RESULT", result=verdict)
        print(f"Job {job['job_id']} recorded with simulated verdict {verdict}")
    require(core.on_device_event(dict(schema_version=1, device_id=1, msg_type="PRODUCTION_FINISHED",
                                      session_id=arm["session_id"], cmd_id=arm["cmd_id"],
                                      batch_id=arm["batch_id"], produced_total=len(verdicts))))
    final = core.get_batch_report(started.data["batch_id"])
    assert final["batch_status"] == "COMPLETED", final
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verdicts", nargs="*", choices=("PASS", "FAIL"), metavar="PASS|FAIL")
    args = parser.parse_args()
    verdicts = args.verdicts or ["PASS", "FAIL", "PASS"]
    if not integer(len(verdicts), *LIMITS["target_parts"]):
        parser.error("Supply between 1 and 1000 verdicts.")
    print("Simulation using a temporary database")
    with TemporaryDirectory() as work:
        print(json.dumps(run_batch(Path(work) / "demo.sqlite3", verdicts), indent=2))


if __name__ == "__main__":
    main()
