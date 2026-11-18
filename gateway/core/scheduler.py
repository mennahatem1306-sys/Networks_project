"""FIFO queries shared by work admission, dispatch and reporting."""

from .config import QUEUE_CAPACITY

ACTIVE = ("DISPATCHING", "IN_PROGRESS")


def queue_capacity():
    return QUEUE_CAPACITY


def queue_status(db, batch_id):
    return db.execute("""
        SELECT count(CASE WHEN status='QUEUED' THEN 1 END) AS waiting,
               max(CASE WHEN status IN ('DISPATCHING','IN_PROGRESS') THEN job_id END) AS active
        FROM inspection_jobs WHERE batch_id=?
    """, (batch_id,)).fetchone()


def next_waiting(db, batch_id):
    return db.execute("""
        SELECT * FROM inspection_jobs WHERE batch_id=? AND status='QUEUED'
        ORDER BY job_id LIMIT 1
    """, (batch_id,)).fetchone()


def outstanding(db, batch_id):
    return db.execute("SELECT * FROM parts WHERE batch_id=? AND status='RESERVED'",
                      (batch_id,)).fetchone()
