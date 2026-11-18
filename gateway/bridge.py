"""Connect the durable Gateway Core to the two production transports."""

import json
import logging
from time import monotonic

from .core import GatewayController
from .core.gateway import DONE
from .transports.inspection_link import InspectionLink
from .transports.production_link import ProductionLink

LOG = logging.getLogger("uvicorn.error")
PROTOCOLS = {1: "USB", 2: "TCP"}
STAGES = {"DELIVERED", "ACCEPTED", "COMPLETED", "REJECTED", "UNRESOLVED"}


class CommandError(Exception):
    def __init__(self, status, code, reason):
        super().__init__(reason)
        self.status = status
        self.code = code


def command_event(row):
    stage = "FAILED" if row["stage"] == "UNRESOLVED" else row["stage"]
    event = dict(schema_version=1, msg_type="COMMAND_EVENT", cmd_id=row["cmd_id"],
                 event=stage, event_id=row["event_id"], created_at=row["created_at"])
    return event | {key: row[key] for key in ("result", "error_code") if row[key] is not None}


class TeamGateway:
    def __init__(self, port, address, database, *, clock=monotonic, **serial_options):
        self.core = GatewayController(database, clock=clock)
        self.serial = ProductionLink(port, self, clock=clock, **serial_options)
        self.tcp = InspectionLink(address, self, clock)
        self.links = {1: self.serial, 2: self.tcp}
        self.events = []
        self.recycle = False
        self.cursor = self.core.latest_event_id()

    def close(self):
        self.serial.close()
        self.tcp.close()

    def open_session(self, device):
        result = self.core.open_device_session(device)
        if not result.success:
            raise OSError(result.reason)
        return result.data["session_id"], result.data["sync_cmd_id"]

    def online(self, device, session):
        result = self.core.on_device_status_changed(device, session, True)
        if not result.success:
            raise OSError(result.reason)

    def fault(self, device, reason):
        self.core.on_communication_fault(f"ESP32 {chr(64 + device)}: {reason}")
        self.recycle = True

    def sent(self, device, request, seq, retry_index):
        result = self.core.record_transport(device, request, "RETRY" if retry_index else "SENT",
                                          protocol=PROTOCOLS[device], seq=seq, retry_index=retry_index)
        if not result.success:
            raise OSError(f"{result.error_code}: {result.reason}")

    def receive(self, message, seq):
        device = message["device_id"]
        outcome = self.core.on_device_event(message, protocol=PROTOCOLS[device], seq=seq)
        if outcome.error_code == "DEVICE_REJECTED":
            self.recycle = True
        elif outcome.error_code == "DATABASE_ERROR":
            self.fault(device, outcome.reason)
        elif not outcome.success:
            LOG.warning("Ignored %s from station %s: %s", message["msg_type"], device, outcome.error_code)
        return outcome

    def _dispatch(self, request):
        kind = request["msg_type"]
        device = 2 if kind in {"INSPECT_PART", "SET_INSPECTION_CONFIG"} else 1
        timeout = 2
        expected = DONE.get(kind)
        if kind in {"INSPECT_PART", "PRODUCTION_PERMIT"}:
            duration_field = "inspection_time_ms" if device == 2 else "cycle_time_ms"
            expected = "VERDICT" if device == 2 else "PRODUCED"
            timeout += self.core.get_configuration(request["config_version"])[duration_field] / 1000
        elif kind in {"PAUSE_PRODUCTION", "STOP_PRODUCTION"}:
            snapshot = self.core.get_status_snapshot()
            part_id = snapshot["outstanding_permit_part_id"]
            if part_id:
                timeout += self.core.get_permit_configuration(part_id)["cycle_time_ms"] / 1000
        try:
            self.links[device].send_request(request, expected, timeout)
        except (OSError, ValueError) as error:
            self.fault(device, str(error))

    def _recycle_links(self):
        self.serial.close()
        self.tcp.disconnect()
        self.recycle = False

    def tick(self):
        state = self.core.get_status_snapshot()["state"]
        device_state = "BUSY" if state == "RUNNING" else "SAFE_IDLE"
        self.serial.tick(device_state)
        if not self.recycle:
            self.tcp.tick(device_state)
        if not self.recycle and state == "RUNNING":
            for next_work in (self.core.next_inspection_job, self.core.request_production_permit):
                work = next_work()
                for request in work.outbound:
                    self._dispatch(request)
                if self.recycle:
                    break
        if self.recycle:
            self._recycle_links()
        self._collect_events()

    def _collect_events(self):
        for row in self.core.read_events(self.cursor):
            self.cursor = row["event_id"]
            if row["operator_command"] and row["stage"] in STAGES:
                self.events.append(command_event(row))
            if row["msg_type"] in {"BATCH_COMPLETED", "BATCH_ABORTED"}:
                report = self.core.get_batch_report(row["batch_id"])
                self.events.append(dict(schema_version=1, msg_type=row["msg_type"], **report))
            elif row["msg_type"] == "INSPECTION_RESULT" and row["stage"] == "COMPLETED":
                message = json.loads(row["payload"])
                self.events.append(message | {"verdict": message["result"], "event_id": row["event_id"]})

    def reserve_id(self):
        result = self.core.reserve_command_id()
        if not result.success:
            raise CommandError(503, result.error_code, result.reason)
        return result.data["cmd_id"]

    def submit(self, message):
        outcome = self.core.submit(message)
        for request in outcome.outbound:
            self._dispatch(request)
            if self.recycle:
                self._recycle_links()
                break
        if not outcome.success:
            status = {"UNKNOWN_CMD_ID": 404, "DATABASE_ERROR": 503, "ID_EXHAUSTED": 503,
                      "INVALID_REQUEST": 422, "INVALID_VALUE": 422}.get(outcome.error_code, 409)
            if outcome.error_code in {"DUPLICATE_ID_CONFLICT", "UNKNOWN_CMD_ID", "DATABASE_ERROR", "ID_EXHAUSTED"}:
                raise CommandError(status, outcome.error_code, outcome.reason)
            record = self.command_status(message["cmd_id"])
            if record is None:
                raise CommandError(status, outcome.error_code, outcome.reason)
            record["error"] = dict(status=status, error_code=outcome.error_code, reason=outcome.reason)
            return record
        return self.command_status(message["cmd_id"])

    def command_status(self, cmd_id):
        record = self.core.get_command_status(cmd_id)
        if record is None or record["target_device"] is not None:
            return None
        events = [command_event(row) for row in record["events"] if row["stage"] in STAGES]
        return dict(cmd_id=cmd_id, payload=record["payload"], events=events)

    def batch_report(self, batch_id):
        return self.core.get_batch_report(batch_id)

    def snapshot(self):
        status = self.core.get_status_snapshot()
        status["config_version"] = status["config_version"] or 0
        for key in ("esp32_a_online", "esp32_b_online"):
            status[key] = status[key] is True
        status.update(mock=False, can_reset_fault=(status["state"] == "FAULT" and
                      status["esp32_a_online"] and status["esp32_b_online"]),
                      start_pending=status["batch_status"] == "ARMING",
                      mode_note="Benchmark execution is deferred to Milestone 2.")
        return status
