"""Check the standalone runtime without opening any project database."""
import sqlite3
import sys


def main():
    print(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"SQLite {sqlite3.sqlite_version}")
    if sys.version_info < (3, 11):
        raise SystemExit("Use Python 3.11 or newer.")
    print("Standalone Gateway environment ready.")


if __name__ == "__main__":
    main()
