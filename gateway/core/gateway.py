"""Serialized application controller for the production and inspection stations."""
from functools import wraps
import json
import sqlite3
from threading import RLock
from time import monotonic

from . import config, scheduler
from .database import Storage, log_event, now_text
from .messages import canonical, command, flat_message, matching_ids, ok, reject

CONTROLS = {
    "PAUSE_BATCH": ("PAUSE_PRODUCTION", "PAUSED", "BATCH_PAUSED"),
    "RESUME_BATCH": ("RESUME_PRODUCTION", "RESUMED", "BATCH_RESUMED"),
    "STOP_BATCH": ("STOP_PRODUCTION", "STOPPED", "BATCH_STOPPED"),
}
OP_FIELDS = {
    "SELECT_MODE": {"operating_mode"}, "SET_BATCH_CONFIG": set(config.DEFAULTS),
    "START_BATCH": {"target_parts"}, "RESET_FAULT": set(),
    **{kind: {"batch_id"} for kind in CONTROLS},
}
DONE = {"SET_PRODUCTION_CONFIG": "CONFIG_APPLIED", "SET_INSPECTION_CONFIG": "CONFIG_APPLIED",
        "START_PRODUCTION": "ARMED", **{kind: result for kind, result, _ in CONTROLS.values()}}
TERMINAL = {"COMPLETED", "REJECTED", "UNRESOLVED", "CANCELLED", "HISTORICAL"}


