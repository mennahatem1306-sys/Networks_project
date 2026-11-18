# Hardware testing


Do not use Arduino Serial Monitor to type plain JSON into this binary link.
The gateway must encode SLIP/ATR/CRC first. Close Serial Monitor before opening
the same COM port in a gateway/test program. A USB serial port is normally owned
by only one process at a time.

No plaintext debug logging is mixed into `Serial`. Responses, ERROR messages,
ATR sequence numbers and gateway send timestamps support the serial evidence.
The `UartOutput` object also retains last diagnostic/event/ID/retry counters for
debugger inspection. Have Menna save raw and decoded serial logs with units,
the firmware revision and the examiner's configuration. Host test traces are
software evidence only, not actual USB/hardware captures.

Suggested board checks after uploading:

1. Observe HELLO; synchronize using the reported boot ID; configure a cycle.
2. Arm a batch. Verify no parts appear without permits.
3. Send a permit; verify one report after the full cycle and matching IDs.
4. Duplicate the permit before and after completion: no additional part count.
5. Drop a report ACK: observe bounded retries with unchanged IDs/new ATR seq.
6. Pause/Resume/Stop during an active timer; verify the documented behavior.
7. Change cycle configuration mid-cycle; verify the active cycle is unchanged.
8. Stop gateway heartbeats: verify FAULT and fresh-session recovery.
9. Corrupt a CRC and malformed frame: verify no production.
10. Power-cycle A: verify boot ID increments and old-session work is not resumed.

The original handoff's first-send/acceptance age calculation and
LATE_UNVERIFIED/LATE_RECONCILED policy run on the **gateway**, not this station.
A retains the original permit and returns its cached result for that policy.
