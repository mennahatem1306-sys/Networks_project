import argparse
import csv
import json
from pathlib import Path

FIELDS = (
    "monotonic_s",
    "direction",
    "msg_type",
    "device_id",
    "session_id",
    "seq",
    "cmd_id",
    "job_id",
    "batch_id",
    "part_id",
    "config_version",
    "inspection_elapsed_ms",
    "result",
    "ref_msg_type",
    "error_code",
    "reason",
)


def export_trace(source, destination):
    with (
        Path(source).open(encoding="utf-8") as trace,
        Path(destination).open("w", newline="", encoding="utf-8") as output,
    ):
        writer = csv.DictWriter(output, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for line in trace:
            if line.strip():
                event = json.loads(line)
                writer.writerow(
                    {
                        **event["message"],
                        "monotonic_s": event["monotonic_s"],
                        "direction": event["direction"],
                    }
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export an actual gateway trace to CSV"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    if arguments.source.resolve() == arguments.destination.resolve():
        parser.error("source and destination must be different files")
    export_trace(arguments.source, arguments.destination)
