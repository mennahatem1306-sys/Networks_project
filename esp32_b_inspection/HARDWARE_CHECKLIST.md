# Board verification and evidence

Record board model, ESP-IDF version, JSON-library version and compiler output.
Keep a real serial log and Wireshark capture (`tcp.port == 5001`); never fabricate results.

- [ ] Verify compilation, upload and HELLO. Reset twice: boot_id must increase.
- [ ] Run test_gateway.py; retain its actual PASS output and JSONL log.
- [ ] N=3 K=1: part 2 FAIL, part 3 PASS. N=1: every valid part FAIL.
- [ ] Negative K and uint32 maximum part ID; invalid N=0 rejected without changing config.
- [ ] Inspection time 1 second, 5 seconds, then a long examiner-style duration:
      verify continuous one-second heartbeats during the entire wait.
- [ ] Live config update: active job retains old duration/rule/version; next job uses new values.
- [ ] Identical jobs during/after execution: timer never restarts; result and elapsed time match.
- [ ] Existing job ID with changed batch/part/version: DUPLICATE_ID_CONFLICT.
- [ ] Old-session job: INVALID_SESSION; no execution. Unknown type and wrong primitive types rejected.
- [ ] Partial prefix/body, consecutive frames, zero/1025-byte lengths, malformed JSON.
- [ ] Withhold result acknowledgement: four transmissions, then safe disconnection.
- [ ] Stop gateway heartbeat while TCP remains open: safe disconnect after three seconds.
- [ ] Disconnect mid-job: no result in a new session; gateway aborts old batch.
- [ ] Reconnect handshake with fresh session; old commands/results never count in new batch.
- [ ] Fill cache to 1000 jobs: reject next job without eviction or duplicate execution.
- [ ] Measure free heap and loop/write latency under load; record any threshold changes with team.
- [ ] Team integration: gateway captures result age and durably commits before receipt confirmation.

Explain orally: TCP delivery is not acceptance or completion; the board computes the verdict;
immutable snapshots keep live rule changes deterministic; identifiers make retries safe;
heartbeats and sessions prevent uncertain old work from silently resuming.
