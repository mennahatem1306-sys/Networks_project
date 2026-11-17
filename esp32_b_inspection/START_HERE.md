# Aya's ESP32 B - start here

This is the Milestone 1 inspection station, built with native ESP-IDF in VS Code.
No board is needed to read, configure or compile the project.

## Before the board arrives

1. Open `Aya-Inspection-ESP-IDF.code-workspace` in VS Code.
2. Select the Espressif ESP-IDF extension and the installed v6.1.1 environment.
3. Read `EXPLANATION.md` and `TEAM_INTEGRATION.md`.
4. Run **ESP-IDF: Build your project**. Building does not flash a device.

The initial target is the classic `esp32`. When you receive the board, check its
chip model. For an ESP32-S3, C3 or another variant, select that exact target with
**ESP-IDF: Set Espressif Device Target**, then rebuild.

## When the board arrives

1. Connect it with a USB data cable and install its USB serial driver if needed.
2. Connect the gateway laptop and ESP32 to a reachable 2.4 GHz network.
3. Open **ESP-IDF: SDK Configuration Editor (menuconfig)** and find
   **Inspection Station**. Enter the actual SSID, password, gateway IPv4 address
   and port (team default: 5001). Rebuild after changing settings.
4. On the gateway laptop run `python tools/test_gateway.py` for the first check.
   The script listens on TCP 5001; stop it before running Menna's gateway.
5. Select the board's COM port in ESP-IDF, then Flash and Monitor at 115200 baud.
6. Follow `HARDWARE_CHECKLIST.md`. Keep the real logs and packet captures.

Inspection time, N and K come from gateway commands at runtime. They are not
Wi-Fi settings and do not need a firmware edit. Only the ESP32 computes verdicts.

## Files to share with the team

Share this complete project folder or the source ZIP, including the hidden
`.vscode` folder. `TEAM_INTEGRATION.md` describes the contract for Menna and Fady.
Do not share a configured `sdkconfig` containing your Wi-Fi password.

The JSON library is named ArduinoJson, but it is a standalone C++ library here.
The firmware uses ESP-IDF directly and does not use the Arduino framework.

## Scope and evidence

The PDFs describe the requirements and team contract. Their Arduino setup choice
has been replaced by ESP-IDF at your request; the network contract is retained.
This folder covers Aya's station and supporting tests, not the other members'
production firmware, dashboard or full gateway. Formal multi-protocol benchmarks
belong to Milestone 2.

Local checks do not establish successful Wi-Fi operation or a working team batch.
No board measurements, Wireshark captures or serial evidence are fabricated.
See `VALIDATION.md` for the actual verification status.
