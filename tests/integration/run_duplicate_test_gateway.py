"""Run the normal gateway with one opt-in inspection-command replay."""

from datetime import datetime, timezone
import os
import logging
from pathlib import Path
import sys

import uvicorn

sys.path.insert(0, str(Path.cwd()))

from gateway import bridge
from gateway.transports.inspection_link import InspectionLink

LOG = logging.getLogger("uvicorn.error")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DuplicateTestLink(InspectionLink):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected = None
        LOG.info("%s DUPLICATE_TEST armed for the first accepted inspection", utc_now())

    def receive(self, message, now):
        candidate = (
            isinstance(message, dict)
            and self.selected is None
            and message.get("msg_type") == "ACCEPTED"
            and message.get("ref_msg_type") == "INSPECT_PART"
            and type(message.get("job_id")) is int
        )
        key = ("job_id", message["job_id"]) if candidate else None
        transaction = self.pending.get(key)
        newly_accepted = transaction is not None and transaction.accepted_at is None
        super().receive(message, now)
        if newly_accepted and transaction.accepted_at is not None and self.ready:
            self.selected = (self.session, message["job_id"])
            LOG.info("%s DUPLICATE_TEST replay INSPECT_PART job=%s seq=%s",
                     utc_now(), message["job_id"], self.seq)
            # Give the wire replay a fresh sequence without changing the retry budget.
            self.send(transaction.message)

    def receive_result(self, key, message, now):
        selected = self.selected == (self.session, message.get("job_id"))
        if not selected:
            return super().receive_result(key, message, now)
        keys = ("inspected_total", "accepted_total", "rejected_total")
        before = self.application.snapshot()
        super().receive_result(key, message, now)
        after = self.application.snapshot()
        LOG.info("%s DUPLICATE_TEST RESULT job=%s seq=%s before=%s after=%s",
                 utc_now(), message["job_id"], message["seq"],
                 {field: before[field] for field in keys},
                 {field: after[field] for field in keys})


bridge.InspectionLink = DuplicateTestLink
uvicorn.run("gateway.main:app", host=os.environ["MCT431_INSPECTION_HOST"], port=8001)
