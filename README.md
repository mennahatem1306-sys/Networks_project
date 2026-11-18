# MCT431 Networks Project

Milestone 1 connects an ESP32 production station and an ESP32 inspection station to a Python gateway. The gateway serves the dashboard and records commands, results, and batch state in SQLite.

| Folder | Contents |
| --- | --- |
| [esp32_a_production](esp32_a_production/) | Zeina's production firmware. USB serial at 115200 baud. |
| [esp32_b_inspection](esp32_b_inspection/) | Aya's inspection firmware. Wi-Fi TCP on port 5001. |
| [gateway](gateway/) | Fady's scheduler and SQLite core, Menna's USB/TCP transports, and the FastAPI server. |
| [operator_ui](operator_ui/) | Bassem's integrated operator dashboard. |
| [tests](tests/) and [tools](tools/) | Automated checks, mocks, and evidence capture tools. |
| [evidence](evidence/) | Milestone 1 reports, Wireshark captures, serial logs, and SQLite exports. |
| [docs](docs/) and [config](config/) | Protocol notes, hardware setup, and configuration examples. |

## Start the system

1. Open `esp32_a_production/esp32_a_production.ino` in Arduino IDE, select the ESP32 board and its USB COM port, and flash it. Close Arduino Serial Monitor afterward.
2. Open [ESP32 B's setup instructions](esp32_b_inspection/README.md). In ESP-IDF, set the local Wi-Fi details and the gateway laptop's IPv4 address before flashing.
3. Run the following from the repository root on the gateway laptop. Replace the example COM port and IP with your own values.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r gateway/requirements.txt
$env:MCT431_SERIAL_PORT = "COM8"
$env:MCT431_INSPECTION_HOST = "192.168.137.1"
.\.venv\Scripts\python.exe -m uvicorn gateway.main:app --host $env:MCT431_INSPECTION_HOST --port 8001
```

4. Connect the operator laptop to the same network and open the gateway address on port 8001, for example `http://192.168.137.1:8001`. Wait for both boards to show **Online**, apply the batch settings, and press **Start** on the dashboard.

The gateway keeps its SQLite database in `%LOCALAPPDATA%/MCT431/team-gateway.sqlite3` by default. Set `MCT431_DATABASE` to use another local path. Run only one gateway per hardware setup so it can own the serial port and TCP listener. Keep Wi-Fi credentials in the local ESP-IDF `sdkconfig`.

## Verify the setup

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/gateway
```

For a hardware run, use 10 parts, a 1000 ms production cycle, a 2000 ms inspection time, N = 3, and K = 0. The completed batch should show ten produced and ten inspected. The actual pass/fail split depends on the assigned part IDs. [Batch 35's annotated trace](evidence/requirement-06-normal-run/R06-Batch35-Verification.md) links one part through both stations.

See the [test and logging notes](docs/testing-and-logging.md) for the TCP mock, standalone dashboard preview, and diagnostic tools.
