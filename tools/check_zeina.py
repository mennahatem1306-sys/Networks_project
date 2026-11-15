"""Compile the firmware, run native tests, and export software verification."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arduino-cli", type=Path)
    parser.add_argument("--cxx", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--fqbn", default="esp32:esp32:esp32")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    build_dir = args.build_dir.resolve() if args.build_dir else root / "build"
    evidence_dir = root / "evidence/zeina/software"

    arduino = args.arduino_cli or shutil.which("arduino-cli")
    if not arduino and os.name == "nt":
        arduino = Path(os.environ.get("LOCALAPPDATA", "")) / (
            "Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"
        )
    if not arduino or not Path(arduino).is_file():
        parser.error("Arduino CLI not found; pass --arduino-cli with its executable path.")

    compiler = str(args.cxx) if args.cxx else os.environ.get("CXX")
    if not compiler:
        compiler = next(
            (path for name in ("clang++", "g++", "c++", "zig") if (path := shutil.which(name))),
            None,
        )
    if compiler:
        compiler = shutil.which(compiler) or compiler
    if not compiler or not Path(compiler).is_file():
        parser.error("Native C++ compiler not found; pass --cxx with clang++, g++, or Zig.")

    build_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    executable = build_dir / ("esp32_a_host_tests.exe" if os.name == "nt" else "esp32_a_host_tests")
    compiler_args = ["c++"] if Path(compiler).stem.lower() == "zig" else []
    compiler_args += [
        "-std=c++17",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I",
        "esp32_a_production",
        "tests/esp32_a/host_driver.cpp",
        "esp32_a_production/SerialProtocol.cpp",
        "esp32_a_production/ProductionStation.cpp",
        "-o",
        executable,
    ]
    checks = [
        ([compiler, *compiler_args], "native-build.log"),
        (
            [
                sys.executable,
                "tests/esp32_a/test_firmware.py",
                "--executable",
                executable,
                "--report",
                evidence_dir / "test-results.json",
            ],
            "test-results.log",
        ),
        (
            [
                sys.executable,
                "tests/esp32_a/export_trace.py",
                "--executable",
                executable,
                "--output",
                evidence_dir / "serial-trace.jsonl",
            ],
            "trace-export.log",
        ),
        (
            [
                arduino,
                "compile",
                "--fqbn",
                args.fqbn,
                "--build-path",
                build_dir / "arduino",
                "--warnings",
                "all",
                "esp32_a_production",
            ],
            "arduino-build.log",
        ),
        ([arduino, "version"], "arduino-cli-version.log"),
    ]
    for command, log_name in checks:
        print(f"Running {log_name.removesuffix('.log')}...", flush=True)
        result = subprocess.run(
            [str(argument) for argument in command],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
        (evidence_dir / log_name).write_text(result.stdout, encoding="utf-8")
        print(result.stdout, end="", flush=True)
        result.check_returncode()

    sources = sorted((root / "esp32_a_production").glob("*"))
    manifest = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "hardware_test": False,
        "arduino_cli": (evidence_dir / "arduino-cli-version.log").read_text().strip(),
        "fqbn": args.fqbn,
        "python": sys.version,
        "source_sha256": {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
            if path.suffix in (".ino", ".h", ".cpp")
        },
    }
    (evidence_dir / "source-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print("Software checks passed. No upload or physical USB test was performed.", flush=True)


if __name__ == "__main__":
    main()
