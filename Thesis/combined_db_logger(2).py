
#!/usr/bin/env python3

import csv
import os
import re
import signal
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import serial
from scipy import signal as scipy_signal

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
SAUTER_PORT     = "COM20"
SAUTER_BAUDRATE = 2400
SAUTER_TIMEOUT  = 0.1

MEMS_PORT        = "COM16"
MEMS_BAUDRATE    = 115200
MEMS_SAMPLE_RATE = 16000

ADC_MIDPOINT = 2048.0
ADC_MAX = 2048.0  # for normalization

WINDOW_MS     = 500   # 0.5 seconds

FILE_SAVE_DIRECTORY = "data/"
STRFTIME = "%Y-%m-%dT%H:%M:%S.%f"

# ──────────────────────────────────────────────
# CALIBRATION (FILL AFTER DATA COLLECTION)
# ──────────────────────────────────────────────
USE_CALIBRATION = False

CAL_REF_RMS   = 1.0   # reference RMS (set after calibration)
CAL_SLOPE     = 1.0   # linear slope (a)
CAL_OFFSET_DB = 40.0   # linear offset (b)

run = False

# ──────────────────────────────────────────────
# SHARED MEMS STATE
# ──────────────────────────────────────────────
latest_mems_lock = threading.Lock()
latest_mems_data = {
    "timestamp": None,
    "dba": None
}

# ──────────────────────────────────────────────
# SIGNAL HANDLER
# ──────────────────────────────────────────────
def handle_sigint(sig, frame):
    global run
    print("\nStopping...")
    run = False

signal.signal(signal.SIGINT, handle_sigint)

# ──────────────────────────────────────────────
# SAUTER HELPERS
# ──────────────────────────────────────────────
def chkchksum(msg):
    return len(msg) > 2 and int(msg[-1]) == (sum(msg[:-1]) % 256)

def decode_msg(msg):
    m = re.match(b"^\x08\x04(.)\x0a\x0a(...)\x01$", msg[:-1])
    if not m:
        return None
    d = m.groups()
    val = d[1][0]*10 + d[1][1] + d[1][2]/10
    return float(val)

# ──────────────────────────────────────────────
# MEMS PROCESSING
# ──────────────────────────────────────────────
def design_a_weighting(fs):
    f1, f2, f3, f4 = 20.6, 107.7, 737.9, 12194.0
    nums = [(2*np.pi*f4)**2, 0, 0, 0, 0]
    dens = np.polymul([1, 4*np.pi*f4, (2*np.pi*f4)**2],
                      [1, 4*np.pi*f1, (2*np.pi*f1)**2])
    dens = np.polymul(np.polymul(dens, [1, 2*np.pi*f3]),
                      [1, 2*np.pi*f2])
    return scipy_signal.bilinear(nums, dens, fs)

def rms_db_calibrated(x):
    rms = np.sqrt(np.mean(x**2))
    rms = max(rms, 1e-12)

    if USE_CALIBRATION:
        db = 20 * np.log10(rms / CAL_REF_RMS)
        return CAL_SLOPE * db + CAL_OFFSET_DB
    else:
        return 20 * np.log10(rms)

# ──────────────────────────────────────────────
# MEMS THREAD
# ──────────────────────────────────────────────
def memsThread():
    global run

    b, a = design_a_weighting(MEMS_SAMPLE_RATE)

    try:
        ser = serial.Serial(MEMS_PORT, MEMS_BAUDRATE, timeout=1)
        time.sleep(2)
    except Exception as e:
        print(f"[mems] Failed: {e}")
        return

    window_samples = []
    window_start_ts = None

    while run:
        try:
            line = ser.readline().decode(errors="ignore").strip()
        except:
            continue

        parts = line.split(",")
        if len(parts) != 3:
            continue

        try:
            block_ts = int(parts[0])
            adc = int(parts[2])
        except:
            continue

        if window_start_ts is None:
            window_start_ts = block_ts
            window_wall_start = datetime.now()

        window_samples.append(adc)

        if (block_ts - window_start_ts) >= WINDOW_MS:

            arr = np.array(window_samples, dtype=np.float64)
            window_samples = []

            ts = window_wall_start.isoformat(timespec="milliseconds")

            window_start_ts += WINDOW_MS
            window_wall_start += timedelta(milliseconds=WINDOW_MS)

            # --- SIGNAL PROCESSING FIXES ---
            arr = arr - ADC_MIDPOINT
            arr = arr - np.mean(arr)

            # A-weighting
            weighted = scipy_signal.lfilter(b, a, arr)

            # dB calculation
            dba = rms_db_calibrated(weighted)

            # Clamp to realistic range
            dba = min(dba, 120)

            with latest_mems_lock:
                latest_mems_data["timestamp"] = ts
                latest_mems_data["dba"] = float(f"{dba:.2f}")

# ──────────────────────────────────────────────
# SAUTER THREAD
# ──────────────────────────────────────────────
def sauterThread():
    global run

    filename = "combined_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + ".csv"
    path = os.path.join(FILE_SAVE_DIRECTORY, filename)

    ser = serial.Serial(SAUTER_PORT, SAUTER_BAUDRATE, timeout=SAUTER_TIMEOUT)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
            "sauter_timestamp",
            "sauter_dba",
            "mems_timestamp",
            "mems_dba"
        ])

        while run:
            char = ser.read()

            if char == b"\x10":
                ser.write(b"\x20")
            else:
                continue

            msg = b""
            while True:
                c = ser.read()
                if not c:
                    break
                msg += c

            if not chkchksum(msg):
                continue

            val = decode_msg(msg)
            if val is None:
                continue

            ts = datetime.now().strftime(STRFTIME)

            with latest_mems_lock:
                mems_ts = latest_mems_data["timestamp"]
                mems_db = latest_mems_data["dba"]

            writer.writerow([
                ts,
                val,
                mems_ts,
                mems_db
            ])
            f.flush()

            print(f"[combined] {ts} | Sauter: {val} | MEMS: {mems_db}")

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    global run

    os.makedirs(FILE_SAVE_DIRECTORY, exist_ok=True)

    run = True

    t1 = threading.Thread(target=memsThread, daemon=True)
    t2 = threading.Thread(target=sauterThread, daemon=True)

    t1.start()
    t2.start()

    try:
        while run:
            time.sleep(0.5)
    finally:
        run = False
        t1.join()
        t2.join()

if __name__ == "__main__":
    main()

