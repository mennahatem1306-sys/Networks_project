# Inspection station integration contract

B is a TCP client connecting to the gateway's LAN address on port 5001.
Every JSON body is preceded by its four-byte big-endian UTF-8 byte length, excluding
the prefix. Length range: 1..1024. All fields below are flat JSON.

These example IDs illustrate one connection; the production gateway must allocate its own
fresh persistent identifiers. Every transmission, including a retry, gets the sender's next seq.

## 1. Receive B's HELLO

```json
{"schema_version":1,"msg_type":"HELLO","device_id":2,"seq":0,"boot_id":42,"device_state":"SAFE_IDLE"}
```

## 2. Synchronize the current boot

```json
{"schema_version":1,"msg_type":"SYNC_SESSION","device_id":0,"seq":0,"session_id":9,"cmd_id":100,"target_device_id":2,"target_boot_id":42}
```

B returns DELIVERY_ACK, ACCEPTED, then COMPLETED(result=READY), each with device_id=2,
session_id=9, ref_msg_type=SYNC_SESSION, cmd_id=100 and its own increasing seq.
Do not send another SYNC_SESSION with different IDs on the same synchronized connection.

## 3. Exchange heartbeats once per second

```json
{"schema_version":1,"msg_type":"HEARTBEAT","device_id":0,"seq":1,"session_id":9,"heartbeat_seq":0,"device_state":"SAFE_IDLE"}
```

Increment heartbeat_seq for every heartbeat. Repeated/older values do not refresh B's liveness timer.
Heartbeat is not acknowledged. Three seconds without a valid gateway heartbeat closes TCP.
Neither long inspection time nor ordinary TCP traffic disables heartbeat supervision.

## 4. Apply inspection settings

```json
{"schema_version":1,"msg_type":"SET_INSPECTION_CONFIG","device_id":0,"seq":2,"session_id":9,"cmd_id":101,"config_version":1,"inspection_time_ms":2000,"rule_n":3,"rule_k":1}
```

Wait for COMPLETED(result=CONFIG_APPLIED), correlated by ref_msg_type and cmd_id, before
dispatching new-version jobs. This follows DELIVERY_ACK and ACCEPTED. Config versions
increase within a session. An active job remains on its own original settings snapshot.

## 5. Send one inspection job

```json
{"schema_version":1,"msg_type":"INSPECT_PART","device_id":0,"seq":3,"session_id":9,"batch_id":1,"job_id":27,"part_id":15,"config_version":1}
```

B returns DELIVERY_ACK then ACCEPTED, both referencing INSPECT_PART and job_id=27.
After the job's timer expires, it returns:

```json
{"schema_version":1,"msg_type":"INSPECTION_RESULT","device_id":2,"seq":8,"session_id":9,"batch_id":1,"job_id":27,"part_id":15,"config_version":1,"inspection_elapsed_ms":2001,"result":"PASS"}
```

Elapsed time and outgoing seq above are illustrative, not measured evidence. Only B
calculates the production verdict. The gateway must verify all identifying fields,
current session/batch, and its own monotonic result-age threshold before counting anything.

## 6. Durably record before confirming the result

```json
{"schema_version":1,"msg_type":"ACCEPTED","device_id":0,"seq":4,"session_id":9,"ref_msg_type":"INSPECTION_RESULT","job_id":27}
```

For interoperability, B also supports DELIVERY_ACK with the same correlation fields as
a final positive receipt. Either stops automatic result retries. Use one agreed receipt
convention; a transport-only ACK sent before recording must not be used for this purpose.

Duplicate job requests repeat their original data and ID. In-progress duplicates replay
ACCEPTED. Completed duplicates replay ACCEPTED and the original result, including its
original config_version and elapsed time. The gateway must count each job at most once.
Different batch_id, part_id or config_version under the same cached job_id is a conflict.

## Recovery and responsibilities

| Event | B | Gateway |
|---|---|---|
| Result receipt absent | Resend original result, maximum four automatic sends | Commit once, confirm duplicates safely |
| Completion timeout | Respond to same-ID recovery with existing status/result | Recover same job ID; never allocate replacement work silently |
| Current-session late result | Retain original cached data | Classify LATE_UNVERIFIED, optionally reconcile same-ID replay once |
| TCP loss / reset | Abandon active work; new handshake | FAULT/ABORTED; quarantine old-session data; require reset |
| Pause / graceful Stop | Finish already accepted inspection | Stop dispatching new jobs; wait for active work |
| Cache capacity reached | Reject new work; never evict IDs | Complete/settle batch; establish new safe session when appropriate |

The test gateway is a development fixture, not Fady's production controller. It does not
implement full batch accounting, stale-result reconciliation, HTTP/WebSocket or SQLite.
