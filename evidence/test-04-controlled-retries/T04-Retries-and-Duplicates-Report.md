# MCT431 Milestone 1 | Test 4 — Retries and duplicate prevention

**Result:** The controlled gateway checks passed. The physical ESP32 B lost-ACK and duplicate-execution check remains blocked. The checked firmware and gateway had no built-in option to suppress the first inspection delivery ACK.

The test ran against the integrated `TeamGateway` code and its real TCP adapter, USB framing code, and SQLite core. It used isolated databases, Zeina’s compiled firmware in a host fixture, and a simulated ESP32 B sending framed application messages over TCP loopback. Each scenario started a one-part batch using a 500 ms production cycle, 1000 ms inspection time, N = 3, and K = 0. The harness submitted the same gateway commands the dashboard uses. It used loopback ports 15001 and 15002. Afterward the live dashboard still showed IDLE and both boards online.

## 4A — Lost acknowledgement

The simulated inspection peer suppressed the first application `DELIVERY_ACK` and deferred `ACCEPTED` and the result until the retry. This isolated the one-second delivery timer. Wireshark frames 26 and 32 contain the initial `INSPECT_PART` and a retransmission 0.997 s later. Both carry session 2, batch 1, part 1, job 1, and config version 1. Their message sequences are 3 and 5. Gateway event 60 records retry index 1. The fixture supplied one logical verdict and the gateway committed it once. The fixture’s execution count is simulated. A physical run is needed to verify that Aya’s board executes the inspection only once when the command is retried.

## 4B — Duplicate reports

| Report | Before replay | After replay | SQLite events |
| --- | --- | --- | --- |
| `PART_PRODUCED`, command 8, part 1 | Produced 1 | Produced 1 | 54 COMPLETED, 59 DUPLICATE |
| `INSPECTION_RESULT`, job 1, part 1 | Produced 1, inspected 1, accepted 1, rejected 0 | Produced 1, inspected 1, accepted 1, rejected 0 | 63 COMPLETED, 67 DUPLICATE |

The USB fixture suppressed the gateway’s first `PART_PRODUCED` receipt, so Zeina’s host firmware naturally resent the report. The TCP fixture resent the result with a fresh transport sequence after receiving the first receipt. The gateway acknowledged both inspection deliveries. The completed SQLite batch held one part row and one inspection job row.

## 4C — Retry exhaustion

The simulated peer kept sending heartbeats while withholding every response to `INSPECT_PART`. Wireshark frames 75, 81, 87, and 93 show the original and three retransmissions at approximately one-second intervals. All four transmissions preserve the same logical IDs. Gateway events 58–61 record the initial send and retries 1–3. The gateway logged `RETRY_EXHAUSTED`, entered FAULT, aborted the batch, and left job 1 UNRESOLVED with zero inspections counted. The harness continued ticking for three seconds after FAULT and observed no fifth transmission.

## Evidence

- `T04-Evidence/T04_Lost_ACK_Loopback.pcapng` contains 102 packets from the two controlled TCP scenarios. To isolate outbound inspection requests in Wireshark, use `(tcp.srcport == 15001 || tcp.srcport == 15002) && tcp contains "INSPECT_PART"`.
- `T04-Evidence/gateway.log` contains decoded USB and TCP messages and the exhaustion warning.
- `T04-Evidence/recover.json` and `T04-Evidence/exhaust.json` contain event exports, counter snapshots, and retry timing. The corresponding SQLite databases are included.
