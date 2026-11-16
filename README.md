# MCT431 Networks Project

Milestone 1 connects two ESP32 stations to a Python gateway with a separate operator interface.

| Folder | Contents | Owner |
| --- | --- | --- |
| `esp32_a_production/` | Production station firmware | Zeina |
| `esp32_b_inspection/` | Inspection station firmware | Aya |
| `gateway/core/`, `gateway/db/` | Scheduler, state, and SQLite | Fady |
| `gateway/transports/` | Gateway USB serial and TCP communication | Menna |
| `gateway/api/`, `operator_ui/` | HTTP, WebSocket, and dashboard | Bassem |
| `tests/`, `evidence/` | Mocks, integration tests, captures, logs, and results | Team |
| `docs/`, `config/` | Architecture, protocol, and shared configuration examples | Team |

Keep Wi-Fi passwords and other local settings in an untracked `.env` or outside the repo.

## Run the local ESP32 B handshake

From the repo root, start the gateway in one terminal:

```powershell
python -m gateway.transports.gateway_tcp
```

Run the mock in a second terminal:

```powershell
python -m tests.mocks.mock_esp32_b
```

The gateway prints delivery, acceptance, and completion for the same command, followed by `ESP32 B is ready`. Both programs use localhost and port 5001 by default.
