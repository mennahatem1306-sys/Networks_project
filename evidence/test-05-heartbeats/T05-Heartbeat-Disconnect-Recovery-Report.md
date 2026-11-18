# MCT431 Milestone 1 | Test 5 — Heartbeats, disconnection, and recovery

**Result:** ESP32 B heartbeat, fault, and recovery checks passed on the physical setup. ESP32 A’s USB heartbeat cadence and a fresh post-reset batch remain unverified in this run.

The gateway ran at `10.181.17.167`. Aya’s ESP32 B communicated from `10.181.17.232` over TCP port `5001`. The operator disconnected ESP32 B during batch 14, which had a target of 20 parts. Times below are UTC on 17 November 2026.

| Time (UTC) | Observation | Evidence |
| --- | --- | --- |
| Before batch | The gateway and ESP32 B sent application `HEARTBEAT` messages about once per second in both directions while idle. | Idle TCP capture |
| 15:40:31.336 | Last captured heartbeat from ESP32 B on the old connection, session 2148. | Frame 292, TCP stream 1 |
| 15:40:34.386–34.390 | The gateway aborted batch 14 and entered `FAULT`. The recorded reason was `ESP32 B: Handshake or heartbeat timed out`. | Batch row and gateway events 2274–2275 |
| 15:40:39.770–40.126 | ESP32 B opened a new TCP connection, sent `HELLO` in `SAFE_IDLE`, and completed `SYNC_SESSION` with `READY`. The gateway recorded session 2150 ready. | Frames 416, 420, 422 and 424–426, TCP stream 26. Gateway events 2282–2283 |
| 15:40:42.514–42.515 | The operator submitted `RESET_FAULT` after synchronization and the gateway returned to `IDLE`. | Gateway events 2286–2289, command 2348 |

The last captured ESP32 B heartbeat preceded the recorded fault by **3.054 seconds**. This measures the interval from the last captured heartbeat, rather than from the physical unplug action. The batch remained `ABORTED` at **11 produced, 5 inspected, 3 accepted, and 2 rejected**. Job 77 was outstanding when the connection failed and had no recorded inspection result. The reconnect stream contained no new inspection request. Returning to `IDLE` after manual reset did not resume the aborted batch.

## Evidence files

- `T05-Evidence/T05-Batch14-Disconnect.pcapng` is a byte-for-byte copy of the supplied capture. Use `tcp.stream == 1 || tcp.stream == 26` in Wireshark.
- `T05-Evidence/T05-Idle-TCP-Heartbeats.pcapng` records idle heartbeat traffic in both directions.
- `T05-Evidence/T05-Batch14-Gateway-Events.csv` and `T05-Evidence/T05-Batch14-Batch.json` contain the durable gateway events and final batch record.

The Wi-Fi captures cover ESP32 B. Verifying ESP32 A’s bidirectional heartbeat cadence requires a USB serial trace. The new-batch fresh-ID check requires starting a separate batch after reset.
