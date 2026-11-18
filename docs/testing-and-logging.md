# Testing and logging

## Software checks

Run `python -m unittest discover -s tests/gateway` from the repository root to exercise scheduling, lifecycle rules, storage, and migration.

To test the standalone TCP handshake, run `python -m gateway.transports.gateway_tcp` in one terminal and `python -m tests.mocks.mock_esp32_b` in another. Both use localhost port 5001. Run this demo separately from the integrated gateway.

To preview the dashboard with simulated data, change into `examples/dashboard_mock`, install its `requirements.txt`, and run `python -m uvicorn main:app --host 127.0.0.1 --port 8000`. Open `http://127.0.0.1:8000`.

See [ESP32 A hardware testing](hardware-testing.md) and [ESP32 B validation](../esp32_b_inspection/VALIDATION.md) for station-specific tests.

## Collect hardware evidence

- Capture the ESP32 B connection on the gateway's Wi-Fi interface using `tcp port 5001` as a Wireshark capture filter.
- Set `MCT431_LOG_RAW_USB=1` before starting the gateway and save its application log. Use `python tools/watch_usb_atr.py <gateway-log>` from the repository root to decode the timestamped SLIP bytes. It reports the ATR sequence, payload length, and both CRC values.
- Use `python tools/watch_gateway_events.py` to follow new SQLite events without changing the database. To read ESP32 B diagnostics, run `python tools/capture_inspection_uart.py <port> [seconds]` with the board's own COM port.

## Controlled reliability tests

Run the helpers in `tests/integration/` from the repository root on the test setup. Set `MCT431_INSPECTION_HOST` to the test gateway's Wi-Fi address, `MCT431_SERIAL_PORT` to ESP32 A's port, and `MCT431_DATABASE` to the test database. Stop the normal gateway before using a harness and restore it afterward.

- ESP32 B's CMake options `MCT431_LOST_ACK_TEST`, `MCT431_RETRY_EXHAUSTION_TEST`, and `MCT431_DUPLICATE_TEST` are off by default. The one-shot lost-ACK and exhaustion modes select the first eligible job with 2000 ms or 5000 ms inspection respectively, N = 3, and K = 0.
- Run `python tests/integration/run_duplicate_test_gateway.py` with the duplicate-test firmware to replay one accepted inspection request and record counters around the response. The station replays that result after receiving its first receipt.
- Run `python tests/integration/run_validation_gateway.py <evidence-directory>` for the recorded validation scenario. The directory must contain `Original-Stale-Frame.hex` from the [validation evidence](../evidence/requirement-05-validation/). With both devices ready and the gateway idle, place A, B, or C in `request.txt` in that directory to trigger the respective check. This harness uses the recorded milestone batch and part identifiers for its invalid inputs, and preserves the historical result's original IDs for the stale replay. Its local proxy also uses TCP port 15056 for that check.
