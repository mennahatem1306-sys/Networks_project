"""Repeatable migration from the six-table prototype."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from gateway.core import GatewayController
from gateway.core.database import Storage
from gateway.core.migration import migrate, read_legacy

LEGACY = """
CREATE TABLE batches(batch_id TEXT PRIMARY KEY, mode TEXT, target_quantity INTEGER, status TEXT,
    produced_count INTEGER, accepted_count INTEGER, rejected_count INTEGER, session_id TEXT);
CREATE TABLE parts(part_id TEXT PRIMARY KEY,batch_id TEXT,status TEXT,config_version INTEGER);
CREATE TABLE inspection_jobs(job_id TEXT PRIMARY KEY,part_id TEXT,status TEXT,result TEXT,session_id TEXT);
CREATE TABLE commands(command_id TEXT PRIMARY KEY,command_name TEXT,status TEXT,session_id TEXT);
CREATE TABLE configurations(config_id INTEGER PRIMARY KEY,setting_name TEXT,setting_value TEXT,version INTEGER);
CREATE TABLE message_events(event_id INTEGER PRIMARY KEY,source TEXT,message_id TEXT,session_id TEXT,payload TEXT);
INSERT INTO batches VALUES
    ('BATCH-001','PRODUCTION',1,'COMPLETE',1,1,0,'SESSION-001'),
    ('BATCH-002','PRODUCTION',2,'INTERRUPTED',1,0,0,'SESSION-002'),
    ('BATCH-005','PRODUCTION',2,'COMPLETE',1,0,0,NULL);
INSERT INTO parts VALUES ('PART-001','BATCH-001','PASS',1),('PART-002','BATCH-002','WAITING_FOR_INSPECTION',1);
INSERT INTO inspection_jobs VALUES
    ('JOB-001','PART-001','COMPLETE','PASS','SESSION-001'),
    ('JOB-002','PART-002','ACTIVE',NULL,'SESSION-002'),
    ('JOB-010','PART-099','COMPLETE','PASS',NULL);
INSERT INTO commands VALUES ('CMD-001','START_BATCH','ACCEPTED','SESSION-001');
INSERT INTO configurations VALUES (1,'_next_CMD','20',1);
INSERT INTO message_events VALUES (1,'Remote','CMD-001','SESSION-005','log');
"""


class MigrationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        self.source = folder / "legacy.db"
        self.target = folder / "gateway.sqlite3"
        self.backup = folder / "backup.db"
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript(LEGACY)

    def test_refuses_implicit_legacy_database_upgrade(self):
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "explicit migration"):
            GatewayController(self.source)
        self.assertEqual(self.source.read_bytes(), before)

    def test_backup_repeat_and_normalized_history(self):
        before = self.source.read_bytes()
        report = migrate(self.source, self.target, self.backup)
        self.assertEqual(report, migrate(self.source, self.target, self.backup))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(read_legacy(self.source), read_legacy(self.backup))
        self.assertGreaterEqual(report["flagged"], 2)
        core = GatewayController(self.target)
        self.assertEqual(core.get_batch_report(1)["batch_status"], "COMPLETED")
        self.assertEqual(core.get_batch_report(2)["batch_status"], "ABORTED")
        self.assertEqual(core.get_batch_report(5)["abortion_reason"], "LEGACY_REVIEW")
        self.assertEqual(core.reserve_command_id().data["cmd_id"], 20)
        self.assertEqual(core.open_device_session(1).data["session_id"], 6)
        with core.storage.transaction(write=False) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM message_events WHERE msg_type='LEGACY_ROW'")
                             .fetchone()[0], sum(report["archived"].values()))
            self.assertEqual(db.execute("SELECT status FROM inspection_jobs WHERE job_id=2").fetchone()[0],
                             "UNRESOLVED")
            self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())

    def test_refuses_existing_live_target_and_matching_paths(self):
        Storage(self.target)
        before = self.target.read_bytes()
        with self.assertRaises(ValueError):
            migrate(self.source, self.target, self.backup)
        self.assertEqual(self.target.read_bytes(), before)
        with self.assertRaises(ValueError):
            migrate(self.source, self.source, self.backup)
