# MCT431 Milestone 1 — Test 3
## Delivery, acceptance, and completion

**Test date:** 17 November 2026

**Result:** PASS for the observed application-message sequence

**Batch:** 11 · **Part:** 55 · **Job:** 55 · **Session:** 2141

**Link:** Gateway `10.181.17.167:5001` ↔ ESP32 B `10.181.17.232`

### Wireshark and SQLite comparison

The Test 1 capture was reused for this check. Packet numbers below refer to
the project-only capture included in the evidence bundle.

| Frame | Direction and application message | What it confirms | SQLite event ID |
| ---: | --- | --- | ---: |
| 119 | Gateway → B · `INSPECT_PART` | The gateway requested inspection of part 55 as job 55. | 1636 |
| 120 | B → Gateway · `DELIVERY_ACK` | B received the application request. The reference is `INSPECT_PART`. | 1644 |
| 121 | B → Gateway · `ACCEPTED` | B validated and accepted that same job. | 1645 |
| 127 | B → Gateway · `INSPECTION_RESULT` | B finished job 55 for part 55 and reported `PASS`. | 1662 |
| 128 | Gateway → B · `DELIVERY_ACK` | The gateway confirmed receipt of the result. The reference is `INSPECTION_RESULT`. | Outgoing receipt verified in Wireshark |

The three application stages are separate messages in the TCP payload.
Wireshark's TCP ACK flags are transport metadata. Each of the first four
packets matches a gateway event by message type, job ID, and sequence number.
The outgoing receipt follows the result packet and the saved completion timestamp.

### Saved result

The SQLite `inspection_jobs` query returned **exactly one row** for job 55.
It references part 55 and batch 11, with status **COMPLETED** and verdict **PASS**.
The event query also returned exactly one completed `INSPECTION_RESULT` for this
job. This test verifies the normal exchange and its single recorded completion.
Deliberate duplicate delivery and retries are separate tests.

### Reproduce the view

In Wireshark, use this display filter on the included capture:

```wireshark
frame.number == 119 || frame.number == 120 || frame.number == 121 || frame.number == 127 || frame.number == 128
```

Select a frame and expand **Data** to read its JSON, or use **Follow → TCP Stream**.
The evidence bundle also contains the five decoded packets, the gateway event
rows, and the saved inspection row as separate CSV files.
