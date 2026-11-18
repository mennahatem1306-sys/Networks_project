# MCT431 Milestone 1 — Test 2
## ESP32 A USB serial communication

**Date:** 17 November 2026

**Link:** Gateway ↔ Zeina's ESP32 A, COM8 at 115200 baud

**Batch:** 12 · **Configuration version:** 17 · **Result:** PASS for the captured USB exchange


The Operator PC requested **3 parts** with a **1000 ms production cycle** and a
**2000 ms inspection time**, using **N = 3** and **K = 0**. The gateway recorded
the batch as Completed with **3 produced, 3 inspected, 2 accepted, and 1 rejected**.
The produced part IDs were **58, 59, and 60**. Part 60 failed Aya's inspection rule.

### Match the production request to the report

For each part, the raw USB trace includes a `PRODUCTION_PERMIT` from the
gateway and a `PART_PRODUCED` response from ESP32 A using the same `cmd_id`,
`part_id`, `batch_id`, and configuration version. The trace also contains one
`PRODUCTION_FINISHED` report. Sequences progressed in each direction.

| Part ID | Production permit command ID | Inspection verdict |
| ---: | ---: | --- |
| 58 | 2301 | PASS |
| 59 | 2302 | PASS |
| 60 | 2303 | FAIL |

### Compare SLIP, ATR, and CRC

Find command **2301**, part **58**, in the two accompanying logs. The raw log
shows the wire bytes beginning and ending with `C0`. The decoded log shows the
eight-byte ATR header, payload length, and CRC after removing SLIP delimiters and
reversing byte escapes. ATR records contain **8 header bytes + JSON bytes + 2 CRC bytes**.

| Direction and message | 8-byte ATR header | Sequence | JSON bytes | Record bytes | CRC received | CRC recalculated |
| --- | --- | ---: | ---: | ---: | --- | --- |
| TX `PRODUCTION_PERMIT` | `00 00 00 00 01 24 00 90` | 292 | 144 | 154 | `0xC066` | `0xC066` |
| RX `PART_PRODUCED` | `00 00 00 00 01 36 00 A0` | 310 | 160 | 170 | `0xF603` | `0xF603` |

In the raw permit frame, the final bytes are `DB DC 66 C0`. The escape
`DB DC` restores the first CRC byte `C0`, so the decoded CRC is `C0 66`.
The last `C0` is the SLIP end marker.

All **21** selected batch frames reassembled from the live serial log
had valid SLIP boundaries, matching ATR lengths, and matching CRC values.

### Controlled checks

- CRC-16/CCITT-FALSE returned `0x29B1` for the standard input `123456789`.
- Flipping one bit in the CRC of a copied `PART_PRODUCED` record caused the
  gateway decoder to reject it with `ATR CRC mismatch`.
- A synthetic record with reserved bytes `C0` and `DB` used SLIP escapes
  `DB DC` and `DB DD` and decoded back to the original record.

The corrupted-copy and escaping checks ran offline. No deliberately corrupted
frame was sent to the physical ESP32.

### Evidence

- `T02_USB_Raw.log` contains the selected raw frames reassembled from the
  gateway's timestamped COM8 reads and writes.
- `T02_USB_Decoded.log` contains their decoded ATR fields, CRCs, and JSON.
