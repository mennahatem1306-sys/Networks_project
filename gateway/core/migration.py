"""Explicit, non-destructive import of the original six-table database."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .config import integer
from .database import Storage, TABLES, log_event, now_text
from .messages import canonical

PREFIXES = {"batch": "BATCH", "part": "PART", "job": "JOB", "cmd": "CMD", "session": "SESSION"}


def numeric(value, prefix):
    if integer(value):
        return value
    match = re.fullmatch(prefix + r"-([0-9]+)", value) if isinstance(value, str) else None
    if match:
        number = int(match[1])
        if integer(number):
            return number
    return None


def read_legacy(path):
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        names = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if set(TABLES) != names or db.execute("PRAGMA user_version").fetchone()[0] != 0:
            raise ValueError("Expected the legacy six-table schema (user_version 0).")
        return {name: [dict(row) for row in db.execute(f"SELECT * FROM {name} ORDER BY rowid")]
                for name in TABLES}


def digest(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def temporary(directory):
    fd, filename = tempfile.mkstemp(prefix=".gateway-migration-", suffix=".sqlite3", dir=directory)
    os.close(fd)
    return Path(filename)


def backup(source, destination, expected_digest):
    if destination.exists():
        if digest(read_legacy(destination)) != expected_digest:
            raise ValueError("An existing backup belongs to a different legacy snapshot.")
        return
    temp = temporary(destination.parent)
    try:
        with (closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
              closing(sqlite3.connect(temp)) as copy):
            original.backup(copy)
        if digest(read_legacy(temp)) != expected_digest:
            raise ValueError("Source changed during backup. Retry after closing the legacy gateway.")
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def migrate(source, destination, backup_path):
    source, destination, backup_path = (Path(value).resolve()
                                        for value in (source, destination, backup_path))
    if len({source, destination, backup_path}) != 3:
        raise ValueError("Legacy source, backup, and migrated target must be distinct files.")
    records = read_legacy(source)
    fingerprint = digest(records)
    if destination.exists():
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT payload FROM message_events WHERE msg_type='LEGACY_MIGRATION' "
                             "ORDER BY event_id DESC LIMIT 1").fetchone()
        if row and json.loads(row[0])["source_digest"] == fingerprint:
            backup(source, backup_path, fingerprint)
            return json.loads(row[0])
        raise ValueError("The target exists and was created from a different source.")
    backup(source, backup_path, fingerprint)
    temp = temporary(destination.parent)
    try:
        storage = Storage(temp)
        with storage.transaction() as db:
            report = _import(db, records)
            report["source_digest"] = fingerprint
            log_event(db, "Migration", "LEGACY_MIGRATION", "COMPLETED", payload=report)
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Migrated relationships failed foreign-key verification.")
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return report


def _import(db, records):
    report = {"archived": {}, "normalized": {"batches": 0, "parts": 0, "inspection_jobs": 0, "commands": 0},
              "flagged": 0}

    def flag(table, row, why):
        report["flagged"] += 1
        log_event(db, table, "LEGACY_REVIEW", "FLAGGED", payload={"reason": why, "record": row})

    floors = dict.fromkeys((*PREFIXES, "config"), 0)
    id_fields = {"batch": ("batch_id",), "part": ("part_id",), "job": ("job_id",),
                 "cmd": ("command_id", "message_id"), "session": ("session_id",)}
    for table, rows in records.items():
        report["archived"][table] = len(rows)
        for row in rows:
            log_event(db, table, "LEGACY_ROW", "ARCHIVED", payload=row)
            for namespace, keys in id_fields.items():
                values = [numeric(row.get(key), PREFIXES[namespace]) for key in keys]
                floors[namespace] = max([floors[namespace], *(value for value in values if value)])
            # Legacy message IDs can also carry batch, part, or job labels.
            if isinstance(row.get("message_id"), str):
                for namespace, prefix in PREFIXES.items():
                    floors[namespace] = max(floors[namespace], numeric(row["message_id"], prefix) or 0)
            version = row.get("config_version", row.get("version", 0))
            if integer(version):
                floors["config"] = max(floors["config"], version)

    for row in records["configurations"]:
        name = row.get("setting_name", "")
        for namespace, prefix in PREFIXES.items():
            if name == f"_next_{prefix}":
                try:
                    value = int(row["setting_value"]) - 1
                except (ValueError, TypeError):
                    flag("configurations", row, "Invalid legacy counter.")
                    continue
                if integer(value, 0):
                    floors[namespace] = max(floors[namespace], value)
                else:
                    flag("configurations", row, "Counter does not fit the numeric namespace.")
    for namespace, value in floors.items():
        if value:
            log_event(db, namespace, "ID_ALLOCATION", "MIGRATED_HIGH_WATER", result=str(value))

    parts_by_batch = {}
    jobs_by_part = {}
    for row in records["parts"]:
        parts_by_batch.setdefault(row.get("batch_id"), []).append(row)
    for row in records["inspection_jobs"]:
        jobs_by_part.setdefault(row.get("part_id"), []).append(row)

    batches = {}
    for row in records["batches"]:
        batch_id = numeric(row.get("batch_id"), "BATCH")
        target = row.get("target_quantity")
        if not batch_id or batch_id in batches or not integer(target, 1, 1000):
            flag("batches", row, "Invalid or conflicting numeric batch identity or target.")
            continue
        parts = parts_by_batch.get(row["batch_id"], [])
        produced = row.get("produced_count", 0)
        yes, no = row.get("accepted_count", 0), row.get("rejected_count", 0)
        jobs = [job for part in parts for job in jobs_by_part.get(part.get("part_id"), ())]
        good = [job for job in jobs if job.get("status") in {"COMPLETE", "COMPLETED"}
                and job.get("result") in ("PASS", "FAIL") and numeric(job.get("job_id"), "JOB")]
        part_ids = [numeric(part.get("part_id"), "PART") for part in parts]
        job_ids = [numeric(job.get("job_id"), "JOB") for job in good]
        counts_agree = (integer(produced, 0, target) and integer(yes, 0, produced)
                        and integer(no, 0, produced) and yes + no <= produced
                        and len(parts) == produced and all(part_ids)
                        and len(set(part_ids)) == len(parts)
                        and len(set(job_ids)) == len(good)
                        and len({job.get("part_id") for job in good}) == len(good)
                        and yes == sum(job["result"] == "PASS" for job in good)
                        and no == sum(job["result"] == "FAIL" for job in good))
        claims_complete = row.get("status") in {"COMPLETE", "COMPLETED"}
        complete = counts_agree and claims_complete and produced == target == yes + no
        coherent = counts_agree and (not claims_complete or complete)
        if not coherent:
            flag("batches", row, "Legacy counters or completion claim do not match linked records. Review archived data.")
            produced = yes = no = 0
        reason = None if complete else ("LEGACY_" + str(row.get("status")) if coherent else "LEGACY_REVIEW")
        operating = row.get("mode") if row.get("mode") in {"PRODUCTION", "BENCHMARKING"} else "PRODUCTION"
        db.execute("""
            INSERT INTO batches(batch_id,operating_mode,target_parts,produced_total,inspected_total,
                accepted_total,rejected_total,batch_status,started_at,finished_at,abortion_reason,production_finished)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (batch_id, operating, target, produced, yes + no, yes, no,
              "COMPLETED" if complete else "ABORTED", row.get("created_at"), row.get("updated_at"),
              reason, int(complete)))
        batches[batch_id] = coherent
        report["normalized"]["batches"] += 1

    parts = {}
    for row in records["parts"]:
        part_id = numeric(row.get("part_id"), "PART")
        batch_id = numeric(row.get("batch_id"), "BATCH")
        if not part_id or part_id in parts or batch_id not in batches:
            flag("parts", row, "Part identity is invalid, duplicated or orphaned.")
            continue
        status = "PRODUCED" if batches[batch_id] else "UNRESOLVED"
        timestamp = row.get("created_at") if status == "PRODUCED" else None
        db.execute("INSERT INTO parts(part_id,batch_id,status,produced_at) VALUES (?,?,?,?)",
                   (part_id, batch_id, status, timestamp))
        parts[part_id] = batch_id
        report["normalized"]["parts"] += 1

    occupied_parts, used_jobs = set(), set()
    for row in records["inspection_jobs"]:
        job_id, part_id = numeric(row.get("job_id"), "JOB"), numeric(row.get("part_id"), "PART")
        if not job_id or job_id in used_jobs or part_id not in parts or part_id in occupied_parts:
            flag("inspection_jobs", row, "Job identity is invalid, duplicated or orphaned.")
            continue
        batch_id = parts[part_id]
        result = row.get("result")
        valid = (batches[batch_id] and row.get("status") in {"COMPLETE", "COMPLETED"}
                 and result in ("PASS", "FAIL"))
        if not valid:
            flag("inspection_jobs", row, "Inspection outcome is uncertain and cannot be replayed.")
        db.execute("""
            INSERT INTO inspection_jobs(job_id,batch_id,part_id,status,result,queued_at,completed_at)
            VALUES (?,?,?,?,?,?,?)
        """, (job_id, batch_id, part_id, "COMPLETED" if valid else "UNRESOLVED",
              result if valid else None, row.get("created_at"), row.get("updated_at")))
        occupied_parts.add(part_id)
        used_jobs.add(job_id)
        report["normalized"]["inspection_jobs"] += 1

    used_commands = set()
    for row in records["commands"]:
        cmd_id = numeric(row.get("command_id"), "CMD")
        if not cmd_id or cmd_id in used_commands or not row.get("command_name"):
            flag("commands", row, "Command identity is invalid or duplicated.")
            continue
        db.execute("INSERT INTO commands(cmd_id,msg_type,status,payload,created_at) VALUES (?,?,'HISTORICAL',?,?)",
                   (cmd_id, row["command_name"], canonical(row), row.get("created_at") or now_text()))
        used_commands.add(cmd_id)
        report["normalized"]["commands"] += 1
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--backup", required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination, args.backup), indent=2))
