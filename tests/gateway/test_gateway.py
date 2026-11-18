"""Updated Gateway tests with controlled sessions and temporary databases."""
from concurrent.futures import ThreadPoolExecutor
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gateway.core import GatewayController
from gateway.core.config import DEFAULTS, UINT32_MAX
from gateway.core.database import log_event


def operator(core, kind, **fields):
    cmd_id = core.reserve_command_id().data["cmd_id"]
    return core.submit(dict(schema_version=1, msg_type=kind, cmd_id=cmd_id, **fields))


def device_event(core, device, request, kind, **fields):
    reference = {key: request[key] for key in ("cmd_id", "job_id") if key in request}
    return core.on_device_event(dict(schema_version=1, msg_type=kind, device_id=device,
                                     session_id=request["session_id"], **reference, **fields))


def complete(core, device, request, result):
    for event in ("DELIVERY_ACK", "ACCEPTED"):
        assert device_event(core, device, request, event, ref_msg_type=request["msg_type"]).success
    outcome = device_event(core, device, request, "COMPLETED",
                           ref_msg_type=request["msg_type"], result=result)
    assert outcome.success, outcome


def connect(core):
    sessions = {}
    for device in (1, 2):
        session = core.open_device_session(device).data["session_id"]
        assert core.on_device_status_changed(device, session, True).success
        sessions[device] = session
    return sessions


def apply(core, target, **values):
    outcome = operator(core, "SET_BATCH_CONFIG", **(DEFAULTS | {"target_parts": target} | values))
    assert outcome.success, outcome
    for device, req in enumerate(outcome.outbound, 1):
        complete(core, device, req, "CONFIG_APPLIED")
    return outcome


def start(core, target):
    outcome = operator(core, "START_BATCH", target_parts=target)
    assert outcome.success, outcome
    complete(core, 1, outcome.outbound[0], "ARMED")
    return outcome


