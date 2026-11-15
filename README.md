# MCT431 ESP32 A Production Station

Zeina's Arduino firmware for the Milestone 1 production station. It produces
virtual parts only after gateway permits and communicates over USB serial using
SLIP, ATR and CRC-16/CCITT-FALSE.

The gateway, inspection station and operator dashboard belong to the other team
modules. Hardware upload and live gateway integration are still pending.

## Arduino IDE

1. Open [esp32_a_production.ino](esp32_a_production/esp32_a_production.ino).
2. Install **esp32 by Espressif Systems**, version **3.3.3**.
3. Select the actual board. The verified compile target is **ESP32 Dev Module**
   (`esp32:esp32:esp32`); native-USB boards need their own serial settings.
4. Set **Core Debug Level: None** and click **Verify**.
5. Select the board's COM port before uploading for hardware tests.

Keep the `.ino`, `.h` and `.cpp` files together. No additional Arduino library or
separate ESP-IDF installation is required. The serial link is 115200 baud, 8N1.

## Layout

```text
esp32_a_production/  Arduino sketch and production/protocol C++ sources
config/              Board and runtime-message examples
tests/esp32_a/       Native C++ driver, Python tests and trace export
tools/              Verification command
docs/               Serial protocol and hardware testing sequence
```

## Software verification

Requirements: Python 3.10+, Arduino CLI, ESP32 core 3.3.3 and a C++17 compiler
(Clang++, G++, or Zig). Python checks use only the standard library.

From the repository root:

```sh
python tools/check_zeina.py
```

Pass executable paths if they are not on `PATH`:

```sh
python tools/check_zeina.py --cxx /path/to/zig --arduino-cli /path/to/arduino-cli
```

On Windows, the script also detects Arduino IDE's bundled CLI. A custom board
can be compiled with `--fqbn`; `--build-dir` changes the output directory.
Generated binaries go to `build/` and reports to `evidence/zeina/software/`.
Both directories are ignored by Git.

The software suite contains 40 tests. It covers framing, CRC, validation,
permits, duplicate suppression, retries, control commands, configuration
snapshots, sessions, heartbeat loss, transmit failures and capacity limits.
These checks do not replace physical USB/NVS/gateway testing.

## Runtime sequence

```text
HELLO -> SYNC_SESSION -> SET_PRODUCTION_CONFIG -> START_PRODUCTION
      -> PRODUCTION_PERMIT -> PART_PRODUCED -> report confirmation
```

Start only arms the station. Each part requires a separate permit. The gateway
must send fresh heartbeats every second and acknowledge reports after recording
them. `PRODUCTION_FINISHED` describes production only, not inspection completion.
Binary SLIP/ATR framing is required; plain JSON typed in Serial Monitor is not a
valid command.

## Documentation

- [Serial protocol and reliability](docs/protocol.md)
- [Hardware testing sequence](docs/hardware-testing.md)

## References

- [ESP32 installation](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html)
- [Preferences / NVS](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/preferences.html)
- [Serial / UART](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/serial.html)
