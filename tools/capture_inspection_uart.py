"""Read the inspection station's diagnostic UART without resetting it."""

from datetime import datetime, timezone
import sys
from time import monotonic

import serial


with serial.Serial(port=None, baudrate=115200, timeout=0.5) as station:
    station.port = sys.argv[1]
    station.dtr = False
    station.rts = False
    station.open()
    deadline = monotonic() + (int(sys.argv[2]) if len(sys.argv) > 2 else 120)
    while monotonic() < deadline:
        line = station.readline()
        if line:
            print(datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  line.decode("utf-8", errors="replace").strip(), flush=True)