def make_report(device, request, kind, **values):
    return dict(request, device_id=device, msg_type=kind, **values)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "gateway.sqlite3"
        self.tick = 10.0
        self.reset_enabled = True
        self.core = GatewayController(self.path, clock=lambda: self.tick,
                                      readiness_provider=lambda sessions: self.reset_enabled)
        self.sessions = connect(self.core)

    def ready_batch(self, target=10):
        apply(self.core, target)
        return start(self.core, target)

    def permit(self):
        result = self.core.request_production_permit()
        self.assertTrue(result.success, result)
        request, = result.outbound
        self.assertTrue(self.core.record_transport(1, request, "SENT", protocol="USB", seq=0).success)
        return request

    def produced(self, permit):
        return self.core.on_device_event(make_report(1, permit, "PART_PRODUCED", result="PRODUCED"))

    def produce(self):
        permit = self.permit()
        result = self.produced(permit)
        self.assertEqual(result.status, "COMMITTED")
        return permit, result

    def dispatch(self):
        outcome = self.core.next_inspection_job()
        self.assertTrue(outcome.success, outcome)
        job, = outcome.outbound
        self.assertTrue(self.core.record_transport(2, job, "SENT", protocol="TCP", seq=0).success)
        return job

    def inspect(self, job, verdict="PASS", **metadata):
        report = make_report(2, job, "INSPECTION_RESULT", result=verdict)
        return self.core.on_device_event(report | metadata)

    def finish_production(self, started):
        arm = started.outbound[0]
        return device_event(self.core, 1, arm, "PRODUCTION_FINISHED", batch_id=arm["batch_id"],
                            produced_total=arm["target_parts"])

    def snapshot(self):
        return self.core.get_status_snapshot()

    def test_requires_sessions_config_and_arm_confirmation(self):
        self.assertEqual(operator(self.core, "START_BATCH", target_parts=1).error_code, "NOT_CONFIGURED")
        apply(self.core, 1)
        pending = operator(self.core, "START_BATCH", target_parts=1)
        self.assertEqual(self.snapshot()["batch_status"], "ARMING")
        self.assertEqual(self.core.request_production_permit().error_code, "INVALID_STATE")
        self.assertEqual(operator(self.core, "START_BATCH", target_parts=1).error_code, "TRANSITION_PENDING")
        complete(self.core, 1, pending.outbound[0], "ARMED")
        self.assertEqual(self.snapshot()["state"], "RUNNING")
        self.assertEqual(operator(self.core, "START_BATCH", target_parts=1).error_code, "INVALID_STATE")

    def test_mode_selection_and_benchmark_unavailable(self):
        self.assertFalse(operator(self.core, "SELECT_MODE", operating_mode="bad").success)
        self.assertTrue(operator(self.core, "SELECT_MODE", operating_mode="BENCHMARKING").success)
        self.assertEqual(operator(self.core, "START_BATCH", target_parts=1).error_code, "MODE_UNAVAILABLE")
        self.assertFalse(self.snapshot()["benchmark_ready"])

    def test_permit_assigns_numeric_part_and_reserves_it_before_send(self):
        self.ready_batch(1)
        permit = self.permit()
        self.assertEqual(permit["part_id"], 1)
        self.assertEqual(self.snapshot()["outstanding_permit_part_id"], 1)
        self.assertEqual(self.core.request_production_permit().error_code, "PERMIT_OUTSTANDING")
        wrong = self.produced(permit | {"part_id": 99})
        self.assertFalse(wrong.success)
        self.assertEqual(self.snapshot()["produced_total"], 0)
        result = self.produced(permit)
        self.assertTrue(result.data["acknowledge"])
        self.assertEqual(self.dispatch()["part_id"], permit["part_id"])
        self.assertEqual(self.core.request_production_permit().error_code, "TARGET_REACHED")

    def test_two_concurrent_callers_cannot_reserve_two_permits(self):
        self.ready_batch()
        with ThreadPoolExecutor(2) as threads:
            outcomes = list(threads.map(lambda _: self.core.request_production_permit(), range(2)))
        self.assertEqual(sum(result.success for result in outcomes), 1)

    def test_fifo_full_and_one_active_with_reserved_slot(self):
        self.ready_batch()
        parts = [self.produce()[0]["part_id"] for _ in range(5)]
        self.assertEqual(self.snapshot()["queue_waiting"], 5)
        self.assertEqual(self.core.request_production_permit().error_code, "QUEUE_FULL")
        job = self.dispatch()
        self.assertEqual(job["part_id"], parts[0])
        self.assertEqual(self.core.next_inspection_job().error_code, "INSPECTION_ACTIVE")
        permit = self.permit()
        self.assertTrue(self.snapshot()["queue_blocked"])
        self.assertEqual(self.snapshot()["queue_waiting"], 4)
        self.assertEqual(self.produced(permit).status, "COMMITTED")
        self.assertTrue(self.inspect(job).success)
        self.assertEqual(self.dispatch()["part_id"], parts[1])

    def test_duplicate_reports_and_conflicting_results(self):
        self.ready_batch(1)
        permit, first = self.produce()
        repeat = self.produced(permit | {"seq": 7})
        self.assertEqual(repeat.status, "DUPLICATE")
        self.assertEqual(repeat.data["job_id"], first.data["job_id"])
        self.assertEqual(self.produced(permit | {"part_id": 42}).error_code, "DUPLICATE_ID_CONFLICT")
        job = self.dispatch()
        self.assertTrue(self.inspect(job).data["acknowledge"])
        self.assertTrue(self.inspect(job, seq=2).data["acknowledge"])
        self.assertEqual(self.inspect(job, "FAIL").error_code, "DUPLICATE_ID_CONFLICT")
        self.assertEqual((self.snapshot()["produced_total"], self.snapshot()["inspected_total"]), (1, 1))

    def test_delivery_acceptance_and_completion_are_distinct(self):
        self.ready_batch()
        permit = self.permit()
        for kind in ("DELIVERY_ACK", "ACCEPTED"):
            self.assertTrue(device_event(self.core, 1, permit, kind,
                                         ref_msg_type="PRODUCTION_PERMIT").success)
        self.assertEqual(self.core.get_command_status(permit["cmd_id"])["status"], "ACCEPTED")
        self.produced(permit)
        record = self.core.get_command_status(permit["cmd_id"])
        self.assertEqual(record["status"], "COMPLETED")
        self.assertTrue({"SENT", "DELIVERY_ACK", "ACCEPTED", "COMPLETED"} <=
                        {event["stage"] for event in record["events"]})

    def test_pause_waits_for_production_and_active_inspection_and_resume_preserves_queue(self):
        started = self.ready_batch()
        self.produce()
        job = self.dispatch()
        permit = self.permit()
        batch_id = started.data["batch_id"]
        pause = operator(self.core, "PAUSE_BATCH", batch_id=batch_id)
        self.assertEqual(self.snapshot()["state"], "RUNNING")
        self.assertTrue(self.snapshot()["pause_pending"])
        self.assertEqual(self.core.next_inspection_job().error_code, "TRANSITION_PENDING")
        self.assertEqual(self.core.request_production_permit().error_code, "TRANSITION_PENDING")
        complete(self.core, 1, pause.outbound[0], "PAUSED")
        self.produced(permit)
        self.assertEqual(self.snapshot()["state"], "RUNNING")
        self.inspect(job)
        self.assertEqual(self.snapshot()["state"], "PAUSED")
        self.assertEqual(self.snapshot()["queue_waiting"], 1)
        self.assertFalse(self.core.next_inspection_job().success)
        resume = operator(self.core, "RESUME_BATCH", batch_id=batch_id)
        complete(self.core, 1, resume.outbound[0], "RESUMED")
        self.assertEqual(self.dispatch()["part_id"], permit["part_id"])

    def test_graceful_stop_waits_for_active_work_and_aborts_with_partial_counts(self):
        started = self.ready_batch()
        self.produce()
        job = self.dispatch()
        permit = self.permit()
        stop = operator(self.core, "STOP_BATCH", batch_id=started.data["batch_id"])
        self.assertTrue(self.snapshot()["stop_pending"])
        self.assertFalse(self.core.next_inspection_job().success)
        self.produced(permit)
        complete(self.core, 1, stop.outbound[0], "STOPPED")
        self.assertEqual(self.snapshot()["state"], "RUNNING")
        self.inspect(job, "FAIL")
        final = self.snapshot()
        self.assertEqual((final["state"], final["batch_status"], final["queue_waiting"]),
                         ("IDLE", "ABORTED", 0))
        self.assertEqual((final["produced_total"], final["inspected_total"], final["rejected_total"]),
                         (2, 1, 1))

    def test_stop_from_settled_pause(self):
        started = self.ready_batch()
        batch_id = started.data["batch_id"]
        pause = operator(self.core, "PAUSE_BATCH", batch_id=batch_id)
        complete(self.core, 1, pause.outbound[0], "PAUSED")
        stop = operator(self.core, "STOP_BATCH", batch_id=batch_id)
        complete(self.core, 1, stop.outbound[0], "STOPPED")
        self.assertEqual(self.snapshot()["batch_status"], "ABORTED")

    def test_fault_cannot_be_cleared_by_last_result(self):
        started = self.ready_batch(1)
        self.produce()
        self.finish_production(started)
        job = self.dispatch()
        self.core.on_communication_fault("CABLE_LOST", device_id=2, session_id=self.sessions[2])
        self.assertEqual(self.inspect(job).error_code, "STALE_SESSION")
        self.assertEqual(self.snapshot()["state"], "FAULT")
        self.assertEqual(self.snapshot()["batch_status"], "ABORTED")
        self.assertEqual(self.snapshot()["inspected_total"], 0)
        self.assertFalse(self.core.next_inspection_job().success)
        self.assertFalse(self.core.request_production_permit().success)
        self.assertEqual(operator(self.core, "RESET_FAULT").error_code, "NOT_READY")
        connect(self.core)
        self.reset_enabled = False
        self.assertEqual(operator(self.core, "RESET_FAULT").error_code, "NOT_READY")
        self.reset_enabled = True
        self.assertTrue(operator(self.core, "RESET_FAULT").success)
        self.assertEqual(self.snapshot()["state"], "IDLE")

    def test_fault_while_producing_preserves_prior_commits(self):
        self.ready_batch()
        self.produce()
        self.inspect(self.dispatch())
        permit = self.permit()
        self.core.on_communication_fault("CABLE_LOST")
        self.assertEqual(self.produced(permit).error_code, "STALE_SESSION")
        self.assertEqual(self.snapshot()["produced_total"], 1)
        self.assertEqual(self.snapshot()["accepted_total"], 1)
        with self.core.storage.transaction(write=False) as db:
            status = db.execute("SELECT status FROM parts WHERE part_id=?", (permit["part_id"],)).fetchone()[0]
        self.assertEqual(status, "UNRESOLVED")

    def test_finish_requires_every_part_and_verified_production_finished(self):
        started = self.ready_batch(2)
        self.produce()
        self.assertEqual(self.finish_production(started).error_code, "REPORT_ORDER")
        self.produce()
        job = self.dispatch()
        self.inspect(job)
        self.assertEqual(self.snapshot()["state"], "RUNNING")
        self.inspect(self.dispatch(), "FAIL")
        self.assertEqual(self.snapshot()["state"], "RUNNING")
        self.assertTrue(self.finish_production(started).data["acknowledge"])
        final = self.snapshot()
        self.assertEqual((final["state"], final["batch_status"], final["produced_total"],
                          final["inspected_total"], final["accepted_total"], final["rejected_total"]),
                         ("IDLE", "COMPLETED", 2, 2, 1, 1))
        self.assertIsNone(final["active_job_id"])
        self.assertIsNone(final["outstanding_permit_part_id"])
        self.assertEqual(final["queue_waiting"], 0)
        self.assertEqual(self.finish_production(started).status, "DUPLICATE")

    def test_configuration_requires_both_confirmations_and_holds_dispatch(self):
        self.ready_batch(3)
        first = self.snapshot()["config_version"]
        self.produce()
        old_job = self.dispatch()
        old_permit = self.permit()
        too_soon = operator(self.core, "SET_BATCH_CONFIG", **(DEFAULTS | {"target_parts": 3}))
        self.assertEqual(too_soon.error_code, "AWAITING_ACCEPTANCE")
        for device, request in ((1, old_permit), (2, old_job)):
            self.assertTrue(device_event(self.core, device, request, "ACCEPTED",
                                         ref_msg_type=request["msg_type"]).success)
        result = operator(self.core, "SET_BATCH_CONFIG", **(DEFAULTS |
                          {"target_parts": 3, "cycle_time_ms": 1, "inspection_time_ms": 1, "rule_k": -7}))
        a, b = result.outbound
        complete(self.core, 1, a, "CONFIG_APPLIED")
        self.assertEqual(self.snapshot()["config_version"], first)
        self.assertTrue(self.snapshot()["config_updating"])
        self.assertEqual(self.core.next_inspection_job().error_code, "TRANSITION_PENDING")
        self.assertEqual(self.core.request_production_permit().error_code, "TRANSITION_PENDING")
        complete(self.core, 2, b, "CONFIG_APPLIED")
        second = self.snapshot()["config_version"]
        self.assertGreater(second, first)
        self.tick += 2.1  # Old 500/1000 ms bounds still govern existing work.
        self.assertEqual(self.produced(old_permit).status, "COMMITTED")
        self.assertEqual(self.inspect(old_job).status, "COMMITTED")
        self.assertEqual(self.dispatch()["config_version"], second)
        self.assertEqual(self.permit()["config_version"], second)

    def test_invalid_config_bounds_and_locked_batch_target(self):
        for key, val in (("rule_n", 0), ("rule_k", -(2**31) - 1), ("cycle_time_ms", True),
                         ("inspection_time_ms", 3_600_001), ("target_parts", 0)):
            with self.subTest(key=key, val=val):
                fields = DEFAULTS | {key: val}
                self.assertEqual(operator(self.core, "SET_BATCH_CONFIG", **fields).error_code, "INVALID_VALUE")
        self.ready_batch(2)
        self.assertEqual(operator(self.core, "SET_BATCH_CONFIG", **(DEFAULTS | {"target_parts": 3})).error_code,
                         "TARGET_LOCKED")

    def test_config_rejection_cannot_activate_partial_config(self):
        result = operator(self.core, "SET_BATCH_CONFIG", **DEFAULTS)
        a, b = result.outbound
        complete(self.core, 1, a, "CONFIG_APPLIED")
        response = device_event(self.core, 2, b, "REJECTED", ref_msg_type=b["msg_type"],
                                error_code="INVALID_RULE_N", reason="Rule invalid")
        self.assertEqual(response.error_code, "DEVICE_REJECTED")
        self.assertIsNone(self.snapshot()["config_version"])
        self.assertFalse(self.snapshot()["config_updating"])
        self.assertEqual(self.snapshot()["state"], "FAULT")

    def test_operator_retry_has_no_second_outbound_and_conflict_is_rejected(self):
        cmd_id = self.core.reserve_command_id().data["cmd_id"]
        message = dict(schema_version=1, msg_type="SET_BATCH_CONFIG", cmd_id=cmd_id, **DEFAULTS)
        first = self.core.submit(message)
        self.assertEqual(len(first.outbound), 2)
        repeat = self.core.submit(message)
        self.assertEqual(repeat.status, first.status)
        self.assertFalse(repeat.outbound)
        self.assertEqual(self.core.submit(message | {"rule_n": 7}).error_code, "DUPLICATE_ID_CONFLICT")

    def test_restart_marks_permit_active_and_waiting_inspections_safely(self):
        started = self.ready_batch()
        self.produce()
        job = self.dispatch()
        self.produce()
        permit = self.permit()
        reopened = GatewayController(self.path)
        report = reopened.get_batch_report(started.data["batch_id"])
        self.assertEqual(report["abortion_reason"], "GATEWAY_RESTART")
        self.assertEqual(report["batch_status"], "ABORTED")
        self.assertEqual(reopened.get_status_snapshot()["state"], "FAULT")
        self.assertFalse(reopened.next_inspection_job().success)
        with reopened.storage.transaction(write=False) as db:
            self.assertEqual([row[0] for row in db.execute("SELECT status FROM inspection_jobs ORDER BY job_id")],
                             ["UNRESOLVED", "CANCELLED"])
            self.assertEqual(db.execute("SELECT status FROM parts WHERE part_id=?", (permit["part_id"],))
                             .fetchone()[0], "UNRESOLVED")
        new_sessions = connect(reopened)
        self.assertGreater(min(new_sessions.values()), max(self.sessions.values()))
        self.assertTrue(operator(reopened, "RESET_FAULT").success)
        new_config = apply(reopened, 2)
        new_batch = start(reopened, 2)
        new_permit = reopened.request_production_permit()
        self.assertGreater(new_batch.data["batch_id"], started.data["batch_id"])
        self.assertGreater(new_permit.data["part_id"], permit["part_id"])
        self.assertGreater(new_config.data["config_version"], permit["config_version"])
        old = reopened.on_device_event(make_report(2, job, "INSPECTION_RESULT", result="PASS"))
        self.assertEqual(old.error_code, "STALE_SESSION")

    def test_restart_while_paused_or_faulted_preserves_fault(self):
        started = self.ready_batch()
        self.produce()
        pause = operator(self.core, "PAUSE_BATCH", batch_id=started.data["batch_id"])
        complete(self.core, 1, pause.outbound[0], "PAUSED")
        reopened = GatewayController(self.path)
        self.assertEqual(reopened.get_status_snapshot()["state"], "FAULT")
        again = GatewayController(self.path)
        self.assertEqual(again.get_status_snapshot()["state"], "FAULT")
        self.assertEqual(again.get_batch_report(started.data["batch_id"])["batch_status"], "ABORTED")
        self.assertEqual(operator(again, "RESET_FAULT").error_code, "NOT_READY")

    def test_old_batch_uncommitted_report_cannot_change_new_running_batch(self):
        started = self.ready_batch(2)
        self.produce()
        stop = operator(self.core, "STOP_BATCH", batch_id=started.data["batch_id"])
        complete(self.core, 1, stop.outbound[0], "STOPPED")
        start(self.core, 2)
        self.assertEqual(self.finish_production(started).error_code, "STALE_BATCH")
        self.assertEqual(self.snapshot()["produced_total"], 0)
        self.assertEqual(self.core.next_inspection_job().error_code, "QUEUE_EMPTY")

    def test_late_result_needs_verified_same_id_replay(self):
        self.ready_batch()
        permit = self.permit()
        self.tick += 3
        late = self.produced(permit)
        self.assertEqual(late.status, "LATE_UNVERIFIED")
        self.assertFalse(late.data["acknowledge"])
        self.assertEqual(self.produced(permit).status, "LATE_UNVERIFIED")
        self.assertEqual(self.snapshot()["produced_total"], 0)
        self.assertFalse(self.core.record_transport(1, permit | {"part_id": 88}, "RETRY", retry_index=1).success)
        self.assertTrue(self.core.record_transport(1, permit, "RETRY", retry_index=1, seq=2).success)
        reconciled = self.produced(permit)
        self.assertTrue(reconciled.data["late_reconciled"])
        self.assertTrue(reconciled.data["acknowledge"])
        self.assertEqual(self.snapshot()["produced_total"], 1)
        job = self.dispatch()
        self.tick += 3.01
        self.assertEqual(self.inspect(job).status, "LATE_UNVERIFIED")
        self.assertTrue(self.core.record_transport(2, job, "RETRY", retry_index=1).success)
        self.assertEqual(self.inspect(job, "FAIL").error_code, "DUPLICATE_ID_CONFLICT")
        self.assertTrue(self.inspect(job).data["late_reconciled"])
        self.assertEqual(self.snapshot()["inspected_total"], 1)

    def test_retry_exhaustion_is_bounded_and_faults_without_counting(self):
        self.ready_batch()
        permit = self.permit()
        self.assertEqual(self.core.on_recovery_exhausted(1, permit).error_code, "INVALID_RETRY")
        for index in range(1, 4):
            retry = self.core.record_transport(1, permit, "RETRY", retry_index=index, seq=index)
            self.assertTrue(retry.success)
        self.assertFalse(self.core.record_transport(1, permit, "RETRY", retry_index=4).success)
        self.assertEqual(self.core.on_recovery_exhausted(1, permit).status, "FAULT")
        self.assertEqual(self.snapshot()["produced_total"], 0)
        self.assertEqual(self.snapshot()["fault_reason"], "RETRY_EXHAUSTED")

    def test_failed_reservation_rolls_back_and_burns_allocated_ids(self):
        self.ready_batch()
        with self.core.storage.transaction() as db:
            db.execute("CREATE TRIGGER fail_reserve BEFORE INSERT ON parts "
                       "BEGIN SELECT RAISE(ABORT,'storage failure'); END")
        failing = self.core.request_production_permit()
        self.assertEqual(failing.error_code, "DATABASE_ERROR")
        self.assertFalse(failing.outbound)
        self.assertIsNone(self.snapshot()["outstanding_permit_part_id"])
        with self.core.storage.transaction() as db:
            db.execute("DROP TRIGGER fail_reserve")
            self.assertEqual(db.execute("SELECT count(*) FROM commands WHERE msg_type='PRODUCTION_PERMIT'")
                             .fetchone()[0], 0)
        self.assertEqual(self.permit()["part_id"], 2)

    def test_atomic_production_write_and_acknowledgement_on_database_failure(self):
        self.ready_batch()
        permit = self.permit()
        with self.core.storage.transaction() as db:
            db.execute("CREATE TRIGGER fail_job BEFORE INSERT ON inspection_jobs "
                       "BEGIN SELECT RAISE(ABORT,'storage failure'); END")
        outcome = self.produced(permit)
        self.assertEqual(outcome.error_code, "DATABASE_ERROR")
        self.assertFalse(outcome.data.get("acknowledge"))
        self.assertEqual(self.snapshot()["produced_total"], 0)
        self.assertEqual(self.snapshot()["outstanding_permit_part_id"], permit["part_id"])
        with self.core.storage.transaction() as db:
            db.execute("DROP TRIGGER fail_job")
        self.assertEqual(self.produced(permit).data["job_id"], 2)

    def test_atomic_verdict_and_counts_on_database_failure(self):
        self.ready_batch()
        self.produce()
        job = self.dispatch()
        with self.core.storage.transaction() as db:
            db.execute("CREATE TRIGGER fail_counts BEFORE UPDATE OF inspected_total ON batches "
                       "BEGIN SELECT RAISE(ABORT,'storage failure'); END")
        outcome = self.inspect(job)
        self.assertEqual(outcome.error_code, "DATABASE_ERROR")
        self.assertFalse(outcome.data.get("acknowledge"))
        self.assertEqual(self.snapshot()["inspected_total"], 0)
        self.assertEqual(self.snapshot()["active_job_id"], job["job_id"])
        with self.core.storage.transaction() as db:
            db.execute("DROP TRIGGER fail_counts")
        self.assertTrue(self.inspect(job).data["acknowledge"])

    def test_csv_preserves_rows_and_refuses_unknown_table_or_overwrite(self):
        self.ready_batch()
        self.produce()
        out = Path(self.temp.name) / "parts.csv"
        self.core.export_csv("parts", out)
        with out.open(encoding="utf-8", newline="") as stream:
            row, = list(csv.DictReader(stream))
        self.assertEqual((row["part_id"], row["status"]), ("1", "PRODUCED"))
        with self.assertRaises(FileExistsError):
            self.core.export_csv("parts", out)
        with self.assertRaises(ValueError):
            self.core.export_csv("other_table", out)

    def test_exhausted_id_never_wraps_or_creates_request(self):
        self.ready_batch()
        with self.core.storage.transaction() as db:
            log_event(db, "part", "ID_ALLOCATION", "RESERVED", result=str(UINT32_MAX))
        result = self.core.request_production_permit()
        self.assertEqual(result.error_code, "ID_EXHAUSTED")
        self.assertFalse(result.outbound)
