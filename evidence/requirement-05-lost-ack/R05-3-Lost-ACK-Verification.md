# MCT431 Milestone 1 | Requirement 5.3 — Controlled lost ACK

**Result: PASS on the physical ESP32 B.** Operator-started batch 31 ran with 10 parts, 1000 ms production, 2000 ms inspection, N = 3, K = 0, and configuration version 44. The selected work was part 261, job 261, inspection session 2182.

| Event | UTC on 17 November 2026 | Job ID | Application sequence | Evidence |
| --- | --- | ---: | ---: | --- |
| Original `INSPECT_PART` | 17:42:49.068428 | 261 | 236 | Capture frame 949 and SQLite event 6354 |
| First `DELIVERY_ACK` and `ACCEPTED` suppressed | Between original and retry | 261 | — | No application ACK in frames 949–952. Frame 950 is a TCP transport ACK. Frame 951 carries a board heartbeat. |
| Delivery timeout | 17:42:50.027, inferred from the retry event | 261 | — | SQLite event 6362 records retry 1, 1.028 s after the recorded original send. |
| Retransmitted `INSPECT_PART` | 17:42:50.034158 | 261 | 238 | Frame 953 and event 6362. Session, batch, part, job, and configuration IDs matched the original. |
| `DELIVERY_ACK` received | 17:42:50.149173 | 261 | 246 | Frame 954 and event 6374. References `INSPECT_PART`. |
| `ACCEPTED` received | 17:42:50.149177 | 261 | 247 | Frame 955 and event 6375. References `INSPECT_PART`. |
| `INSPECTION_RESULT` received | 17:42:51.116866 | 261 | 249 | Frame 961 and event 6380. Part 261, job 261, FAIL, elapsed 2000 ms. |
| Gateway acknowledged the result | 17:42:51.248821 | 261 | 241 | Frame 964. `DELIVERY_ACK` references `INSPECTION_RESULT`. |
| Duplicate execution prevented | Verified at completion | 261 | 249 | Frame 961 arrived 2.048 s after the original and 1.083 s after the retry. The reported 2000 ms duration fits the original inspection, and SQLite has one completed job and one committed result. |

Packet rows use Wireshark timestamps. The gateway records the configured one-second timeout through its retry event, with no standalone timeout event. On the wire the attempts were 0.966 s apart. The independently recorded gateway send and retry times were 1.028 s apart. The job timing and unchanged IDs provide evidence that the board had registered the original job and recognized the retransmission without restarting the inspection. A board UART trace from the lost-ACK run was unavailable.

Batch 31 finished with 10 produced, 10 inspected, 6 PASS, and 4 FAIL. The failed parts were 261, 264, 267, and 270, consistent with N = 3 and K = 0. Job 261 was the batch’s sole inspection retry. The next nine jobs received normal acknowledgements.

## Restoration

The original firmware image was re-flashed to COM13 and the write hash verified. The gateway and both devices returned to service. In a separate one-part batch 32 at the same cycle, inspection time, N, and K, job 271 received a first-attempt `DELIVERY_ACK` in about 86 ms on the wire, then `ACCEPTED`, then its PASS result. The capture and ESP32 B UART log corroborate those responses. There was no retry. After verification, the operator's ten-part settings were reapplied as configuration version 46. The gateway was IDLE and both devices were online.

## Evidence

- `R05-3-Verified-Capture.pcapng` contains the live port-5001 capture through both runs. The table uses its original frame numbering.
- `Decoded-TCP-Packets.csv` contains the actual decoded packet payloads. The ESP32 B packets are its wire-side evidence for the lost-ACK run.
- `Gateway-Job261-Application.log` contains the original gateway console messages for job 261.
- `SQLite-Message-Events.csv`, `SQLite-Inspection-Jobs.csv`, and `SQLite-Batches.csv` contain the gateway records for both batches.
- `ESP32B-Normal-Firmware-UART.log` and `Restore-Firmware.log` document normal-firmware operation and the verified flash.
