# MCT431 Milestone 1 | Requirement 5.6 — Invalid messages, CRC, and stale results

**Result: PASS for 5.6A, 5.6B, and 5.6C.** All tests ran separately while the gateway was IDLE, with both physical ESP32 boards online and configuration 54 applied. No Operator batch was needed. The harness owned COM8 and forwarded ESP32 B's TCP connection while the normal receiver logic handled each input. Times are UTC on 17 November 2026.

| Test | Injected message | Expected behavior | Actual behavior | Evidence | Result |
| --- | --- | --- | --- | --- | --- |
| 5.6A Invalid message | `INSPECT_PART`, seq 33, without required `job_id`. Valid 4-byte big-endian length prefix, 135 JSON bytes. | Reject before scheduling an inspection. | Physical ESP32 B returned `REJECTED / MISSING_FIELD`, seq 38. No job or result was recorded. | Wi-Fi frames 1968 and 1978 at 18:17:55.545 and 18:17:55.788. | PASS |
| 5.6B Corrupted CRC | SLIP-encoded ATR production permit, seq 42, test cmd 4294967000. One JSON byte changed part 277 to 278 after CRC calculation. | Reject at CRC validation before executing the permit. | Physical ESP32 A returned `ERROR / CRC_ERROR` with reason `Invalid serial record discarded`, seq 46. No permit acceptance or production was recorded. | COM8 transmitted 163 bytes at 18:17:57.221. Raw serial response received at 18:17:57.252. | PASS |
| 5.6C Stale result | Exact framed batch 11 result for job/part 57, old session 2141, seq 1039, verdict FAIL. | Reject an old-session result before updating state. | Gateway logged `Ignored old TCP session message: INSPECTION_RESULT`. Current session 2198 and last live seq 41 confirm this exercised the session guard after the sequence check. | Replay at 18:17:58.671, verified capture frame 2011 on the harness-to-gateway TCP leg. The original result is SQLite event 1674 and original capture frame 152. | PASS |

## Serial verification

The first CRC value was calculated on the valid ATR record using CRC-16/CCITT-FALSE, then kept on the wire after changing one byte of the payload. The SLIP delimiters and ATR length stayed valid. ESP32 A received **0xC8B4** in the CRC field. Recalculating CRC on the modified bytes yields **0x873E**. The actual transmit hex is in `Corrupted-USB-Frame.hex`. The gateway log contains the real raw and decoded CRC error from the board. The gateway used its own open serial connection.

## Session and database verification

The harness replayed the exact length-prefixed frame from the physical batch 11 capture without changing any IDs, sequence, JSON fields, or wire bytes. It sent the frame on the temporary TCP proxy's loopback connection to the unchanged gateway receiver. The gateway rejected it because session 2141 was stale in session 2198. The result was already historical, but this test exercised the session guard before the database duplicate check.

| Counter or state | Before each injection | After each injection |
| --- | --- | --- |
| Produced / inspected / accepted / rejected | 10 / 10 / 7 / 3 | 10 / 10 / 7 / 3 |
| Gateway and latest batch | IDLE, batch 34 COMPLETED | IDLE, batch 34 COMPLETED |
| Earlier aborted batch 33 | ABORTED | ABORTED |
| SQLite message-event high-water ID | 7121 | 7121 |

Event 1674 identifies the original valid result. The three rejected injections produced no new SQLite message-event rows. The receiver decisions are evidenced by the physical response packets and application logs, and the read-only snapshots record unchanged job counts, part count, and batch records.

## Restoration

The test gateway and TCP proxy were stopped. The normal gateway reconnected directly to Aya's board on Wi-Fi TCP port 5001. Both ESP32 boards are online, and the gateway is IDLE with the ten-part settings restored as configuration 55. Neither board's firmware was changed for these tests. No operator batch or test-related Fault reset was needed.

## Included evidence

- `R05-6-Verified-Capture.pcapng` and `Decoded-TCP-Packets.csv` include the physical Wi-Fi reject and the byte-for-byte stale replay on the loopback interface. For 5.6A, frames 1962 and 1972 show the same messages on the other side of the proxy.
- `Gateway-Test-Excerpt.log` includes actual transmit and receiver decisions. `Corrupted-USB-Frame.hex` is the transmitted SLIP frame.
- `Original-Stale-Frame.hex` and `Original-Stale-SQLite-Event.json` are the historical evidence used for replay.
- `Test-A-Evidence.json`, `Test-B-Evidence.json`, and `Test-C-Evidence.json` contain before/after gateway and SQLite snapshots.
- `After-Restore.json` and `Normal-Gateway.log` document removal of the harness and normal gateway operation.
