# Verification status

Checked on 17 November 2026. No ESP32 board was available.

## Passed locally

- ESP-IDF v6.1.1 build for the classic `esp32` target completed successfully.
  The toolchain generated `aya_inspection_station.bin` (817,072 bytes), the
  bootloader and partition table in `C:/Espressif/builds/mct431_aya_inspection`.
- The build compiled `inspection_station.cpp`, `platform.cpp` and all twelve
  `static_assert` verdict/timing checks in `tests/logic_compile_checks.cpp`.
  Station sources use `-Wall -Wextra -Werror`.
- Link-time size check: application partition has 231,504 bytes (about 22%) free.
  Static DRAM use is 87,855 bytes and IRAM use is 87,719 bytes. See `size_report.txt`.
  These figures do not establish runtime free heap or physical timing.
- Seven Python unit tests passed: fragmented/coalesced messages, invalid lengths,
  truncated frames, payload size limits, unmatched-message preservation, timeout
  handling and CSV identifier/unit preservation.
- Python helper syntax and VS Code JSON configuration checks passed.

Reproduce the Python tests from this folder:

```powershell
python -m unittest discover -s tools -p test_tools.py -v
```

Reproduce the firmware build using the selected ESP-IDF terminal:

```powershell
idf.py build
```

The initial build uses placeholder Wi-Fi credentials. Set the actual network
configuration and confirm the chip target before rebuilding and flashing.
The source ZIP excludes local `sdkconfig`, generated binaries and build output.

## Pending hardware and team integration

Flashing, Wi-Fi connectivity, serial output, real timing, fault recovery,
heap/latency observations, gateway interoperability, Wireshark captures and
full-batch demonstration have not been tested. The Python tests validate the PC
helpers; they do not execute the ESP32 firmware. Run the hardware peer scenarios
and `HARDWARE_CHECKLIST.md` when a board is available.

The SDK emitted upstream Kconfig deprecation notices during configuration.
They did not prevent the successful build. An early overlapping invocation of
the same build directory failed while another build was active; the completed
build log ends with `Project build complete`. Run only one build per directory.
