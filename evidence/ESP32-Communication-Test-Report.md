# MCT431 Milestone 1 — ESP32 communication test report

**Test date:** 17 November 2026

**Batch:** 9 · **Final status:** Completed

**Configuration version:** 12 · **Requested parts:** 10

## Communication checks

| Station | Link verified | Evidence | Result |
| --- | --- | --- | --- |
| ESP32 A — Production | USB serial on COM8, configured for 115200 baud | Windows listed the CP210x USB-to-serial adapter. The gateway reported ESP32 A Online and recorded 10 `PART_PRODUCED` reports plus one `PRODUCTION_FINISHED` report over USB for this batch. | Pass |
| ESP32 B — Inspection | Wi-Fi TCP from `10.181.17.232` to gateway `10.181.17.167:5001` | Windows showed an established TCP connection. The gateway reported ESP32 B Online and recorded 10 `INSPECTION_RESULT` reports over TCP. | Pass |

At the connectivity check, both stations were Online and the gateway was Idle. Aya's USB cable supplied power. Her inspection traffic traveled over Wi-Fi.

## Batch results

The applied production cycle was **500 ms**, and the inspection time was **1000 ms**. The batch produced **10/10** parts and inspected **10/10**. Aya's ESP32 returned **6 PASS** and **4 FAIL**.

With **N = 3** and **K = 0**, a part fails when its Part ID is divisible by 3. The four rejected IDs were **42, 45, 48, and 51**. Every recorded verdict matched the rule.

| Part ID | Inspection job ID | Verdict |
| ---: | ---: | --- |
| 42 | 42 | FAIL |
| 43 | 43 | PASS |
| 44 | 44 | PASS |
| 45 | 45 | FAIL |
| 46 | 46 | PASS |
| 47 | 47 | PASS |
| 48 | 48 | FAIL |
| 49 | 49 | PASS |
| 50 | 50 | PASS |
| 51 | 51 | FAIL |

## Example matched exchange

Part **42** was produced under permit command **2251**, then inspected under job **42**. These are the gateway's stored event IDs and stages.

| Gateway event ID | Link | Message | Stage |
| ---: | --- | --- | --- |
| 1282 | USB | `PRODUCTION_PERMIT` | SENT |
| 1283 | USB | `DELIVERY_ACK` | DELIVERY_ACK |
| 1284 | USB | `ACCEPTED` | ACCEPTED |
| 1287 | USB | `PART_PRODUCED` | COMPLETED |
| 1290 | TCP | `INSPECT_PART` | SENT |
| 1298 | TCP | `DELIVERY_ACK` | DELIVERY_ACK |
| 1299 | TCP | `ACCEPTED` | ACCEPTED |
| 1322 | TCP | `INSPECTION_RESULT` | COMPLETED |

## Evidence source

The report uses a read-only query of the integrated gateway's SQLite batch, part, job, and event records, plus live dashboard and Windows connection checks. The gateway event IDs above are SQLite references. The Wireshark capture was not inspected or attached to this report.
