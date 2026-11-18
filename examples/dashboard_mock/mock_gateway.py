import time  # Use a monotonic clock to measure elapsed time.
from collections import deque  # Use a FIFO queue for parts awaiting inspection.


class CommandError(Exception):  # Represent an expected command rejection.
    def __init__(self, status, code, reason):  # Accept the HTTP status, error code, and reason.
        super().__init__(reason)  # Store the reason in the exception.
        self.status = status  # Keep the HTTP status to return.
        self.code = code  # Keep a stable code that the UI can check.


class MockGateway:  # Simulate the Gateway core locally.
    def __init__(self):
        self.state = "IDLE"  # Start the production line in IDLE.
        self.mode = "PRODUCTION"  # Use PRODUCTION as the default mode.
        self.next_cmd_id = 1  # Start allocating command IDs at one.
        self.next_batch_id = 1  # Start allocating batch IDs at one.
        self.next_part_id = 1  # Start allocating part IDs at one.
        self.reserved = set()  # Track IDs reserved by the operator.
        self.commands = {}  # Store commands and results for duplicate detection.
        self.batches = {}  # Store current and past batches.
        self.events = []  # Buffer events for WebSocket delivery.
        self.batch = None  # Track the currently displayed batch.
        self.queue = deque()  # Keep parts waiting for inspection.
        self.active_job = None  # Track the active inspection and its due time.
        self.permit = None  # Track the part in production and its due time.
        self.pending = None  # Track an accepted Pause or Stop waiting for active work.
        self.config_version = 0  # Track the configuration version used by new jobs.
        self.config = {  # Set initial values for local testing.
            "target_parts": 10,  # Set the default target size.
            "cycle_time_ms": 500,  # Set the production duration in milliseconds.
            "inspection_time_ms": 1000,  # Set the inspection duration in milliseconds.
            "rule_n": 3,  # Set the divisor for the simulated rule.
            "rule_k": 0,  # Set the offset for the simulated rule.
        }

    def reserve_id(self):  # Reserve an ID before command submission.
        cmd_id = self.next_cmd_id  # Read the next available ID.
        self.next_cmd_id += 1  # Advance the ID counter.
        self.reserved.add(cmd_id)  # Mark the ID as reserved.
        return cmd_id  # Return the ID to the API.

    def emit(self, msg_type, **fields):  # Queue an event for WebSocket delivery.
        self.events.append({"schema_version": 1, "msg_type": msg_type, **fields})

    def phase(self, record, event, **fields):  # Append a lifecycle stage to a command.
        entry = {"event": event, **fields}  # Build the stage and its associated fields.
        record["events"].append(entry)  # Store the stage in the command record.
        self.emit("COMMAND_EVENT", cmd_id=record["cmd_id"], **entry)  # Notify the dashboard about the stage.

    def require(self, condition, reason):  # Check whether the current state permits the command.
        if not condition:
            raise CommandError(409, "INVALID_STATE", reason)  # Reject the command with a readable reason.

    def submit(self, payload):  # Execute a command or return its saved record.
        cmd_id = payload["cmd_id"]  # Read the operator's command ID.
        if cmd_id not in self.reserved:
            raise CommandError(404, "UNKNOWN_CMD_ID", "Reserve a command ID first.")

        previous = self.commands.get(cmd_id)  # Look for an earlier command with the same ID.
        if previous is not None:
            if previous["payload"] != payload:
                raise CommandError(409, "CMD_ID_CONFLICT", "This ID has a different payload.")
            return previous  # Return the same record for an identical request.

        record = {"cmd_id": cmd_id, "payload": payload, "events": []}  # Start a lifecycle record for the command.
        self.commands[cmd_id] = record  # Store the request before attempting execution.
        self.phase(record, "DELIVERED")  # Mark the validated request as delivered.

        try:
            result = self.apply(payload, record)  # Check the current state and apply the command.
        except CommandError as error:
            record["error"] = {  # Record the rejection against this command ID.
                "status": error.status, "error_code": error.code, "reason": str(error)
            }
            self.phase(record, "REJECTED", error_code=error.code, reason=str(error))
        else:
            if result is not None:
                self.phase(record, "COMPLETED", result=result)  # Record completion for an immediate command.

        return record  # Return the completed, pending, or rejected record.

    def apply(self, payload, record):  # Apply the behavior for the requested command.
        kind = payload["msg_type"]  # Read the command name from the message.
        idle = self.state == "IDLE"  # Check whether the line is idle.
        current = self.batch or {}  # Allow safe batch lookups before the first run.

        if kind == "SELECT_MODE":
            self.require(idle, "Mode can only change in IDLE.")
            self.phase(record, "ACCEPTED")  # Record acceptance after checking the state.
            self.mode = payload["operating_mode"]  # Apply the selected mode.
            return "MODE_SELECTED"  # Report successful mode selection.

        if kind == "SET_BATCH_CONFIG":
            self.require(self.mode == "PRODUCTION", "Select Production first.")
            self.require(self.state in {"IDLE", "RUNNING", "PAUSED"}, "Configuration is unavailable.")
            self.require(self.pending is None, "Wait for the pending transition.")
            self.require(
                idle or payload["target_parts"] == current.get("target_parts"),
                "The target cannot change during an active batch.",
            )
            self.phase(record, "ACCEPTED")  # Accept the configuration after checking the state.
            for key in self.config:  # Copy only the configuration fields.
                self.config[key] = payload[key]
            self.config_version += 1  # Version the settings used by new work.
            return "CONFIG_APPLIED"  # Report immediate application in the simulation.

        if kind == "START_BATCH":
            self.require(idle and self.mode == "PRODUCTION", "Start requires IDLE and Production.")
            self.phase(record, "ACCEPTED")  # Accept the request to start a batch.
            batch_id = self.next_batch_id  # Allocate the batch ID.
            self.next_batch_id += 1  # Advance the batch ID counter.
            self.batch = {  # Create a batch with zeroed counters.
                "batch_id": batch_id,
                "batch_status": "RUNNING",
                "target_parts": payload["target_parts"],
                "produced_total": 0,
                "inspected_total": 0,
                "accepted_total": 0,
                "rejected_total": 0,
            }
            self.batches[batch_id] = self.batch  # Index the batch by its ID.
            self.state = "RUNNING"  # Allow production and inspection to be scheduled.
            return "BATCH_STARTED"  # Report that the Start command completed and the batch began.

        if kind == "RESET_FAULT":
            self.require(self.state == "FAULT", "Reset requires FAULT.")
            self.phase(record, "ACCEPTED")  # Accept the fault reset.
            self.state = "IDLE"  # Return the simulated line to IDLE.
            return "FAULT_RESET"  # Report that the fault was reset.

        self.require(payload["batch_id"] == current.get("batch_id"), "Batch ID is not current.")
        self.require(self.pending is None, "Wait for the pending transition.")
        if kind == "RESUME_BATCH":
            self.require(self.state == "PAUSED", "Resume requires PAUSED.")
            self.phase(record, "ACCEPTED")  # Accept the resume request.
            self.state = "RUNNING"  # Allow new work to be scheduled again.
            current["batch_status"] = "RUNNING"  # Update the saved batch status.
            return "BATCH_RESUMED"  # Report that the batch resumed.

        allowed = {"RUNNING"} if kind == "PAUSE_BATCH" else {"RUNNING", "PAUSED"}
        self.require(self.state in allowed, f"{kind} is unavailable in {self.state}.")
        self.phase(record, "ACCEPTED")  # Record acceptance of Pause or Stop.
        self.pending = (kind, record)  # Wait for work that has already started.
        return None  # Let a later tick report completion.

    def finish_batch(self, batch_status):  # End the batch and keep its final counters.
        self.batch["batch_status"] = batch_status  # Mark the batch as COMPLETED or ABORTED.
        self.state = "IDLE"  # Return the line to IDLE.
        self.emit(f"BATCH_{batch_status}", **self.batch)  # Publish the final batch result.

    def tick(self):  # Run one simulation step without blocking.
        if self.state not in {"RUNNING", "PAUSED"}:
            return  # Skip scheduling in IDLE or FAULT.

        now = time.monotonic()  # Read the current time for deadline checks.
        batch = self.batch  # Refer to the current batch counters.

        if self.permit and now >= self.permit[1]:
            part_id, _ = self.permit  # Read the ID of the part that finished production.
            self.permit = None  # Clear the active production permit.
            batch["produced_total"] += 1  # Record one produced part.
            self.queue.append(part_id)  # Add the part to the end of the inspection queue.

        if self.active_job and now >= self.active_job[1]:
            part_id, _, rule_n, rule_k, version = self.active_job  # Recover the settings captured at inspection start.
            failed = (part_id + rule_k) % rule_n == 0  # Calculate a simulated verdict for dashboard testing.
            count_key = "rejected_total" if failed else "accepted_total"
            batch[count_key] += 1  # Increment the matching verdict counter.
            batch["inspected_total"] += 1  # Count the completed inspection.
            self.active_job = None  # Free the active inspection slot.
            self.emit(
                "INSPECTION_RESULT", job_id=part_id, part_id=part_id,
                batch_id=batch["batch_id"], config_version=version,
                verdict="FAIL" if failed else "PASS",
            )  # Publish the simulated inspection result.

        if self.pending:
            if self.permit or self.active_job:
                return  # Let the active operations finish first.
            kind, record = self.pending  # Read the pending transition and its command.
            self.pending = None  # Clear the pending transition.
            if kind == "STOP_BATCH":
                self.queue.clear()  # Cancel the waiting inspection jobs.
                self.finish_batch("ABORTED")  # Mark the batch as stopped early.
                self.phase(record, "COMPLETED", result="BATCH_STOPPED")
            else:
                self.state = "PAUSED"  # Pause scheduling of new work.
                batch["batch_status"] = "PAUSED"  # Record the paused batch state.
                self.phase(record, "COMPLETED", result="BATCH_PAUSED")
            return  # End this tick after handling the transition.

        if batch["inspected_total"] == batch["target_parts"]:
            self.finish_batch("COMPLETED")  # Finish after every target part has been inspected.
            return  # Stop scheduling work for the completed batch.

        if self.state == "PAUSED":
            return  # Preserve the queue until the operator resumes.

        if self.active_job is None and self.queue:
            part_id = self.queue.popleft()  # Select the oldest waiting part.
            self.active_job = (
                part_id,
                now + self.config["inspection_time_ms"] / 1000,  # Compute the inspection deadline.
                self.config["rule_n"],  # Capture N for the active inspection.
                self.config["rule_k"],  # Capture K for the active inspection.
                self.config_version,  # Associate the job with its configuration version.
            )

        if self.permit is None and len(self.queue) < 5 and batch["produced_total"] < batch["target_parts"]:
            part_id = self.next_part_id  # Allocate a unique part ID for this run.
            self.next_part_id += 1  # Advance the part ID counter.
            self.permit = (part_id, now + self.config["cycle_time_ms"] / 1000)  # Reserve queue capacity for the part in production.

    def simulate_fault(self):  # Trigger a local fault for testing Reset.
        if self.batch and self.state in {"RUNNING", "PAUSED"}:
            self.permit = None  # Cancel active simulated production.
            self.active_job = None  # Clear the active inspection without recording a verdict.
            self.queue.clear()  # Empty the waiting queue.
            self.finish_batch("ABORTED")  # Mark the batch as aborted.
        if self.pending:
            _, record = self.pending  # Retrieve the command that was waiting.
            self.phase(record, "FAILED", error_code="SIMULATED_FAULT")  # Record the execution failure.
            self.pending = None  # Clear the old pending transition.
        self.state = "FAULT"  # Expose FAULT as the current line state.

    def snapshot(self):  # Build the shared HTTP and WebSocket status snapshot.
        batch = self.batch or {  # Provide display defaults before the first batch.
            "batch_id": None, "batch_status": None, "target_parts": self.config["target_parts"],
            "produced_total": 0, "inspected_total": 0, "accepted_total": 0, "rejected_total": 0,
        }
        pending_kind = self.pending[0] if self.pending else None  # Identify the pending transition, if any.
        return {
            "schema_version": 1, "msg_type": "STATUS_UPDATE",  # Identify the message schema and type.
            **batch,  # Include the batch ID, status, and counters.
            "state": self.state, "operating_mode": self.mode,  # Include the line state and operating mode.
            "benchmark_ready": False,  # Defer benchmark execution to Milestone 2.
            "mode_note": "Benchmark execution is deferred to Milestone 2.",
            "queue_waiting": len(self.queue),  # Count waiting parts separately from active inspection.
            "active_job_id": self.active_job[0] if self.active_job else None,
            "outstanding_permit_part_id": self.permit[0] if self.permit else None,
            "queue_blocked": len(self.queue) + int(self.permit is not None) >= 5,
            "pause_pending": pending_kind == "PAUSE_BATCH",  # Show whether Pause is still pending.
            "stop_pending": pending_kind == "STOP_BATCH",  # Show whether Stop is still pending.
            "config_updating": False,  # Apply mock settings immediately.
            "esp32_a_online": False, "esp32_b_online": False,  # Report that no physical boards are connected.
            "config_version": self.config_version,  # Report the active configuration version.
            "late_result_count": 0,  # Reserve the late-result count for Gateway integration.
            "mock": True,  # Let the dashboard identify this as a simulation.
        }
