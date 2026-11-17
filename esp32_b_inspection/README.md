# Aya - ESP32 B inspection station

Native ESP-IDF C++ project for VS Code's Espressif ESP-IDF extension.
Team contract: MCT431 Milestone 1, handoff v1.1, 14 November 2026.

Start with **START_HERE.md** for the short preparation and first-flash guide.
**REQUIREMENTS.md** maps Aya's responsibilities to the implementation.

In the team repository, open this folder's workspace directly. Its build directory
is `build/` inside this folder. Select your own installed ESP-IDF environment.
The existing `gateway/transports/gateway_tcp.py` is a handshake-only demonstration;
use `tools/test_gateway.py` here for station tests until the full gateway is integrated.

## Open and build in VS Code

1. Open `Aya-Inspection-ESP-IDF.code-workspace`.
2. Use the installed **Espressif ESP-IDF** extension. Select the installed ESP-IDF v6.1.1 environment if asked.
3. Run **ESP-IDF: Set Espressif Device Target** from the Command Palette. The initial build
   target is `esp32`, for the classic ESP32. Confirm the physical board model before flashing.
4. Run **ESP-IDF: SDK Configuration Editor (menuconfig)**. Under **Inspection Station**, set
   the Wi-Fi SSID, password, gateway numeric IPv4 address and TCP port (5001).
5. Use **ESP-IDF: Build your project**, or press **Ctrl+Shift+B**.
6. When the board is available, choose its real serial port with the ESP-IDF status bar and
   use **ESP-IDF: Flash your project** followed by **ESP-IDF: Monitor your device**.
   Monitor baud rate is 115200. Close the monitor before another tool uses the same COM port.

The project uses ESP-IDF Wi-Fi, lwIP sockets, NVS, esp_timer and FreeRTOS directly.
ArduinoJson is a standalone C++ JSON dependency installed by the ESP component manager;
the Arduino framework and Arduino IDE are not required for this project.

## Main files

| File | Purpose |
|---|---|
| main/inspection_station.cpp | Message validation, session handling, configuration, job execution and retries |
| main/platform.cpp | Native Wi-Fi, TCP sockets, persistent boot counter and clock |
| main/platform.h | Platform function and TCP connection declarations |
| main/station_types.h | Job and configuration-command cache records |
| main/protocol.h | Limits and timing constants |
| main/logic.h | Overflow-safe verdict and elapsed-time calculations |
| main/Kconfig.projbuild | Project settings shown in ESP-IDF menuconfig |
| main/config.h | Typed access to the selected project settings |
| main/idf_component.yml | ESP-IDF constraint and pinned JSON dependency |
| CMakeLists.txt / main/CMakeLists.txt | Native ESP-IDF build definition |
| sdkconfig.defaults | Default FreeRTOS tick, main-task stack and flash size |
| EXPLANATION.md | Detailed code walkthrough and discussion preparation |
| TEAM_INTEGRATION.md | Wire messages and gateway responsibilities |
| HARDWARE_CHECKLIST.md | Real-board verification checklist |
| ARCHITECTURE.md | Team topology and station processing diagrams |
| tests/logic_compile_checks.cpp | Twelve compile-time arithmetic and rollover checks |

## Test tools

In a VS Code terminal, from this folder:

```powershell
python -m unittest discover -s tools -p test_tools.py -v
python tools/test_gateway.py
```

The second command waits for a real ESP32. Set its gateway address to this laptop's
reachable LAN IPv4 address. Use a 2.4 GHz network supported by the selected board.
Allow TCP 5001 on the private team network if Windows asks. Stop the test peer before
starting Menna's real gateway on that port.

Separate fault-test runs:

```powershell
python tools/test_gateway.py --scenario heartbeat-loss --log heartbeat_loss.jsonl
python tools/test_gateway.py --scenario result-retries --log result_retries.jsonl
python tools/export_trace.py gateway_trace.jsonl gateway_trace.csv
```

Reset/reconnect the board for each test run. CSV export preserves real event transmissions,
including duplicates; it does not generate sample measurements or production counts.

## Behavior

- TCP client, four-byte big-endian JSON length prefix, maximum JSON body 1024 bytes.
- HELLO -> SYNC_SESSION -> DELIVERY_ACK -> ACCEPTED -> COMPLETED(READY).
- Runtime inspection time 1..3,600,000 ms, positive signed-32-bit N, signed-32-bit K.
- FAIL iff `(int64_t(part_id) + K) % N == 0`; otherwise PASS, computed on B.
- One active job, immutable per-job configuration snapshot, no blocking inspection wait.
- Duplicate requests replay status or results. Changed content under an accepted ID is rejected.
- Heartbeat every second; missing valid gateway heartbeat for three seconds disconnects safely.
- Result delivery: initial report plus three retries, one second apart, then safe disconnection.
- Persistent NVS boot counter; fresh gateway session after every connection; no old-job auto-resume.
- 1000 accepted jobs and 256 config commands per session; no cache eviction. New work is rejected
  at capacity. Establish a new session only after the gateway has safely settled/aborted work.

## Integration details

The gateway must send correlated ACCEPTED or DELIVERY_ACK for INSPECTION_RESULT only after
safe recording. Either is treated as a final positive receipt by this station. Use
ref_msg_type=INSPECTION_RESULT and the original job_id. No acknowledgement of an ACK.

The gateway owns batch state, queue management, durable unique IDs, result-age detection,
late reconciliation, and operator fault reset. An interrupted batch must be aborted and
old-session reports quarantined. B has no separate pause command in the handoff: the
gateway pauses dispatch while an already accepted inspection finishes.

## Verification status

See VALIDATION.md for the actual compiler and test results. Physical upload, Wi-Fi timing,
fault recovery and team integration remain pending until an ESP32 is available.
The 4 MB flash default is a baseline for common development boards; confirm real hardware.

Official references:

- https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/windows-setup.html
- https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/lwip.html
- https://components.espressif.com/components/bblanchon/arduinojson/versions/7.4.3/readme
