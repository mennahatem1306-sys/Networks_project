# ESP32 A serial protocol


Wire record before escaping:

```text
test_id:u16 | seq:u32 | payload_len:u16 | UTF-8 JSON | crc16:u16
```

All multi-byte fields are big-endian. Production `test_id=0`; JSON is at most
512 bytes. CRC-16/CCITT-FALSE covers the eight-byte header and JSON, excluding
the trailing CRC: polynomial `0x1021`, initial `0xFFFF`, no reflection, XOR-out
zero. `CRC("123456789")=0x29B1`.

SLIP: leading/trailing `C0`, escape `C0` as `DB DC`, escape `DB` as `DB DD`.
The maximum unescaped record is 522 bytes and maximum encoded frame is 1046.
ATR `seq` increments for every transmission, including retries. It is absent
from serial JSON. Parsing handles partial frames and consecutive frames and
resynchronizes at the next delimiter after invalid input. ROM boot text before
the first delimiter is ignored by the parser.

Every synchronized JSON message has `schema_version:1`, sender `device_id`,
`session_id`, and `msg_type`. Gateway sender ID is 0; this board's ID is 1.
`HELLO` is sessionless. Errors before a session exists are also sessionless.

### Startup sequence

```text
A  -> GW  HELLO(boot_id, device_state)
GW -> A   SYNC_SESSION(cmd_id, session_id, target_device_id=1, target_boot_id)
A  -> GW  DELIVERY_ACK(ref_msg_type=SYNC_SESSION, cmd_id)
A  -> GW  ACCEPTED(ref_msg_type=SYNC_SESSION, cmd_id)
A  -> GW  COMPLETED(ref_msg_type=SYNC_SESSION, cmd_id, result=READY)
GW -> A   SET_PRODUCTION_CONFIG(cmd_id, config_version, cycle_time_ms)
A  -> GW  DELIVERY_ACK, ACCEPTED, COMPLETED(result=CONFIG_APPLIED)
GW -> A   START_PRODUCTION(cmd_id, batch_id, target_parts)
A  -> GW  DELIVERY_ACK, ACCEPTED, COMPLETED(result=ARMED)
GW -> A   PRODUCTION_PERMIT(cmd_id, batch_id, part_id, config_version)
A  -> GW  DELIVERY_ACK, ACCEPTED
          [full configured cycle passes; communication continues]
A  -> GW  PART_PRODUCED(original IDs, result=PRODUCED)
GW -> A   DELIVERY_ACK(ref_msg_type=PART_PRODUCED, original permit cmd_id)
```

Send gateway heartbeat messages once per second, also during configuration,
pauses and long cycles. Fields: `heartbeat_seq` (increasing uint32) and
`device_state` using the handoff's local state enum
`SAFE_IDLE/ARMED/BUSY/FAULT`. Mapping a gateway's global `RUNNING/PAUSED/IDLE`
to this heartbeat field belongs in the gateway adapter; do not send those
global enum values as `device_state`. A gateway `device_state=FAULT` makes A
abandon work safely.

### Result confirmations and retry behavior

Per handoff section 9.1, send a positive result-receipt acknowledgement **after
the gateway has safely recorded the result**. This firmware accepts either
`DELIVERY_ACK` or `ACCEPTED` with the original `cmd_id` and exact
`ref_msg_type=PART_PRODUCED` (or `PRODUCTION_FINISHED`). It never acknowledges
an acknowledgement. A result rejection triggers a local fault.

Malformed acknowledgements/rejections are validated before affecting pending
reports. Missing rejection fields or wrong-type text fields produce an ERROR
and leave the report pending. A local transmission failure cancels unfinished
work and pending controls; it cannot continue a terminal-message sequence after
the session has faulted.

Each report is transmitted once and retried at one-second intervals up to
three times. Retries have identical work IDs and a new ATR sequence. With no
confirmation after four total transmissions and another timeout, A faults.
The gateway can independently resend the original permit to obtain cached
acceptance/result without creating another part. There are no invented
`GET_STATUS` or inspection messages in this implementation.

The gateway must acknowledge `PRODUCTION_FINISHED` separately, using the Start
command's ID and `ref_msg_type=PRODUCTION_FINISHED`. Both terminal reports may
be pending at the same time. A new batch is refused until pending reports are
confirmed. Already-completed permit replies remain replayable within a live
session; the gateway must handle repeated final reports without double count.

### Safety and resource boundaries

- A only permits one active production timer and one unconfirmed part report.
- Pause/Resume/Stop also work after A finishes producing while B may still be
  inspecting. The gateway remains responsible for the global four-state
  machine, final completion and operator Fault reset.
- A genuinely new SYNC_SESSION cancels old timers and pending reports, discards
  old configuration and returns local SAFE_IDLE. It does not resume a batch.
  Gateway Fault reset and reconfiguration must precede new production.
- Previously used session IDs are refused. Up to 64 retired session IDs are
  remembered per boot, with no silent eviction or assumption about numeric
  session ordering.
- The cache retains up to 1536 well-formed commands per session, including up to
  1000 permits plus control/configuration commands. No accepted IDs are evicted.
  `CACHE_FULL` refuses new commands; synchronize a fresh session at a safe
  boundary. More than 64 retired sessions requires a safe board reboot.
- Configuration versions cannot decrease or change their meaning within a
  session. Reapplying the current version with the same duration is harmless.
- uint32 transport/heartbeat counters never silently wrap. Production timers
  use unsigned elapsed-time arithmetic and handle `millis()` wraparound.
- The flat JSON profile allows at most 20 fields, 31-byte decoded keys and
  95-byte decoded strings. Numbers used by commands must be integer literals
  fitting uint32, rather than strings, booleans, decimals or exponent notation.
  Nested objects, arrays, embedded NUL and unknown fields are rejected.

`CACHE_FULL`, `BOOT_COUNTER_ERROR`, `SEQUENCE_EXHAUSTED`, `TX_QUEUE_FULL`, and
`RESULT_REJECTED` are descriptive implementation diagnostics beyond the
handoff's standard error-code examples. All message names and required
production fields remain those of the handoff. These boundary behaviors should
be checked with Menna during pair integration rather than independently changed.
