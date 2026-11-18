"""Stream new Gateway diagnostic events from a read-only SQLite connection."""

import json
import os
from pathlib import Path
import sqlite3
from time import monotonic, sleep


default_database = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MCT431" / "team-gateway.sqlite3"
database = Path(os.environ.get("MCT431_DATABASE", str(default_database))).resolve()
with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
    connection.row_factory = sqlite3.Row
    cursor = connection.execute("SELECT max(event_id) FROM message_events").fetchone()[0] or 0
    print(json.dumps({"logging_started_after_event": cursor}), flush=True)
    deadline = monotonic() + 1800
    while monotonic() < deadline:
        rows = connection.execute(
            "SELECT * FROM message_events WHERE event_id > ? ORDER BY event_id", (cursor,)
        ).fetchall()
        for row in rows:
            print(json.dumps(dict(row)), flush=True)
            cursor = row["event_id"]
        sleep(0.5)
