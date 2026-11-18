"""Six-table SQLite storage, durable ID reservation and CSV exports."""

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .config import UINT32_MAX
from .messages import canonical


TABLES = ("batches", "parts", "inspection_jobs", "commands", "configurations", "message_events")
NAMESPACES = ("batch", "part", "job", "cmd", "session", "config")
SCHEMA_VERSION = 2


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Storage:
    def __init__(self, path):
        self.path = Path(path).resolve()
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not tables and version == 0:
                schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8-sig")
                db.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")
            elif version != SCHEMA_VERSION or tables != set(TABLES):
                raise ValueError("Unknown or legacy database. Run explicit migration to a new file.")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed.")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("SQLite foreign-key check failed.")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self, *, write=True):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def reserve(self, *names):
        """Commit high-water marks before a caller can use any returned ID."""
        with self.transaction() as db:
            result = []
            for name in names:
                if name not in NAMESPACES:
                    raise ValueError(f"Unknown ID namespace {name}")
                prev = db.execute(
                    "SELECT result FROM message_events WHERE msg_type='ID_ALLOCATION' "
                    "AND source=? ORDER BY event_id DESC LIMIT 1", (name,)
                ).fetchone()
                value = 1 if prev is None else int(prev[0]) + 1
                if value > UINT32_MAX:
                    raise OverflowError(f"{name} identifier space exhausted")
                log_event(db, name, "ID_ALLOCATION", "RESERVED", result=str(value))
                result.append(value)
            return result

    def export_csv(self, table, destination):
        if table not in TABLES:
            raise ValueError("Unknown export table")
        target = Path(destination).resolve()
        if target == self.path:
            raise ValueError("CSV output must differ from the database")
        with self.transaction(write=False) as db, target.open("x", newline="", encoding="utf-8") as stream:
            cursor = db.execute(f"SELECT * FROM {table} ORDER BY rowid")
            writer = csv.writer(stream)
            writer.writerow(col[0] for col in cursor.description)
            writer.writerows(cursor)


def log_event(db, source, msg_type, stage, *, payload=None, **fields):
    columns = ("cmd_id", "batch_id", "part_id", "job_id", "session_id", "result",
               "error_code", "retry_index", "protocol", "seq")
    db.execute("""
        INSERT INTO message_events
        (created_at,source,msg_type,stage,cmd_id,batch_id,part_id,job_id,session_id,result,
         error_code,retry_index,protocol,seq,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (now_text(), source, msg_type, stage, *(fields.get(key) for key in columns),
              None if payload is None else canonical(payload)))