def serialized(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self.lock:
            try:
                return method(self, *args, **kwargs)
            except sqlite3.Error:
                return reject("DATABASE_ERROR", "SQLite rolled back. Do not acknowledge this outcome.")
            except OverflowError as error:
                return reject("ID_EXHAUSTED", str(error))
    return run


def one(db, sql, args=()):
    return db.execute(sql, args).fetchone()


def batch(db):
    return one(db, "SELECT * FROM batches WHERE batch_status IN ('ARMING','RUNNING','PAUSED')")


def latest(db, kind):
    return one(db, "SELECT result,payload FROM message_events WHERE msg_type=? ORDER BY event_id DESC LIMIT 1",
               (kind,))


def state(db):
    marker = latest(db, "GATEWAY_STATE")
    return marker["result"] if marker else "IDLE"


def mode(db):
    marker = latest(db, "OPERATING_MODE")
    return marker["result"] if marker else "PRODUCTION"


def transition(db, value, reason=None):
    log_event(db, "Gateway", "GATEWAY_STATE", "TRANSITION", result=value,
              payload={"reason": reason} if reason else None)


def settings(db, status="ACTIVE"):
    return one(db, "SELECT * FROM configurations WHERE status=?", (status,))


def pending_control(db, batch_id):
    return one(db, "SELECT * FROM commands WHERE batch_id=? AND target_device IS NULL "
                   "AND status='ACCEPTED' AND msg_type IN ('PAUSE_BATCH','RESUME_BATCH','STOP_BATCH')",
               (batch_id,))


def trace(db, message, stage, source, **metadata):
    ids = {key: message[key] for key in ("cmd_id", "batch_id", "part_id", "job_id", "session_id")
           if config.integer(message.get(key))}
    log_event(db, source, message["msg_type"], stage, payload=message, **ids, **metadata)


def save_command(db, request, device=None, accepted=False):
    timestamp = now_text()
    db.execute("""
        INSERT INTO commands(cmd_id,parent_cmd_id,msg_type,target_device,session_id,batch_id,
                             config_version,status,payload,created_at,accepted_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, (request["cmd_id"], request.get("parent_cmd_id"), request["msg_type"], device,
          request.get("session_id"), request.get("batch_id"), request.get("config_version"),
          "ACCEPTED" if accepted else "SUBMITTED", canonical(request), timestamp,
          timestamp if accepted else None))
    trace(db, request, "SUBMITTED", "Gateway" if device else "Operator")
    if device is None:
        trace(db, request, "DELIVERED", "Gateway")
    if accepted:
        trace(db, request, "ACCEPTED", "Gateway")


def finish_command(db, cmd_id, result):
    db.execute("UPDATE commands SET status='COMPLETED',completed_at=?,final_result=? WHERE cmd_id=?",
               (now_text(), result, cmd_id))
    log_event(db, "Gateway", "COMMAND_OUTCOME", "COMPLETED", cmd_id=cmd_id, result=result)


class GatewayController:
    """One live controller per database. Adapters own transport and device I/O."""

    def __init__(self, database, *, clock=monotonic, readiness_provider=None):
        self.storage = Storage(database)
        self.lock = RLock()
        self.clock = clock
        self.readiness_provider = readiness_provider
        self.sessions = {}
        self.timing = {}
        with self.storage.transaction() as db:
            if batch(db) or settings(db, "PENDING"):
                self._abort(db, batch(db), "GATEWAY_RESTART", fault=True)
            db.execute("UPDATE configurations SET status='SUPERSEDED' WHERE status='ACTIVE'")

    def _ready(self):
        return all(self.sessions.get(device, (None, False))[1] for device in (1, 2))

    @serialized
    def open_device_session(self, device_id):
        if type(device_id) is not int or device_id not in (1, 2):
            return reject("INVALID_DEVICE", "Expected station 1 or 2.")
        if self.sessions.get(device_id, (None, False))[1]:
            result = self.on_communication_fault("SESSION_REPLACED", device_id=device_id)
            if not result.success:
                return result
        session_id, sync_cmd_id = self.storage.reserve("session", "cmd")
        with self.storage.transaction() as db:
            log_event(db, str(device_id), "DEVICE_SESSION", "OPENED",
                      session_id=session_id, cmd_id=sync_cmd_id)
        self.sessions[device_id] = (session_id, False)
        return ok("RESERVED", session_id=session_id, sync_cmd_id=sync_cmd_id)

    @serialized
    def on_device_status_changed(self, device_id, session_id, ready):
        if type(device_id) is not int or not config.integer(session_id) or type(ready) is not bool:
            return reject("INVALID_VALUE", "Invalid readiness event.")
        current = self.sessions.get(device_id)
        if not current or current[0] != session_id:
            return reject("STALE_SESSION", "This is not the current device session.")
        if current[1] and not ready:
            return self.on_communication_fault("DEVICE_DISCONNECTED", device_id=device_id, session_id=session_id)
        with self.storage.transaction() as db:
            log_event(db, str(device_id), "DEVICE_SESSION", "READY" if ready else "NOT_READY",
                      session_id=session_id)
        self.sessions[device_id] = (session_id, ready)
        return ok("RECORDED")

    @serialized
    def reserve_command_id(self):
        cmd_id, = self.storage.reserve("cmd")
        with self.storage.transaction() as db:
            log_event(db, "Operator", "OPERATOR_ID", "RESERVED", cmd_id=cmd_id)
        return ok("RESERVED", cmd_id=cmd_id)

    @serialized
    def submit(self, message):
        if not flat_message(message) or not config.integer(message.get("cmd_id")):
            return reject("INVALID_REQUEST", "Expected a flat schema-version 1 operator command.")
        kind, cmd_id = message["msg_type"], message["cmd_id"]
        fields = OP_FIELDS.get(kind)
        if fields is None or set(message) != fields | {"schema_version", "msg_type", "cmd_id"}:
            return reject("INVALID_REQUEST", "Unexpected or missing operator fields.")
        with self.storage.transaction(write=False) as db:
            saved = one(db, "SELECT * FROM commands WHERE cmd_id=?", (cmd_id,))
            if saved:
                if saved["target_device"] is not None or saved["payload"] != canonical(message):
                    return reject("DUPLICATE_ID_CONFLICT", "Command ID belongs to another request.")
                if saved["status"] == "REJECTED":
                    return reject(saved["error_code"], saved["response"])
                return ok(saved["status"], cmd_id=cmd_id, duplicate=True,
                          batch_id=saved["batch_id"], result=saved["final_result"],
                          error_code=saved["error_code"])
            if not one(db, "SELECT 1 FROM message_events WHERE msg_type='OPERATOR_ID' AND cmd_id=?", (cmd_id,)):
                return reject("UNKNOWN_CMD_ID", "Reserve an operator command ID first.")
            error = self._check_operator(db, message)
        namespaces = {"SET_BATCH_CONFIG": ("config", "cmd", "cmd"), "START_BATCH": ("batch", "cmd"),
                      **{name: ("cmd",) for name in CONTROLS}}.get(kind, ())
        ids = self.storage.reserve(*namespaces) if not error and namespaces else ()
        with self.storage.transaction() as db:
            save_command(db, message, accepted=not error)
            if error:
                db.execute("UPDATE commands SET status='REJECTED',error_code=?,response=?,completed_at=? "
                           "WHERE cmd_id=?", (error.error_code, error.reason, now_text(), cmd_id))
                trace(db, message, "REJECTED", "Gateway", error_code=error.error_code)
                return error
            return self._run_operator(db, message, ids)

    def _check_operator(self, db, message):
        kind, current = message["msg_type"], state(db)
        active = batch(db)
        if kind == "RESET_FAULT":
            if current != "FAULT":
                return reject("INVALID_STATE", "Reset requires Fault.")
            verified = self._ready() and (self.readiness_provider is None
                                          or self.readiness_provider(dict(self.sessions)) is True)
            return None if verified else reject("NOT_READY", "Both fresh sessions must be verified ready.")
        if kind == "SELECT_MODE":
            if current != "IDLE" or active or settings(db, "PENDING"):
                return reject("INVALID_STATE", "Select a mode while settled and Idle.")
            return None if message["operating_mode"] in config.ALLOWED_MODES else reject(
                "INVALID_MODE", "Choose PRODUCTION or BENCHMARKING.")
        if current == "FAULT":
            return reject("INVALID_STATE", "Reset Fault before issuing new commands.")
        if not self._ready():
            return reject("NOT_READY", "Wait for both device sessions to be ready.")
        if mode(db) != "PRODUCTION":
            return reject("MODE_UNAVAILABLE", "Milestone 1 runs production batches.")
        if settings(db, "PENDING") or (active and (active["batch_status"] == "ARMING"
                or pending_control(db, active["batch_id"]))):
            return reject("TRANSITION_PENDING", "Wait for the current transition.")
        if kind == "SET_BATCH_CONFIG":
            invalid = config.invalid_field(message)
            if invalid:
                return reject("INVALID_VALUE", f"Invalid integer or range for {invalid}.")
            if active and message["target_parts"] != active["target_parts"]:
                return reject("TARGET_LOCKED", "An active batch target cannot change.")
            if active:
                batch_id = active["batch_id"]
                unsettled = one(db, """
                    SELECT 1 FROM commands WHERE batch_id=? AND msg_type='PRODUCTION_PERMIT'
                      AND status IN ('SUBMITTED','DELIVERED')
                    UNION ALL
                    SELECT 1 FROM inspection_jobs WHERE batch_id=? AND status='DISPATCHING'
                """, (batch_id, batch_id))
                if unsettled:
                    return reject("AWAITING_ACCEPTANCE", "Wait for existing work to be accepted or completed.")
        elif kind == "START_BATCH":
            if current != "IDLE" or active:
                return reject("INVALID_STATE", "Start requires Idle.")
            if not config.integer(message["target_parts"], *config.LIMITS["target_parts"]):
                return reject("INVALID_VALUE", "Target must be an integer from 1 through 1000.")
            active_settings = settings(db)
            if not active_settings:
                return reject("NOT_CONFIGURED", "Apply settings to both current sessions first.")
            if message["target_parts"] != active_settings["target_parts"]:
                return reject("TARGET_MISMATCH", "Apply the target in batch settings before starting.")
        else:
            allowed = {"PAUSE_BATCH": {"RUNNING"}, "RESUME_BATCH": {"PAUSED"},
                       "STOP_BATCH": {"RUNNING", "PAUSED"}}
            if not active or current not in allowed[kind]:
                return reject("INVALID_STATE", "The current batch cannot accept this command.")
            if not config.integer(message["batch_id"]) or message["batch_id"] != active["batch_id"]:
                return reject("UNKNOWN_BATCH", "Command must target the current batch.")
        return None

    def _run_operator(self, db, message, ids):
        kind, cmd_id = message["msg_type"], message["cmd_id"]
        if kind == "SELECT_MODE":
            log_event(db, "Gateway", "OPERATING_MODE", "COMPLETED", cmd_id=cmd_id,
                      result=message["operating_mode"])
            finish_command(db, cmd_id, "MODE_SELECTED")
            return ok("COMPLETED", cmd_id=cmd_id, result="MODE_SELECTED")
        if kind == "RESET_FAULT":
            transition(db, "IDLE")
            finish_command(db, cmd_id, "FAULT_RESET")
            return ok("COMPLETED", cmd_id=cmd_id, result="FAULT_RESET")
        if kind == "SET_BATCH_CONFIG":
            version, a_cmd, b_cmd = ids
            db.execute("""
                INSERT INTO configurations(config_version,target_parts,cycle_time_ms,inspection_time_ms,
                    rule_n,rule_k,status,operator_cmd_id,created_at) VALUES (?,?,?,?,?,?,'PENDING',?,?)
            """, (version, *(message[key] for key in config.DEFAULTS), cmd_id, now_text()))
            a = command("SET_PRODUCTION_CONFIG", a_cmd, self.sessions[1][0], parent_cmd_id=cmd_id,
                        config_version=version, cycle_time_ms=message["cycle_time_ms"])
            b = command("SET_INSPECTION_CONFIG", b_cmd, self.sessions[2][0], parent_cmd_id=cmd_id,
                        config_version=version, **{key: message[key] for key in
                                                  ("inspection_time_ms", "rule_n", "rule_k")})
            save_command(db, a, 1)
            save_command(db, b, 2)
            return ok("ACCEPTED", outbound=(a, b), cmd_id=cmd_id, config_version=version)
        if kind == "START_BATCH":
            batch_id, child = ids
            db.execute("INSERT INTO batches(batch_id,operating_mode,target_parts,batch_status,started_at) "
                       "VALUES (?,'PRODUCTION',?,'ARMING',?)", (batch_id, message["target_parts"], now_text()))
            request = command("START_PRODUCTION", child, self.sessions[1][0], parent_cmd_id=cmd_id,
                              batch_id=batch_id, target_parts=message["target_parts"])
            save_command(db, request, 1)
            db.execute("UPDATE batches SET start_cmd_id=? WHERE batch_id=?", (child, batch_id))
            db.execute("UPDATE commands SET batch_id=? WHERE cmd_id=?", (batch_id, cmd_id))
            return ok("ACCEPTED", outbound=(request,), cmd_id=cmd_id, batch_id=batch_id)
        child, = ids
        request = command(CONTROLS[kind][0], child, self.sessions[1][0], parent_cmd_id=cmd_id,
                          batch_id=message["batch_id"])
        save_command(db, request, 1)
        return ok("ACCEPTED", outbound=(request,), cmd_id=cmd_id, batch_id=message["batch_id"])

    def _admission(self, db, production):
        active = batch(db)
        if state(db) != "RUNNING" or not active or active["batch_status"] != "RUNNING":
            return reject("INVALID_STATE", "New work requires an armed Running batch.")
        if not self._ready() or not settings(db):
            return reject("NOT_READY", "Both stations must have applied the active settings.")
        batch_id = active["batch_id"]
        if pending_control(db, batch_id) or settings(db, "PENDING"):
            return reject("TRANSITION_PENDING", "New work is held during a transition.")
        queue = scheduler.queue_status(db, batch_id)
        if production:
            if scheduler.outstanding(db, batch_id):
                return reject("PERMIT_OUTSTANDING", "A permit is already reserved.")
            if active["produced_total"] == active["target_parts"]:
                return reject("TARGET_REACHED", "All requested parts are produced.")
            if queue["waiting"] >= config.QUEUE_CAPACITY:
                return reject("QUEUE_FULL", "All waiting positions are taken.")
        elif queue["active"] is not None:
            return reject("INSPECTION_ACTIVE", "One inspection is already active.")
        return None

    @serialized
    def request_production_permit(self):
        with self.storage.transaction(write=False) as db:
            error = self._admission(db, True)
            if error:
                return error
        part_id, cmd_id = self.storage.reserve("part", "cmd")
        with self.storage.transaction() as db:
            active, current = batch(db), settings(db)
            request = command("PRODUCTION_PERMIT", cmd_id, self.sessions[1][0],
                              batch_id=active["batch_id"], part_id=part_id,
                              config_version=current["config_version"])
            save_command(db, request, 1)
            db.execute("INSERT INTO parts(part_id,batch_id,permit_cmd_id,config_version,status,reserved_at) "
                       "VALUES (?,?,?,?,'RESERVED',?)",
                       (part_id, active["batch_id"], cmd_id, current["config_version"], now_text()))
            trace(db, request, "RESERVED", "Gateway")
        return ok("RESERVED", outbound=(request,), part_id=part_id, cmd_id=cmd_id)

    @staticmethod
    def _job_request(row):
        return dict(schema_version=1, msg_type="INSPECT_PART", device_id=0,
                    **{key: row[key] for key in ("session_id", "batch_id", "part_id", "job_id", "config_version")})

    @serialized
    def next_inspection_job(self):
        with self.storage.transaction() as db:
            error = self._admission(db, False)
            if error:
                return error
            batch_id = batch(db)["batch_id"]
            job = scheduler.next_waiting(db, batch_id)
            if not job:
                return reject("QUEUE_EMPTY", "No inspection job is waiting.")
            request = self._job_request(dict(job) | {
                "session_id": self.sessions[2][0], "config_version": settings(db)["config_version"]})
            db.execute("UPDATE inspection_jobs SET status='DISPATCHING',session_id=?,config_version=? "
                       "WHERE job_id=?", (request["session_id"], request["config_version"], job["job_id"]))
            trace(db, request, "DISPATCHING", "Gateway")
        return ok("DISPATCHING", outbound=(request,), job_id=job["job_id"])

    @staticmethod
    def _operation_row(db, message, device):
        if "job_id" in message:
            if device != 2 or "cmd_id" in message or not config.integer(message["job_id"]):
                return None, None
            key = "job_id"
            row = one(db, "SELECT * FROM inspection_jobs WHERE job_id=?", (message[key],))
        else:
            if not config.integer(message.get("cmd_id")):
                return None, None
            key = "cmd_id"
            row = one(db, "SELECT * FROM commands WHERE cmd_id=? AND target_device=?", (message[key], device))
        return key, row if row and row["session_id"] == message["session_id"] else None

    @staticmethod
    def _normalize(message):
        return {key: value for key, value in message.items() if key != "seq"}

    def _session_matches(self, message, device):
        return (type(device) is int and config.integer(message.get("session_id"))
                and self.sessions.get(device) == (message["session_id"], True))

    @serialized
    def record_transport(self, device_id, message, stage, *, protocol=None, seq=None, retry_index=None):
        """Record first transmission or a same-ID retry from the trusted adapter."""
        if (stage not in {"SENT", "RETRY"} or not flat_message(message)
                or not self._session_matches(message, device_id)):
            return reject("INVALID_REQUEST", "A send event must refer to the current device session.")
        if ((seq is not None and not config.integer(seq, 0))
                or (protocol is not None and not isinstance(protocol, str))
                or (stage == "RETRY" and not config.integer(retry_index, 1, 3))
                or (stage == "SENT" and retry_index is not None and not config.integer(retry_index, 0, 0))):
            return reject("INVALID_VALUE", "Invalid transport metadata.")
        with self.storage.transaction() as db:
            key, row = self._operation_row(db, message, device_id)
            if not row or row["status"] in TERMINAL:
                return reject("UNKNOWN_OPERATION", "Work is no longer pending.")
            work_id = row[key]
            expected = row["payload"] if key == "cmd_id" else canonical(self._job_request(row))
            if canonical(self._normalize(message)) != expected:
                return reject("DUPLICATE_ID_CONFLICT", "Transmission changed the original work.")
            if stage == "RETRY":
                previous = one(db, f"SELECT max(retry_index) FROM message_events WHERE {key}=? "
                                   "AND stage='RETRY' AND source='Gateway'", (work_id,))[0] or 0
                if retry_index != previous + 1:
                    return reject("INVALID_RETRY", "Retry index must advance once per transmission.")
                if key == "cmd_id":
                    db.execute("UPDATE commands SET retry_count=? WHERE cmd_id=?", (retry_index, work_id))
            trace(db, message, stage, "Gateway", protocol=protocol, seq=seq, retry_index=retry_index)
        self.timing.setdefault((key, work_id), {}).setdefault("sent", self.clock())
        return ok("RECORDED")

    @serialized
    def on_device_event(self, message, *, protocol=None, seq=None):
        if not flat_message(message):
            return reject("INVALID_REQUEST", "Expected a flat schema-version 1 device event.")
        seq = message.get("seq") if seq is None else seq
        if ((seq is not None and not config.integer(seq, 0))
                or (protocol is not None and not isinstance(protocol, str))):
            return reject("INVALID_VALUE", "Invalid transport metadata.")
        device = message.get("device_id")
        if not self._session_matches(message, device):
            return reject("STALE_SESSION", "Event is outside the current verified session.")
        kind = message["msg_type"]
        if kind == "PRODUCTION_FINISHED":
            return self._production_finished(message, device, protocol=protocol, seq=seq)
        if kind in {"PART_PRODUCED", "INSPECTION_RESULT"}:
            return self._result(message, device, protocol=protocol, seq=seq)
        if kind not in {"DELIVERY_ACK", "ACCEPTED", "REJECTED", "COMPLETED"}:
            return reject("UNKNOWN_MESSAGE", "Unsupported application event.")
        return self._response(message, device, protocol=protocol, seq=seq)

    def _response(self, message, device, **metadata):
        kind = message["msg_type"]
        accepted = False
        fault = False
        with self.storage.transaction() as db:
            key, row = self._operation_row(db, message, device)
            if not row:
                return reject("UNKNOWN_OPERATION", "Response has no matching operation.")
            work_id = row[key]
            expected = row["msg_type"] if key == "cmd_id" else "INSPECT_PART"
            if message.get("ref_msg_type") != expected:
                return reject("DUPLICATE_ID_CONFLICT", "Response refers to another message type.")
            optional = {"reason", "error_code"} if kind == "REJECTED" else {"result"} if kind == "COMPLETED" else set()
            required = {"schema_version", "device_id", "session_id", "msg_type", "ref_msg_type", key}
            if not required <= message.keys() or message.keys() - required - optional - {"seq"}:
                return reject("INVALID_REQUEST", "Unexpected or missing lifecycle response fields.")
            result, code = message.get("result"), message.get("error_code")
            if any(value is not None and not isinstance(value, str) for value in (result, code, message.get("reason"))):
                return reject("INVALID_VALUE", "Result, reason and error code must be text.")
            trace(db, message, kind, str(device), result=result, error_code=code, **metadata)
            if kind == "COMPLETED" and (key != "cmd_id" or expected not in DONE or
                                       message.get("result") != DONE[expected]):
                return reject("INVALID_RESULT", "Unexpected command completion.")
            if row["status"] in TERMINAL:
                if kind == "COMPLETED" and row["status"] != "COMPLETED":
                    return reject("STALE_OPERATION", "Operation ended without successful completion.")
                return ok("DUPLICATE")
            if kind == "DELIVERY_ACK" and key == "cmd_id" and row["status"] == "SUBMITTED":
                db.execute("UPDATE commands SET status='DELIVERED' WHERE cmd_id=?", (work_id,))
            elif kind == "ACCEPTED" and row["status"] in {"SUBMITTED", "DELIVERED", "DISPATCHING"}:
                table, status = ("commands", "ACCEPTED") if key == "cmd_id" else ("inspection_jobs", "IN_PROGRESS")
                db.execute(f"UPDATE {table} SET status=?,accepted_at=? WHERE {key}=?",
                           (status, now_text(), work_id))
                accepted = True
            elif kind == "REJECTED":
                reason = message.get("error_code")
                if not isinstance(reason, str) or not reason:
                    return reject("INVALID_VALUE", "A device rejection needs an error code.")
                if key == "cmd_id":
                    db.execute("UPDATE commands SET status='REJECTED',completed_at=?,error_code=?,response=? "
                               "WHERE cmd_id=?", (now_text(), reason, message.get("reason"), work_id))
                self._abort(db, batch(db), reason, fault=True)
                fault = True
            elif kind == "COMPLETED":
                self._complete_device_command(db, row, device, message["result"])
        if fault:
            self._invalidate()
            return reject("DEVICE_REJECTED", "Device rejected work. Gateway entered Fault.")
        if accepted:
            self.timing.setdefault((key, work_id), {}).setdefault("accepted", self.clock())
        elif kind == "COMPLETED":
            self.timing.pop((key, work_id), None)
        return ok("RECORDED")

    def _complete_device_command(self, db, row, device, result):
        finish_command(db, row["cmd_id"], result)
        kind, parent = row["msg_type"], row["parent_cmd_id"]
        if kind in ("SET_PRODUCTION_CONFIG", "SET_INSPECTION_CONFIG"):
            field = "production_applied_at" if device == 1 else "inspection_applied_at"
            db.execute(f"UPDATE configurations SET {field}=? WHERE config_version=? AND status='PENDING'",
                       (now_text(), row["config_version"]))
            current = settings(db, "PENDING")
            if current and current["production_applied_at"] and current["inspection_applied_at"]:
                db.execute("UPDATE configurations SET status='SUPERSEDED' WHERE status='ACTIVE'")
                db.execute("UPDATE configurations SET status='ACTIVE',applied_at=? WHERE config_version=?",
                           (now_text(), current["config_version"]))
                finish_command(db, parent, "CONFIG_APPLIED")
        elif kind == "START_PRODUCTION":
            db.execute("UPDATE batches SET batch_status='RUNNING' WHERE batch_id=? AND batch_status='ARMING'",
                       (row["batch_id"],))
            finish_command(db, parent, "BATCH_STARTED")
            transition(db, "RUNNING")
        self._settle(db)

    def _result(self, message, device, **metadata):
        kind = message["msg_type"]
        production = kind == "PART_PRODUCED"
        if device != (1 if production else 2):
            return reject("INVALID_DEVICE", "Result came from the wrong station.")
        expected = {"schema_version", "msg_type", "device_id", "session_id", "batch_id",
                    "part_id", "config_version", "result", "cmd_id" if production else "job_id"}
        optional = {"seq"} if production else {"seq", "inspection_elapsed_ms"}
        if not expected <= message.keys() or message.keys() - expected - optional:
            return reject("INVALID_RESULT", "Unexpected or missing report fields.")
        body = canonical(self._normalize(message))
        with self.storage.transaction(write=False) as db:
            key, operation = self._operation_row(db, message, device)
            if not operation or (production and operation["msg_type"] != "PRODUCTION_PERMIT"):
                return reject("UNKNOWN_OPERATION", "No work matches this report in the current session.")
            work_id = operation[key]
            row = (one(db, "SELECT * FROM parts WHERE permit_cmd_id=?", (work_id,))
                   if production else operation)
            if not row:
                return reject("UNKNOWN_OPERATION", "No permit reservation matches this report.")
            if row["report"] and row["report"] != body:
                return reject("DUPLICATE_ID_CONFLICT", "Report conflicts with the committed outcome.")
            verdicts = {"PRODUCED"} if production else config.ALLOWED_RESULTS
            if not matching_ids(message, row, ("part_id", "batch_id", "config_version")) or message["result"] not in verdicts:
                return reject("INVALID_RESULT", "Report IDs, configuration or verdict are invalid.")
            if "inspection_elapsed_ms" in message and not config.integer(message["inspection_elapsed_ms"], 0):
                return reject("INVALID_VALUE", "Inspection duration must be an unsigned integer.")
            active = batch(db)
            committed = row["report"] is not None
            if not committed and (state(db) not in {"RUNNING", "PAUSED"} or not active
                                  or active["batch_id"] != row["batch_id"]
                                  or row["status"] not in (("RESERVED",) if production else scheduler.ACTIVE)):
                return reject("STALE_OPERATION", "This batch or operation no longer accepts a result.")
            decision, age = ("ALREADY_COMMITTED", None) if committed else self._classify_age(db, row, key, work_id, body)

        if decision == "NO_TIMING":
            return reject("NO_TIMING", "Record the first send or acceptance before committing a result.")
        if decision == "CONFLICT":
            return reject("DUPLICATE_ID_CONFLICT", "Late replay changed the previously observed report.")
        if decision in {"NEW_LATE", "WAIT_FOR_RETRY"}:
            if decision == "NEW_LATE":
                with self.storage.transaction() as db:
                    trace(db, message, "LATE_UNVERIFIED", str(device), **metadata)
                    log_event(db, "Gateway", "RESULT_AGE", "LATE_UNVERIFIED", part_id=row["part_id"],
                              batch_id=row["batch_id"], **{key: work_id}, payload={"result_age_ms": age})
            return ok("LATE_UNVERIFIED", **{key: work_id}, result_age_ms=age,
                      acknowledge=False, recover_same_id=True)

        job_id = None
        if production and not committed:
            job_id, = self.storage.reserve("job")
        with self.storage.transaction() as db:
            if committed:
                trace(db, message, "DUPLICATE", str(device), **metadata)
            elif production:
                self._record_part(db, row, job_id, body)
            else:
                self._record_inspection(db, row, message["result"], body)
            if not committed:
                trace(db, message, "COMPLETED", str(device), result=message["result"], **metadata)
                log_event(db, "Gateway", "RESULT_AGE", "LATE_RECONCILED" if decision == "RECONCILE" else "FRESH",
                          part_id=row["part_id"], batch_id=row["batch_id"],
                          **{key: work_id}, payload={"result_age_ms": age})
                self._settle(db)
            job = one(db, "SELECT job_id,result FROM inspection_jobs WHERE part_id=?", (row["part_id"],))
            data = dict(batch_id=row["batch_id"], part_id=row["part_id"], job_id=job["job_id"],
                        acknowledge=True)
            if production:
                data["cmd_id"] = work_id
            else:
                data["result"] = job["result"]
        self.timing.pop((key, work_id), None)
        return ok("DUPLICATE" if committed else "COMMITTED", late_reconciled=decision == "RECONCILE", **data)

    def _classify_age(self, db, row, key, work_id, body):
        times = self.timing.get((key, work_id), {})
        began = times.get("accepted", times.get("sent"))
        if began is None:
            return "NO_TIMING", None
        age_seconds = self.clock() - began
        age = round(age_seconds * 1000)
        late = one(db, f"SELECT event_id,payload FROM message_events WHERE {key}=? "
                       "AND stage='LATE_UNVERIFIED' AND source IN ('1','2') ORDER BY event_id LIMIT 1",
                   (work_id,))
        if late:
            if canonical(self._normalize(json.loads(late["payload"]))) != body:
                return "CONFLICT", age
            retried = one(db, f"SELECT 1 FROM message_events WHERE {key}=? AND stage='RETRY' "
                              "AND event_id>? LIMIT 1", (work_id, late["event_id"]))
            return ("RECONCILE" if retried else "WAIT_FOR_RETRY"), age
        version = one(db, "SELECT * FROM configurations WHERE config_version=?", (row["config_version"],))
        duration = version["inspection_time_ms" if key == "job_id" else "cycle_time_ms"]
        return ("NEW_LATE" if age_seconds > (duration + 2000) / 1000 else "FRESH"), age

    @staticmethod
    def _record_part(db, row, job_id, body):
        timestamp = now_text()
        db.execute("UPDATE parts SET status='PRODUCED',produced_at=?,report=? WHERE part_id=?",
                   (timestamp, body, row["part_id"]))
        db.execute("INSERT INTO inspection_jobs(job_id,batch_id,part_id,status,queued_at) "
                   "VALUES (?,?,?,'QUEUED',?)", (job_id, row["batch_id"], row["part_id"], timestamp))
        db.execute("UPDATE batches SET produced_total=produced_total+1 WHERE batch_id=?", (row["batch_id"],))
        finish_command(db, row["permit_cmd_id"], "PRODUCED")

    @staticmethod
    def _record_inspection(db, row, result, body):
        db.execute("UPDATE inspection_jobs SET status='COMPLETED',result=?,completed_at=?,report=? WHERE job_id=?",
                   (result, now_text(), body, row["job_id"]))
        db.execute("UPDATE batches SET inspected_total=inspected_total+1,"
                   "accepted_total=accepted_total+?,rejected_total=rejected_total+? WHERE batch_id=?",
                   (int(result == "PASS"), int(result == "FAIL"), row["batch_id"]))

    def _production_finished(self, message, device, **metadata):
        expected = {"schema_version", "msg_type", "device_id", "session_id", "cmd_id", "batch_id", "produced_total"}
        if (device != 1 or message.keys() - {"seq"} != expected
                or not config.integer(message.get("batch_id"))
                or not config.integer(message.get("produced_total"), 0)):
            return reject("INVALID_RESULT", "Expected a production completion report from station 1.")
        with self.storage.transaction() as db:
            row = one(db, "SELECT * FROM batches WHERE batch_id=?", (message["batch_id"],))
            _, op = self._operation_row(db, message, device)
            if not row or not op or op["cmd_id"] != row["start_cmd_id"]:
                return reject("UNKNOWN_OPERATION", "Report must reference the original Start Production command.")
            if message["produced_total"] != row["target_parts"]:
                return reject("INVALID_RESULT", "The reported count differs from the batch target.")
            if row["production_finished"]:
                trace(db, message, "DUPLICATE", str(device), **metadata)
                return ok("DUPLICATE", acknowledge=True, batch_id=row["batch_id"])
            if row["batch_status"] not in {"RUNNING", "PAUSED"} or state(db) == "FAULT":
                return reject("STALE_BATCH", "Production report refers to an inactive batch.")
            if row["produced_total"] != row["target_parts"] or scheduler.outstanding(db, row["batch_id"]):
                return reject("REPORT_ORDER", "Commit every individual production report first.")
            db.execute("UPDATE batches SET production_finished=1 WHERE batch_id=?", (row["batch_id"],))
            trace(db, message, "COMPLETED", str(device), **metadata)
            self._settle(db)
        return ok("COMMITTED", acknowledge=True, batch_id=row["batch_id"])

    def _settle(self, db):
        active = batch(db)
        if not active or state(db) not in {"RUNNING", "PAUSED"}:
            return
        batch_id = active["batch_id"]
        queue, permit = scheduler.queue_status(db, batch_id), scheduler.outstanding(db, batch_id)
        pending = pending_control(db, batch_id)
        if pending:
            child = one(db, "SELECT status FROM commands WHERE parent_cmd_id=? AND target_device=1",
                        (pending["cmd_id"],))
            if permit or queue["active"] is not None or not child or child[0] != "COMPLETED":
                return
            action = pending["msg_type"]
            finish_command(db, pending["cmd_id"], CONTROLS[action][2])
            if action == "STOP_BATCH":
                self._abort(db, active, "OPERATOR_STOP", fault=False)
                return
            next_state = "PAUSED" if action == "PAUSE_BATCH" else "RUNNING"
            db.execute("UPDATE batches SET batch_status=? WHERE batch_id=?", (next_state, batch_id))
            transition(db, next_state)
            if next_state == "PAUSED":
                return
        unresolved = one(db, "SELECT 1 FROM parts WHERE batch_id=? AND status='UNRESOLVED' "
                              "UNION ALL SELECT 1 FROM inspection_jobs WHERE batch_id=? AND status='UNRESOLVED' "
                             "UNION ALL SELECT 1 FROM commands WHERE batch_id=? AND status='UNRESOLVED'",
                         (batch_id, batch_id, batch_id))
        if (active["production_finished"] and not permit and not queue["waiting"]
                and queue["active"] is None and not unresolved and not settings(db, "PENDING")
                and active["target_parts"] == active["produced_total"] == active["inspected_total"]
                == active["accepted_total"] + active["rejected_total"]):
            db.execute("UPDATE batches SET batch_status='COMPLETED',finished_at=? WHERE batch_id=?",
                       (now_text(), batch_id))
            log_event(db, "Gateway", "BATCH_COMPLETED", "COMPLETED", batch_id=batch_id)
            transition(db, "IDLE")

    @staticmethod
    def _abort(db, active, reason, *, fault):
        timestamp = now_text()
        if active:
            batch_id = active["batch_id"]
            db.execute("UPDATE parts SET status='UNRESOLVED' WHERE batch_id=? AND status='RESERVED'", (batch_id,))
            db.execute("UPDATE inspection_jobs SET status=CASE WHEN status='QUEUED' THEN 'CANCELLED' "
                       "ELSE 'UNRESOLVED' END,completed_at=? WHERE batch_id=? "
                       "AND status IN ('QUEUED','DISPATCHING','IN_PROGRESS')", (timestamp, batch_id))
            db.execute("UPDATE batches SET batch_status='ABORTED',finished_at=?,abortion_reason=? WHERE batch_id=?",
                       (timestamp, reason, batch_id))
            log_event(db, "Gateway", "BATCH_ABORTED", "ABORTED", batch_id=batch_id, payload={"reason": reason})
        if fault:
            for pending in db.execute("SELECT cmd_id FROM commands WHERE status IN ('SUBMITTED','DELIVERED','ACCEPTED')"):
                log_event(db, "Gateway", "COMMAND_OUTCOME", "UNRESOLVED",
                          cmd_id=pending["cmd_id"], error_code=reason)
            db.execute("UPDATE commands SET status='UNRESOLVED',error_code=?,completed_at=? "
                       "WHERE status IN ('SUBMITTED','DELIVERED','ACCEPTED')", (reason, timestamp))
            db.execute("UPDATE configurations SET status='FAILED' WHERE status='PENDING'")
            db.execute("UPDATE configurations SET status='SUPERSEDED' WHERE status='ACTIVE'")
        transition(db, "FAULT" if fault else "IDLE", reason)

    def _invalidate(self):
        self.sessions.clear()
        self.timing.clear()

    @serialized
    def on_communication_fault(self, reason, *, device_id=None, session_id=None):
        if not isinstance(reason, str) or not reason.strip():
            return reject("INVALID_VALUE", "A fault needs a reason.")
        if session_id is not None:
            current = self.sessions.get(device_id)
            if not current or not config.integer(session_id) or current[0] != session_id:
                return reject("STALE_SESSION", "Fault refers to a retired session.")
        with self.storage.transaction() as db:
            if state(db) != "FAULT":
                self._abort(db, batch(db), reason, fault=True)
            else:
                log_event(db, "Gateway", "COMMUNICATION_FAULT", "OBSERVED", payload={"reason": reason})
        self._invalidate()
        return ok("FAULT")

    @serialized
    def on_recovery_exhausted(self, device_id, request):
        if not flat_message(request) or not self._session_matches(request, device_id):
            return reject("STALE_SESSION", "Recovery refers to a retired session.")
        with self.storage.transaction(write=False) as db:
            key, row = self._operation_row(db, request, device_id)
            if not row or row["status"] in TERMINAL:
                return reject("UNKNOWN_OPERATION", "Operation is no longer pending.")
            expected = row["payload"] if key == "cmd_id" else canonical(self._job_request(row))
            if canonical(self._normalize(request)) != expected:
                return reject("DUPLICATE_ID_CONFLICT", "Recovery request changed the operation.")
            retries = one(db, f"SELECT max(retry_index) FROM message_events WHERE {key}=? AND stage='RETRY'",
                          (row[key],))[0]
            if retries != 3:
                return reject("INVALID_RETRY", "Record all three retries before reporting exhaustion.")
        return self.on_communication_fault("RETRY_EXHAUSTED", device_id=device_id,
                                           session_id=request["session_id"])

    def get_status_snapshot(self):
        with self.lock, self.storage.transaction(write=False) as db:
            active = batch(db)
            recent = active or one(db, "SELECT * FROM batches ORDER BY batch_id DESC LIMIT 1")
            active_id = active["batch_id"] if active else None
            queue, permit = scheduler.queue_status(db, active_id), scheduler.outstanding(db, active_id)
            control = pending_control(db, active_id)
            current, current_state = settings(db), state(db)
            marker = latest(db, "GATEWAY_STATE")
            counts = {key: recent[key] if recent else 0 for key in
                      ("produced_total", "inspected_total", "accepted_total", "rejected_total")}
            late = one(db, "SELECT count(*) FROM message_events WHERE batch_id=? AND "
                           "msg_type='RESULT_AGE' AND stage='LATE_UNVERIFIED'",
                       (recent["batch_id"] if recent else None,))[0]
            return dict(schema_version=1, msg_type="STATUS_UPDATE", **counts, state=current_state,
                        operating_mode=mode(db), benchmark_ready=False,
                        batch_id=recent["batch_id"] if recent else None,
                        batch_status=recent["batch_status"] if recent else None,
                        target_parts=recent["target_parts"] if recent else
                        (current["target_parts"] if current else config.DEFAULTS["target_parts"]),
                        queue_waiting=queue["waiting"], active_job_id=queue["active"],
                        outstanding_permit_part_id=permit["part_id"] if permit else None,
                        pause_pending=bool(control and control["msg_type"] == "PAUSE_BATCH"),
                        stop_pending=bool(control and control["msg_type"] == "STOP_BATCH"),
                        queue_blocked=queue["waiting"] + bool(permit) >= config.QUEUE_CAPACITY,
                        config_updating=settings(db, "PENDING") is not None,
                        config_version=current["config_version"] if current else None,
                        esp32_a_online=self.sessions.get(1, (None, None))[1],
                        esp32_b_online=self.sessions.get(2, (None, None))[1],
                        late_result_count=late,
                        fault_reason=json.loads(marker["payload"])["reason"] if current_state == "FAULT" else None)

    def get_batch_report(self, batch_id):
        if not config.integer(batch_id):
            return None
        with self.lock, self.storage.transaction(write=False) as db:
            row = one(db, "SELECT * FROM batches WHERE batch_id=?", (batch_id,))
            return dict(row) if row else None

    def get_command_status(self, cmd_id):
        if not config.integer(cmd_id):
            return None
        with self.lock, self.storage.transaction(write=False) as db:
            row = one(db, "SELECT * FROM commands WHERE cmd_id=?", (cmd_id,))
            if row is None:
                return None
            result = dict(row)
            result["payload"] = json.loads(row["payload"])
            result["events"] = [dict(event) for event in db.execute(
                "SELECT * FROM message_events WHERE cmd_id=? ORDER BY event_id", (cmd_id,))]
            return result

    def export_csv(self, table, destination):
        with self.lock:
            self.storage.export_csv(table, destination)

    def get_configuration(self, version):
        with self.lock, self.storage.transaction(write=False) as db:
            row = one(db, "SELECT * FROM configurations WHERE config_version=?", (version,))
            return dict(row) if row else None

    def get_permit_configuration(self, part_id):
        with self.lock, self.storage.transaction(write=False) as db:
            row = one(db, "SELECT c.* FROM configurations c JOIN parts p "
                          "ON c.config_version=p.config_version WHERE p.part_id=?", (part_id,))
            return dict(row) if row else None

    def latest_event_id(self):
        with self.lock, self.storage.transaction(write=False) as db:
            return one(db, "SELECT coalesce(max(event_id),0) FROM message_events")[0]

    def read_events(self, after_id):
        with self.lock, self.storage.transaction(write=False) as db:
            return [dict(row) for row in db.execute("""
                SELECT e.*, c.target_device IS NULL AND c.cmd_id IS NOT NULL AS operator_command
                FROM message_events e LEFT JOIN commands c ON c.cmd_id=e.cmd_id
                WHERE e.event_id>? ORDER BY e.event_id LIMIT 1000
            """, (after_id,))]
