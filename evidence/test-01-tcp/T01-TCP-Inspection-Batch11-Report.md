# MCT431 Milestone 1 — Test 1
## ESP32 B TCP inspection communication

**Date:** 17 November 2026

**Result:** PASS for the captured TCP communication test

**Batch:** 11 · **Configuration version:** 14

**Gateway:** `10.181.17.167:5001` · **ESP32 B:** `10.181.17.232`

### A. Capture and batch configuration

Traffic was captured on the gateway's Wi-Fi interface. The supplied packet file
contains the bidirectional inspection connection only. It was exported from the
original capture, so displayed packet numbers in the two files differ.

The captured run used **3 parts, 500 ms production, 1000 ms inspection, N = 3,
and K = 0**. The gateway database recorded **3 produced, 3 inspected, 2 PASS,
1 FAIL**, and a **Completed** batch. The written test plan specifies **1000 ms
production and 2000 ms inspection**. A run at those exact timings remains due
if required for submission.

### B. Match the application messages

For each job, the gateway sent `INSPECT_PART`. ESP32 B then returned a distinct
`DELIVERY_ACK`, `ACCEPTED`, and `INSPECTION_RESULT`. The gateway sent a final
`DELIVERY_ACK` referencing the result. Request and result matched on batch ID,
part ID, job ID, and session ID. Packet numbers below refer to the attached
project-only capture.

| Job ID | Part ID | Request | Delivery ACK | Acceptance | Result packet | Result receipt | Verdict |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 55 | 55 | 119 | 120 | 121 | 127 | 128 | PASS |
| 56 | 56 | 130 | 133 | 134 | 140 | 142 | PASS |
| 57 | 57 | 144 | 145 | 147 | 152 | 155 | FAIL |

Part 57 failed because 57 is divisible by 3. Parts 55 and 56 passed. The three
packet-capture verdicts match the three results recorded in SQLite.

### C. Check the four-byte length prefix

The TCP payload was reassembled separately in each direction. Every complete
application message was decoded using its four-byte unsigned big-endian length
prefix. All **90 frames** decoded as JSON and each payload was within the
**1–1024 byte** limit.

| Message for job 55 | Original packet | Attached packet | Prefix | JSON bytes | TCP payload bytes |
| --- | ---: | ---: | --- | ---: | ---: |
| `INSPECT_PART` | 476 | 119 | `00 00 00 94` | 148 | 152 |
| `INSPECTION_RESULT` | 494 | 127 | `00 00 00 C6` | 198 | 202 |

The first JSON byte follows the prefix and is `7B` (the opening brace). The
request has **152 − 4 = 148** JSON bytes. The result has **202 − 4 = 198**.
Both prefixes match the actual data, and the next message in each direction
starts at the expected offset.

### Evidence

- `T01-TCP-Inspection-Batch11.pcapng` contains the inspected TCP connection.
- The `stepctest1` text export independently shows the request at line 576 and
  the result at line 623. In that export, the next messages begin at offsets
  `0x1041` and `0x149B`, respectively.
- The integrated gateway's SQLite records confirm the batch settings, final
  counters, and per-job verdicts.
