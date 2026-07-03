#!/usr/bin/env python3
"""
serial_csv_logger.py

Companion script for device_firmware_standalone.ino.

The board cannot write a .csv file by itself (no SD card / filesystem on this
build), so this script runs on your computer, listens to the serial port, and
appends every classification result the board prints to a real .csv file.

Usage:
    pip install pyserial
    python serial_csv_logger.py --port /dev/ttyACM0 --baud 115200 --out waynescoffee.csv

On Windows, --port will look like "COM5" instead of "/dev/ttyACM0".
On macOS, it usually looks like "/dev/cu.usbmodemXXXX".

Behavior:
    - Only lines from the board prefixed with "CSV:" are treated as data rows.
    - The "CSV_HEADER:" line the board sends once at boot is used to write
      the column header into the .csv file the first time it's seen.
    - Everything else (the human-readable [INF]/[DB] log lines) is printed
      to your terminal for visibility, but NOT written to the .csv file.
    - The script appends, so you can stop/restart it without losing data.
    - A wall-clock timestamp is added as an extra column so you have a real
      date/time even though the board itself only knows millis() or a
      compile-time clock.
"""

import argparse
import csv
import datetime
import sys
import time

try:
    import serial
except ImportError:
    print("This script requires pyserial. Install it with:\n    pip install pyserial")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Log Edge Impulse classification CSV lines from serial to a .csv file.")
    parser.add_argument("--port", required=True, help="Serial port, e.g. /dev/ttyACM0 or COM5")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200, must match Serial.begin in the sketch)")
    parser.add_argument("--out", default="classifications.csv", help="Output CSV file path (default: classifications.csv)")
    args = parser.parse_args()

    header_written = False
    board_header_fields = None

    print(f"Opening {args.port} @ {args.baud} baud...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"Could not open serial port: {e}")
        sys.exit(1)

    # Give the board a moment after the port opens (many boards reset on connect)
    time.sleep(2)

    # Open in append mode so repeated runs don't overwrite previous data
    csv_file = open(args.out, "a", newline="")
    writer = csv.writer(csv_file)

    print(f"Logging classification rows to {args.out}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue

            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue

            if not line:
                continue

            if line.startswith("CSV_HEADER:"):
                board_header_fields = line[len("CSV_HEADER:"):].split(",")
                if not header_written:
                    writer.writerow(["timestamp"] + board_header_fields)
                    csv_file.flush()
                    header_written = True
                print(f"[header] {line}")
                continue

            if line.startswith("CSV:"):
                fields = line[len("CSV:"):].split(",")
                timestamp = datetime.datetime.now().isoformat(timespec="seconds")

                if not header_written:
                    # Fallback header if the board's header line was missed
                    fallback = ["seq", "millis", "hour", "minute", "second", "db_level", "category", "confidence"]
                    writer.writerow(["timestamp"] + fallback[: len(fields)])
                    header_written = True

                writer.writerow([timestamp] + fields)
                csv_file.flush()
                print(f"[logged] {timestamp},{line[len('CSV:'):]}")
                continue

            # Anything else is just the board's human-readable log — show it,
            # don't write it to the CSV.
            print(f"[board] {line}")

    except KeyboardInterrupt:
        print("\nStopping — closing serial port and CSV file.")
    finally:
        ser.close()
        csv_file.close()


if __name__ == "__main__":
    main()
