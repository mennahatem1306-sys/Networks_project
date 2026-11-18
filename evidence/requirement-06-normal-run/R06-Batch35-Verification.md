# MCT431 Milestone 1 | Requirement 6 — Normal-operation evidence

**PASS.** The Operator started batch **35** on 17 November 2026. Configuration **58** used 10 parts, 1000 ms production, 2000 ms inspection, N = 3, and K = 0. The batch completed at **18:40:00.801 UTC** (SQLite event **7408**) with **10 produced, 10 inspected, 7 accepted, and 3 rejected**. SQLite contains ten distinct produced part rows and ten completed inspection jobs. Accepted plus rejected equals the target. Both physical stations remained online and the gateway returned to IDLE.

## Selected part

**Part 287, batch 35, configuration 58** was produced using USB permit **2800** in session **2201** and inspected as TCP job **286** in session **2202**. Those are the identifiers in the transmitted payloads. SQLite joins the permit and inspection job through part 287. The verdict was **PASS**.

| Link | Direction and application message | UTC | Application seq | Evidence |
| --- | --- | --- | ---: | --- |
| USB | Gateway → A `PRODUCTION_PERMIT` | 18:39:37.168235 | 69 | USB trace, SQLite 7241 |
| USB | A → Gateway `DELIVERY_ACK` | 18:39:37.200629 | 78 | USB trace, SQLite 7242 |
| USB | A → Gateway `ACCEPTED` | 18:39:37.242728 | 79 | USB trace, SQLite 7243 |
| USB | A → Gateway `PART_PRODUCED` | 18:39:38.207897 | 81 | USB trace, SQLite 7246 |
| USB | Gateway → A `DELIVERY_ACK` for `PART_PRODUCED` | 18:39:38.223740 | 71 | USB trace |
| TCP | Gateway → B `INSPECT_PART` | 18:39:38.285606 | 62 | Frame 340, SQLite 7249 |
| TCP | B → Gateway `DELIVERY_ACK` | 18:39:38.322063 | 70 | Frame 341, SQLite 7257 |
| TCP | B → Gateway `ACCEPTED` | 18:39:38.325240 | 71 | Frame 342, SQLite 7258 |
| TCP | B → Gateway `INSPECTION_RESULT` | 18:39:40.349676 | 74 | Frame 352, SQLite 7274 |
| TCP | Gateway → B `DELIVERY_ACK` for `INSPECTION_RESULT` | 18:39:40.386356 | 65 | Frame 353 |

TCP times come from the original Wi-Fi capture. USB times come from the gateway's serial writes and reads. Reception timestamps on a split USB frame correspond to the chunk containing its closing delimiter. Transport sequences are independent for each sending direction. Display only the five TCP messages above with `frame.number in {340, 341, 342, 352, 353}`.

## Framing and measurements

The USB decoder validated CRC and ATR length on **111 complete frames** in the exported run window. The selected permit begins and ends with SLIP `C0` and has ATR header `00 00 00 00 00 45 00 91`, sequence 69, **145 JSON bytes**, and CRC `0x5EAE`. The production report has header `00 00 00 00 00 51 00 A1`, sequence 81, **161 JSON bytes**, and CRC `0x4C5B`. Each ATR record contains the eight-byte header, declared JSON bytes, and two CRC bytes. The exact recorded SLIP bytes are in the selected raw-frame export. Batch traffic also includes two observed `DB DC` escapes for the reserved byte `C0`.

The TCP reassembly verified the four-byte big-endian prefix on all 50 selected batch messages. For job 286, frame 340 has a 148-byte JSON request and frame 352 has a 198-byte JSON result. Measurements for part 287 were **1039.662 ms** from USB permit write to completed report read, **36.457 ms** from TCP request to application delivery ACK, and **2064.070 ms** from TCP request to result. ESP32 B reported **2000 ms** inspection execution. These are different intervals and their measurement points are shown in the CSV.

## Files

| Filename | Contents |
| --- | --- |
| `R06-Batch35-Original-WiFi.pcapng` | Original 596-packet Wi-Fi capture. Frame numbers above refer to this unfiltered file. |
| `TCP-Decoded-Batch35.csv` | Actual length-prefixed payloads, directions, timestamps, sequences, and frame references for all ten jobs. |
| `USB-Raw-Batch35.log`, `USB-Decoded-Batch35.jsonl` | Original timestamped COM8 byte log for the batch window and decoded records with ATR header, payload length and CRC values. |
| `Part287-USB-Raw-Frames.jsonl`, `USB-SLIP-Escape-Examples.json` | Exact SLIP wire bytes for the selected exchange and observed escape examples. |
| `SQLite-Batch.csv`, `SQLite-Parts.csv`, `SQLite-Inspection-Jobs.csv`, `SQLite-Message-Events.csv`, `SQLite-Configuration.csv` | Read-only exports of durable gateway records. |
| `Batch35-Measurements.csv`, `Part287-End-to-End.json` | Per-part measured intervals and the selected part's combined decoded messages and SQLite rows. |
| `Gateway-Raw-and-Decoded.log`, `Gateway-Final-Snapshot.json` | Application and raw serial logs plus final dashboard status. |

No independent USB bus capture or final Wireshark drop count was available. The gateway's COM8 byte log is the USB wire evidence for this run. The final completion and result receipt are present in the saved TCP capture.
