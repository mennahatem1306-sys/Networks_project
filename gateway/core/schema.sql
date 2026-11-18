CREATE TABLE batches (
    batch_id INTEGER PRIMARY KEY CHECK(batch_id BETWEEN 1 AND 4294967295),
    operating_mode TEXT NOT NULL CHECK(operating_mode IN ('PRODUCTION','BENCHMARKING')),
    target_parts INTEGER NOT NULL CHECK(target_parts BETWEEN 1 AND 1000),
    produced_total INTEGER NOT NULL DEFAULT 0 CHECK(produced_total BETWEEN 0 AND target_parts),
    inspected_total INTEGER NOT NULL DEFAULT 0 CHECK(inspected_total BETWEEN 0 AND produced_total),
    accepted_total INTEGER NOT NULL DEFAULT 0 CHECK(accepted_total >= 0),
    rejected_total INTEGER NOT NULL DEFAULT 0 CHECK(rejected_total >= 0),
    batch_status TEXT NOT NULL CHECK(batch_status IN ('ARMING','RUNNING','PAUSED','COMPLETED','ABORTED')),
    start_cmd_id INTEGER REFERENCES commands(cmd_id),
    production_finished INTEGER NOT NULL DEFAULT 0 CHECK(production_finished IN (0,1)),
    started_at TEXT,
    finished_at TEXT,
    abortion_reason TEXT,
    CHECK(accepted_total + rejected_total = inspected_total)
);

CREATE TABLE configurations (
    config_version INTEGER PRIMARY KEY CHECK(config_version BETWEEN 1 AND 4294967295),
    target_parts INTEGER NOT NULL CHECK(target_parts BETWEEN 1 AND 1000),
    cycle_time_ms INTEGER NOT NULL CHECK(cycle_time_ms BETWEEN 1 AND 3600000),
    inspection_time_ms INTEGER NOT NULL CHECK(inspection_time_ms BETWEEN 1 AND 3600000),
    rule_n INTEGER NOT NULL CHECK(rule_n BETWEEN 1 AND 2147483647),
    rule_k INTEGER NOT NULL CHECK(rule_k BETWEEN -2147483648 AND 2147483647),
    status TEXT NOT NULL CHECK(status IN ('PENDING','ACTIVE','SUPERSEDED','FAILED')),
    operator_cmd_id INTEGER REFERENCES commands(cmd_id),
    created_at TEXT NOT NULL,
    applied_at TEXT,
    production_applied_at TEXT,
    inspection_applied_at TEXT
);
CREATE UNIQUE INDEX current_config ON configurations(status) WHERE status IN ('PENDING','ACTIVE');

CREATE TABLE commands (
    cmd_id INTEGER PRIMARY KEY CHECK(cmd_id BETWEEN 1 AND 4294967295),
    parent_cmd_id INTEGER REFERENCES commands(cmd_id),
    msg_type TEXT NOT NULL,
    target_device INTEGER CHECK(target_device IN (1,2)),
    session_id INTEGER CHECK(session_id BETWEEN 1 AND 4294967295),
    batch_id INTEGER REFERENCES batches(batch_id),
    config_version INTEGER REFERENCES configurations(config_version),
    status TEXT NOT NULL CHECK(status IN
        ('SUBMITTED','DELIVERED','ACCEPTED','COMPLETED','REJECTED','UNRESOLVED','HISTORICAL')),
    payload TEXT NOT NULL,
    response TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    completed_at TEXT,
    final_result TEXT,
    error_code TEXT
);
CREATE INDEX commands_batch ON commands(batch_id);

CREATE TABLE parts (
    part_id INTEGER PRIMARY KEY CHECK(part_id BETWEEN 1 AND 4294967295),
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    permit_cmd_id INTEGER UNIQUE REFERENCES commands(cmd_id),
    config_version INTEGER REFERENCES configurations(config_version),
    status TEXT NOT NULL CHECK(status IN ('RESERVED','PRODUCED','CANCELLED','UNRESOLVED')),
    reserved_at TEXT,
    produced_at TEXT,
    report TEXT
);
CREATE INDEX parts_batch ON parts(batch_id, status);
CREATE UNIQUE INDEX one_reserved_part ON parts(status) WHERE status='RESERVED';

CREATE TABLE inspection_jobs (
    job_id INTEGER PRIMARY KEY CHECK(job_id BETWEEN 1 AND 4294967295),
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    part_id INTEGER NOT NULL UNIQUE REFERENCES parts(part_id),
    config_version INTEGER REFERENCES configurations(config_version),
    session_id INTEGER CHECK(session_id BETWEEN 1 AND 4294967295),
    status TEXT NOT NULL CHECK(status IN
        ('QUEUED','DISPATCHING','IN_PROGRESS','COMPLETED','CANCELLED','UNRESOLVED')),
    result TEXT CHECK(result IN ('PASS','FAIL')),
    queued_at TEXT,
    accepted_at TEXT,
    completed_at TEXT,
    report TEXT,
    CHECK((status='COMPLETED') = (result IS NOT NULL))
);
CREATE INDEX jobs_fifo ON inspection_jobs(batch_id, status, job_id);
CREATE UNIQUE INDEX one_active_job ON inspection_jobs((1)) WHERE status IN ('DISPATCHING','IN_PROGRESS');

CREATE TABLE message_events (
    event_id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    cmd_id INTEGER,
    batch_id INTEGER,
    part_id INTEGER,
    job_id INTEGER,
    session_id INTEGER,
    result TEXT,
    error_code TEXT,
    retry_index INTEGER,
    protocol TEXT,
    seq INTEGER,
    payload TEXT
);
CREATE UNIQUE INDEX one_open_batch ON batches((1)) WHERE batch_status IN ('ARMING','RUNNING','PAUSED');
CREATE INDEX allocation_high_water ON message_events(source, event_id DESC) WHERE msg_type='ID_ALLOCATION';
CREATE INDEX events_batch ON message_events(batch_id, event_id);
CREATE INDEX events_job ON message_events(job_id, event_id);
CREATE INDEX events_cmd ON message_events(cmd_id, event_id);
CREATE INDEX event_marker ON message_events(msg_type, event_id);
CREATE UNIQUE INDEX allocated_id ON message_events(source, result)
    WHERE msg_type='ID_ALLOCATION';

PRAGMA user_version = 2;
