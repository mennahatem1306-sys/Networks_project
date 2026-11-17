# Aya's Milestone 1 implementation map

Scope: handoff v1.1, section 13.2 (Aya), and the shared TCP/session contract.
The course brief defines the deliverable. The handoff supplies team choices such
as TCP port 5001 and the message names. The user's requested ESP-IDF toolchain
replaces the handoff's Arduino setup.

| Requirement | Implementation / verification |
|---|---|
| Wi-Fi TCP client, reconnect backoff | `main/platform.cpp`, `stationLoop()` |
| Four-byte big-endian length and fragmented/coalesced frames | `sendMessage()`, `receiveFrames()` |
| One inspection at a time, non-blocking timer | `active`, `stationLoop()`, `main/logic.h` |
| Runtime N/K/time validation and atomic configuration | `SET_INSPECTION_CONFIG` branch in `handleMessage()` |
| Immutable configuration for active jobs | `Job` snapshot in `main/station_types.h` |
| Overflow-safe deterministic verdict computed on B | `inspectionFails()` in `main/logic.h` |
| Delivery, acceptance and completion separately | `sendResponse()`, `sendInspectionResult()` |
| Preserve job, batch, part and configuration IDs | `INSPECT_PART` validation and `sendInspectionResult()` |
| Duplicate suppression and cached result replay | `jobs[]` and `commands[]` caches in `handleMessage()` |
| Reject conflicting reused IDs | `DUPLICATE_ID_CONFLICT` branches |
| Bounded result retries | `Protocol::MaxResultAttempts`, result retry loop |
| Boot identity, handshake and stale session rejection | NVS in `platform.cpp`, HELLO/SYNC_SESSION branches |
| Heartbeats during inspection and safe disconnection | heartbeat handling, `disconnectSafe()` |
| Repeatable tests | `tests/logic_compile_checks.cpp`, `tools/test_tools.py`, `tools/test_gateway.py` |
| Setup, diagrams and message integration | README, ARCHITECTURE, TEAM_INTEGRATION, START_HERE |

The gateway owns global Idle/Running/Paused/Fault, queue capacity, result-age
checking on its own monotonic clock, durable IDs, database counters and reset
authorization. Those are integration dependencies, not station-side features.

Real packet captures, serial logs, timing observations and demonstration evidence
must be collected with the board and the team's gateway. The included hardware
test script is a test peer, not a replacement for the team's gateway application.
