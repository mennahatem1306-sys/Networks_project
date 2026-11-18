# MCT431 Milestone 1 | Requirement 5.4 — Bounded retries and retry exhaustion

**Result: PASS on the physical ESP32 B.** Batch 33 used configuration 48 with 10 target parts, a 1000 ms production cycle, a 5000 ms inspection time, N = 3, and K = 0. The fault injection selected part 272, job 272, on inspection session 2186.

## Running gateway settings

`team_gateway/inspection_link.py` creates an initial delivery deadline of `now + 1` second and schedules each retry with the same interval. The transaction starts at attempt 1. At expiry with `attempts >= 4`, the gateway calls its fault handler with `RETRY_EXHAUSTED`. The running logic therefore allows three retransmissions and four total attempts.

## Evidence timeline

Times are UTC on 17 November 2026. The main timestamp column uses the gateway event clock. Capture frame times are listed separately where available.

| Event | Gateway UTC | Job ID | Application sequence | SQLite event or Wireshark frame |
| --- | --- | ---: | ---: | --- |
| Original `INSPECT_PART` | 17:54:32.317 | 272 | 82 | Event 6694. Frame 425 at 17:54:32.361. |
| Retransmission 1 | 17:54:33.337 | 272 | 84 | Event 6702. Frame 431 at 17:54:33.326. |
| Retransmission 2 | 17:54:34.338 | 272 | 86 | Event 6714. Frame 437 at 17:54:34.327. |
| Retransmission 3 | 17:54:35.365 | 272 | 88 | Event 6726. Frame 443 at 17:54:35.461. |
| Retry limit reached | About 17:54:36.37 | 272 | — | Gateway console warning `RETRY_EXHAUSTED`. SQLite events 6738 and 6740 preserve the reason. |
| Batch marked `ABORTED` | 17:54:36.371 | 272 | — | Event 6738. The batch row records its finish at 17:54:36.368. |
| Gateway enters `FAULT` | 17:54:36.373 | 272 | — | State transition event 6740. |

ESP32 B's UART log identifies all four received attempts with matching job, part, batch, session and application sequence. Attempt 1 was logged as `duplicate=no`, and attempts 2–4 as `duplicate=yes`. On all four it explicitly logged that delivery ACK, acceptance and the result were withheld and the inspection was never scheduled. The UART log includes host UTC receipt time and the station's monotonic uptime.

The Wireshark capture shows normal application heartbeats during the retries. ESP32 B sent a heartbeat in frame 447 at 17:54:36.154, about 0.22 s before the recorded Fault. The gateway closed the old TCP stream after the last attempt, as shown by frame 449. The capture and gateway events show four sends and no fifth attempt. The application sequences advance while the logical job ID and other identifiers stay fixed.

## Final batch and restoration

Batch 33 ended `ABORTED` with **4 produced, 0 inspected, 0 accepted and 0 rejected**. SQLite lists job 272 as `UNRESOLVED` with no verdict. The other three queued inspections were cancelled. The gateway cleared the queue and pending work. The stored abortion reason is `ESP32 B: RETRY_EXHAUSTED` for `INSPECT_PART` job 272. No later work send or retry was recorded for batch 33.

I captured the Fault snapshot before restoring Aya's normal firmware. The flash write verified successfully and the normal image contains neither test hook. Both devices returned online. The gateway was left in `FAULT` with batch 33 `ABORTED`; no reset or subsequent batch was run.

## Evidence files

- `R05-4-Verified-Capture.pcapng` — physical Wi-Fi capture of TCP port 5001 through the timeout and reconnect.
- `Decoded-TCP-Packets.csv` — decoded request and heartbeat packets.
- `Gateway-Job272-Application.log` — original gateway diagnostic messages.
- `ESP32B-UART.log` — physical station log, including every attempt and the duplicate classification.
- `SQLite-Message-Events.csv`, `SQLite-Inspection-Jobs.csv`, and `SQLite-Batch.csv` — durable gateway records.
- `Gateway-Fault-Snapshot.json`, `Gateway-After-Restore.json`, and `Restore-Firmware.log` — final state and firmware restoration evidence.
