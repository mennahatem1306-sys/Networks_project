# MCT431 Milestone 1 | Requirement 5.5 — Duplicate suppression

**Result: PASS on the physical ESP32 B.** Batch 34 used configuration 52 with ten parts, 1000 ms production, 2000 ms inspection, N = 3, and K = 0. The selected inspection was **job 276, part 277, session 2194**.

The test gateway resent the exact inspection command once after the original `ACCEPTED`, using a new application sequence. ESP32 B accepted the duplicate through its existing job lookup while the original inspection was active. After receiving the normal receipt for the first result, the station replayed that result once with a fresh application sequence. The original and duplicate requests had identical decoded fields apart from sequence. The two results also matched field for field apart from sequence.

| Event | UTC on 17 November 2026 | Job ID | Application sequence | Evidence |
| --- | --- | ---: | ---: | --- |
| Original `INSPECT_PART` | 18:07:40.911168 | 276 | 34 | Wireshark 277 and SQLite 6900 |
| Inspection started | 18:07:41.044 | 276 | 34 | ESP32 B UART `START` |
| Duplicate `INSPECT_PART` | 18:07:41.039984 | 276 | 35 | Wireshark 281 and gateway replay log. The duplicate used the normal wire path. |
| Duplicate detected | 18:07:41.095 | 276 | 35 | ESP32 B UART `DUPLICATE state=active` |
| Inspection completed | 18:07:43.035 | 276 | 47 on result | One ESP32 B UART `COMPLETE`. Wireshark 292 shows result PASS, SQLite 6933 records one completion. |
| Duplicate result received | 18:07:43.123846 | 276 | 48 | Wireshark 295 and SQLite 6938, stage `DUPLICATE` |
| Duplicate result acknowledged without another count | 18:07:43.153769 | 276 | 40 on receipt | Wireshark 296 applies to the duplicate result. Gateway snapshots record identical counters before and after processing it. |

Times are packet capture times for wire events and host receipt times for UART observations. The board uptime timestamps independently place its start at 34373 and completion at 36373, an elapsed time of 2000 ms. The UART contains exactly one `START` and one `COMPLETE` for job 276. The original acceptance is Wireshark frame 279 and SQLite event 6909. The gateway acknowledged the original result in frame 294, before the station replayed it. The injected command replay is a wire send rather than a gateway timeout, so it has no SQLite retry event.

| Gateway checkpoint | Inspected | Accepted | Rejected |
| --- | ---: | ---: | ---: |
| Before the original result | 0 | 0 | 0 |
| After the original result | 1 | 1 | 0 |
| Immediately before processing the duplicate result | 1 | 1 | 0 |
| Immediately after processing the duplicate result | 1 | 1 | 0 |

The gateway log shows these snapshots at the two result handlers. SQLite lists one completed job row for job 276 and separate `COMPLETED` and `DUPLICATE` message events. The dashboard batch finished `COMPLETED` with **10 produced, 10 inspected, 7 accepted and 3 rejected**.

## Restoration

Aya's original firmware was flashed back and verified. The test gateway process was replaced by the normal gateway entry point. Both boards synchronized and are online. The gateway is IDLE with the ten-part settings reapplied as configuration 53. All state changes used the gateway's normal commands and transactions. No counters were edited manually.

## Evidence

- `R05-5-Verified-Capture.pcapng` and `Decoded-TCP-Packets.csv` contain the physical network trace and decoded job payloads.
- `ESP32B-UART.log` records reception, duplicate detection, one start, one completion, and the result replay.
- `Gateway-Diagnostics.log` contains the injected command send and before/after counter snapshots.
- `SQLite-Message-Events.csv`, `SQLite-Inspection-Jobs.csv`, and `SQLite-Batch.csv` contain the durable gateway records.
- `Restore-Firmware.log`, `Normal-Gateway.log` and `After-Restore.json` document restoration.
