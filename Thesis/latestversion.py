#!/usr/bin/env python3
"""
combined_db_logger.py
=====================
Simultaneously logs dB levels from two sources:
  1. Sauter SU130 sound level meter via RS-232 serial
  2. MEMS microphone via RP2040 microcontroller serial

Both sources are read in separate threads and written to their own CSV files,
with timestamps aligned so readings can be compared later.

Optionally saves audio clips when the Sauter reading exceeds a threshold.
"""

import argparse
import csv
import os
import re
import signal
import sys
import threading
import time
import wave
from datetime import datetime
from threading import Timer
from time import sleep

import numpy as np
import pyaudio
import serial
from scipy import signal as scipy_signal

# ──────────────────────────────────────────────
# Default configuration
# ──────────────────────────────────────────────
SAUTER_PORT        = "COM20"
SAUTER_BAUDRATE    = 2400
SAUTER_TIMEOUT     = 0.1

MEMS_PORT          = "COM16"
MEMS_BAUDRATE      = 115200
MEMS_SAMPLE_RATE   = 16000
MEMS_BLOCK_SIZE    = 256
MEMS_BLOCKS_WINDOW = 32          # ~0.51 s window per measurement
ADC_MIDPOINT       = 2048.0

FILE_SAVE_DIRECTORY = "data/"
STRFTIME            = "%Y-%m-%dT%H:%M:%S.%f"

SAVE_AUDIO           = False
LEVEL_THRESHOLD      = 80
AUDIO_HW_ID          = -1
CALIBRATION_OFFSET_DB = 0.0

sample_format = pyaudio.paInt16
channels      = 1
fs            = 44100
chunk         = 1024
audioDuration = 3

# ──────────────────────────────────────────────
# Shared state
# ──────────────────────────────────────────────
run         = False
savingFile  = False
savingTimer = None
buffer      = []

portAudio = None
stream    = None


# ──────────────────────────────────────────────
# Signal handling
# ──────────────────────────────────────────────
def handle_sigint(sig, frame):
    global run
    print("\n[main] Ctrl+C — stopping all threads...")
    run = False

signal.signal(signal.SIGINT, handle_sigint)


# ══════════════════════════════════════════════
# SAUTER helpers
# ══════════════════════════════════════════════

def subbits(byte, mask, r_shift):
    return (byte & mask) >> r_shift

def is_maxhold(ctrl):
    bits = subbits(ctrl, 0b00110000, 4)
    if bits == 0b10:
        return True
    if bits == 0b01:
        return False
    return None

def modetxt(ctrl):
    if subbits(ctrl, 0b00001100, 2) == 0b10:
        slowmode        = subbits(ctrl, 0b00000010, 1) == 0b1
        basedon_minutes = subbits(ctrl, 0b00000001, 0) == 0b1
        leq_mode        = True
    else:
        slowmode        = subbits(ctrl, 0b00000001, 0) == 0b1
        basedon_minutes = None
        leq_mode        = False

    non_leq_modes = {
        0b000: "Lp_(dB),Weighting_A",
        0b001: "Lp_(dB),Weighting_C",
        0b010: "Lp_(dB),Flat",
        0b011: "Ln_(%),Weighting_A",
        0b101: "Unknown",
        0b110: "Cal_(dB)",
    }

    if leq_mode:
        txt = "Leq_(dB),Weighting_A"
        txt += ",based_on_minutes" if basedon_minutes else ",based_on_10s"
    else:
        txt = non_leq_modes[subbits(ctrl, 0b00001110, 1)]

    txt += ",Slow" if slowmode else ",Fast"
    if is_maxhold(ctrl):
        txt += ",MaxHold"
    return txt

def chkchksum(msg):
    if len(msg) <= 2:
        return False
    return int(msg[-1]) == (sum(x for x in msg[:-1]) % 256)

def decode_msg(msg):
    m = re.match(
        b"^\x08\x04(?P<ctrl>.)\x0a\x0a(?P<value>...)\x01$", msg[:-1]
    )
    if not m:
        return None

    d = m.groupdict()
    try:
        val = "%0.1f" % (d["value"][0] * 10 + d["value"][1] + d["value"][2] / 10)
    except Exception:
        val = None

    return (val, modetxt(ord(d["ctrl"])))

def trySerialOpen(port, maxTries):
    for attempt in range(maxTries):
        try:
            port.open()
            return True
        except Exception as e:
            remaining = maxTries - attempt - 1
            print(f"[sauter] Could not open serial port: {e}")
            if remaining <= 0:
                break
            print("[sauter] Retrying in 5 s...")
            sleep(5)
    print("[sauter] Cannot open port — giving up.")
    return False


# ══════════════════════════════════════════════
# MEMS / A-weighting helpers
# ══════════════════════════════════════════════

def design_a_weighting(fs_hz: int):
    f1, f2, f3, f4 = 20.598997, 107.65265, 737.86223, 12194.217
    a1000 = 1.9997
    nums = [(2 * np.pi * f4) ** 2 * (10 ** (a1000 / 20.0)), 0, 0, 0, 0]
    dens = np.polymul(
        [1, 4 * np.pi * f4, (2 * np.pi * f4) ** 2],
        [1, 4 * np.pi * f1, (2 * np.pi * f1) ** 2],
    )
    dens = np.polymul(np.polymul(dens, [1, 2 * np.pi * f3]), [1, 2 * np.pi * f2])
    b, a = scipy_signal.bilinear(nums, dens, fs_hz)
    return b, a

def rms_db(samples: np.ndarray) -> float:
    rms = np.sqrt(np.mean(samples ** 2))
    rms = max(rms, 1e-12)
    return 20.0 * np.log10(rms)


# ══════════════════════════════════════════════
# Audio recording / saving threads
# ══════════════════════════════════════════════

def audioRecordThread():
    global run, buffer
    while run:
        if stream is None:
            time.sleep(0.1)
            continue

        try:
            audiodata = stream.read(chunk, exception_on_overflow=False)
            buffer.append(audiodata)
        except Exception as e:
            if run:
                print(f"[audio] Read error: {e}")
                time.sleep(0.1)
            continue

        totalSamples = len(buffer) * chunk
        if totalSamples > (audioDuration * fs) and not savingFile and not savingTimer:
            oldChunks = len(buffer) - round((audioDuration * fs) / chunk)
            del buffer[:oldChunks]

def audioFileSaveThread():
    global savingFile, savingTimer, buffer

    if savingFile:
        return

    savingFile = True
    try:
        filename = "audio_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + ".wav"
        fileBuffer = buffer.copy()

        wf = wave.open(os.path.join(FILE_SAVE_DIRECTORY, filename), "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(portAudio.get_sample_size(sample_format))
        wf.setframerate(fs)
        wf.writeframes(b"".join(fileBuffer))
        wf.close()
        print(f"[audio] Saved: {filename}")
    finally:
        savingFile = False
        savingTimer = None


# ══════════════════════════════════════════════
# Sauter sensor thread
# ══════════════════════════════════════════════

def sauterThread():
    global run, savingFile, savingTimer

    logFilename = "sauter_log_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + ".csv"
    csvPath = os.path.join(FILE_SAVE_DIRECTORY, logFilename)
    print(f"[sauter] Logging to {csvPath}")

    ser = serial.Serial()
    ser.baudrate = SAUTER_BAUDRATE
    ser.port     = SAUTER_PORT
    ser.timeout  = SAUTER_TIMEOUT

    if not trySerialOpen(ser, 100):
        print("[sauter] Thread exiting because port could not be opened.")
        return

    print(f"[sauter] Serial port opened: {SAUTER_PORT}")

    try:
        with open(csvPath, "w", newline="") as csvFile:
            writer = csv.writer(csvFile)
            writer.writerow(["timestamp", "value_dB", "mode"])
            csvFile.flush()

            while run:
                char = ser.read()

                if char == b"\x10":
                    ser.write(b"\x20")
                elif char == b"":
                    continue
                else:
                    sleep(1)
                    continue

                msg = bytes()
                while True:
                    char = ser.read()
                    if char == b"":
                        break
                    msg += char

                if len(msg) < 1:
                    continue
                if not chkchksum(msg):
                    print(f"[sauter] Checksum error: {msg!r}")
                    continue

                dt = datetime.now().strftime(STRFTIME)
                try:
                    decoded = decode_msg(msg)
                    if decoded is None:
                        print("[sauter] Decode returned None")
                        continue

                    val, mode = decoded
                    print(f"[sauter] {dt}  {val} dB  {mode}")
                    writer.writerow([dt, val, mode])
                    csvFile.flush()
                except Exception as e:
                    print(f"[sauter] Decode error: {e}")
                    continue

                if SAVE_AUDIO and val and float(val) > LEVEL_THRESHOLD:
                    print("[sauter] Loud sound detected!")
                    if savingTimer is not None and savingTimer.is_alive():
                        savingTimer.cancel()
                    savingTimer = Timer(audioDuration, audioFileSaveThread)
                    savingTimer.start()
    finally:
        try:
            ser.close()
        except Exception:
            pass

    print("[sauter] Thread stopped.")


# ══════════════════════════════════════════════
# MEMS sensor thread
# ══════════════════════════════════════════════

def _process_mems_window(block_timestamps, adc_values, level_writer, a_b, a_a):
    samples = np.array(adc_values, dtype=np.float64)
    samples -= ADC_MIDPOINT
    samples -= np.mean(samples)

    relative_db = rms_db(samples)
    weighted    = scipy_signal.lfilter(a_b, a_a, samples)
    a_weighted_db = rms_db(weighted)
    estimated_dba = a_weighted_db + CALIBRATION_OFFSET_DB

    pc_timestamp = datetime.now().isoformat(timespec="milliseconds")
    window_start = block_timestamps[0]
    window_end   = block_timestamps[-1]

    level_writer.writerow([
        pc_timestamp, window_start, window_end,
        f"{relative_db:.2f}", f"{a_weighted_db:.2f}", f"{estimated_dba:.2f}"
    ])

    print(
        f"[mems]  {pc_timestamp} | "
        f"window={window_start}-{window_end} ms | "
        f"rel={relative_db:.2f} dB | "
        f"A-rel={a_weighted_db:.2f} dB | "
        f"est={estimated_dba:.2f} dB(A)"
    )

def memsThread():
    global run

    a_b, a_a = design_a_weighting(MEMS_SAMPLE_RATE)

    rawFilename   = "mems_raw_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + ".csv"
    levelFilename = "mems_levels_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + ".csv"
    rawPath   = os.path.join(FILE_SAVE_DIRECTORY, rawFilename)
    levelPath = os.path.join(FILE_SAVE_DIRECTORY, levelFilename)

    print(f"[mems] Raw samples  → {rawPath}")
    print(f"[mems] Level log    → {levelPath}")

    try:
        ser = serial.Serial(MEMS_PORT, MEMS_BAUDRATE, timeout=1)
        time.sleep(2)
        ser.reset_input_buffer()
        print(f"[mems] Serial port opened: {MEMS_PORT} at {MEMS_BAUDRATE} baud")
    except Exception as e:
        print(f"[mems] Failed to open serial port: {e}")
        return

    current_block_ts        = None
    current_block           = []
    window_block_timestamps = []
    window_samples          = []

    try:
        with open(rawPath, "w", newline="", encoding="utf-8") as raw_file, \
             open(levelPath, "w", newline="", encoding="utf-8") as level_file:

            raw_writer = csv.writer(raw_file)
            level_writer = csv.writer(level_file)

            raw_writer.writerow(["pc_timestamp", "block_timestamp_ms", "sample_index", "adc_value"])
            level_writer.writerow([
                "pc_timestamp", "window_start_ms", "window_end_ms",
                "relative_db", "a_weighted_relative_db", "estimated_dba"
            ])
            raw_file.flush()
            level_file.flush()

            while run:
                try:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                except Exception as e:
                    if run:
                        print(f"[mems] Serial read error: {e}")
                        time.sleep(1)
                    continue

                if not line or line.startswith("#"):
                    continue

                parts = line.split(",")
                if len(parts) != 3:
                    continue

                try:
                    block_ts   = int(parts[0])
                    sample_idx = int(parts[1])
                    adc_value  = int(parts[2])
                except ValueError:
                    continue

                pc_timestamp = datetime.now().isoformat(timespec="milliseconds")
                raw_writer.writerow([pc_timestamp, block_ts, sample_idx, adc_value])

                if current_block_ts is None:
                    current_block_ts = block_ts

                if block_ts != current_block_ts:
                    if len(current_block) == MEMS_BLOCK_SIZE:
                        window_block_timestamps.append(current_block_ts)
                        window_samples.extend(current_block)

                        raw_file.flush()

                        if len(window_block_timestamps) >= MEMS_BLOCKS_WINDOW:
                            _process_mems_window(
                                window_block_timestamps,
                                window_samples,
                                level_writer,
                                a_b,
                                a_a
                            )
                            level_file.flush()
                            window_block_timestamps = []
                            window_samples = []

                    current_block_ts = block_ts
                    current_block = []

                current_block.append(adc_value)
                if len(current_block) > MEMS_BLOCK_SIZE:
                    current_block = current_block[:MEMS_BLOCK_SIZE]

            # Flush final complete block on shutdown
            if len(current_block) == MEMS_BLOCK_SIZE:
                window_block_timestamps.append(current_block_ts)
                window_samples.extend(current_block)
                raw_file.flush()

            # Flush final partial window if it contains at least one complete block
            if window_block_timestamps and window_samples:
                _process_mems_window(
                    window_block_timestamps,
                    window_samples,
                    level_writer,
                    a_b,
                    a_a
                )
                level_file.flush()
    finally:
        try:
            ser.close()
        except Exception:
            pass

    print("[mems] Thread stopped.")


# ══════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════

def main():
    global run, portAudio, stream
    global SAUTER_PORT, SAUTER_BAUDRATE
    global MEMS_PORT, MEMS_BAUDRATE
    global FILE_SAVE_DIRECTORY, SAVE_AUDIO, LEVEL_THRESHOLD
    global AUDIO_HW_ID, CALIBRATION_OFFSET_DB

    parser = argparse.ArgumentParser(description="Combined Sauter SU130 + MEMS dB logger")
    parser.add_argument("-sp", "--sauter-port", default=SAUTER_PORT)
    parser.add_argument("-mp", "--mems-port", default=MEMS_PORT)
    parser.add_argument("-sb", "--sauter-baud", type=int, default=SAUTER_BAUDRATE)
    parser.add_argument("-mb", "--mems-baud", type=int, default=MEMS_BAUDRATE)
    parser.add_argument("-f",  "--datafolder", default=FILE_SAVE_DIRECTORY)
    parser.add_argument("-s",  "--saveaudio", action="store_true", default=SAVE_AUDIO)
    parser.add_argument("-l",  "--levelthreshold", type=int, default=LEVEL_THRESHOLD)
    parser.add_argument("-i",  "--audiohwid", type=int, default=AUDIO_HW_ID)
    parser.add_argument("-c",  "--calibration", type=float, default=CALIBRATION_OFFSET_DB)

    args = parser.parse_args()

    SAUTER_PORT           = args.sauter_port
    SAUTER_BAUDRATE       = args.sauter_baud
    MEMS_PORT             = args.mems_port
    MEMS_BAUDRATE         = args.mems_baud
    FILE_SAVE_DIRECTORY   = args.datafolder
    SAVE_AUDIO            = args.saveaudio
    LEVEL_THRESHOLD       = args.levelthreshold
    AUDIO_HW_ID           = args.audiohwid
    CALIBRATION_OFFSET_DB = args.calibration

    os.makedirs(FILE_SAVE_DIRECTORY, exist_ok=True)

    print("=" * 60)
    print("  Combined dB Logger")
    print(f"  Sauter port  : {SAUTER_PORT}  @ {SAUTER_BAUDRATE} baud")
    print(f"  MEMS port    : {MEMS_PORT}  @ {MEMS_BAUDRATE} baud")
    print(f"  Output folder: {FILE_SAVE_DIRECTORY}")
    print(f"  Save audio   : {SAVE_AUDIO}  (threshold: {LEVEL_THRESHOLD} dB)")
    print(f"  Calibration  : {CALIBRATION_OFFSET_DB:+.1f} dB")
    print("=" * 60)

    if SAVE_AUDIO:
        portAudio = pyaudio.PyAudio()
        kwargs = dict(
            format=sample_format,
            channels=channels,
            rate=fs,
            frames_per_buffer=chunk,
            input=True,
        )
        if AUDIO_HW_ID >= 0:
            kwargs["input_device_index"] = AUDIO_HW_ID
        stream = portAudio.open(**kwargs)

    run = True
    threads = []

    if SAVE_AUDIO:
        at = threading.Thread(target=audioRecordThread, daemon=True, name="AudioRecord")
        at.start()
        threads.append(at)

    st = threading.Thread(target=sauterThread, daemon=True, name="Sauter")
    st.start()
    threads.append(st)

    mt = threading.Thread(target=memsThread, daemon=True, name="MEMS")
    mt.start()
    threads.append(mt)

    print("Running — press Ctrl+C to stop.\n")

    try:
        while run:
            time.sleep(0.5)
    finally:
        print("\n[main] Shutting down...")
        run = False

        for t in threads:
            t.join(timeout=5)

        if SAVE_AUDIO and stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
            try:
                portAudio.terminate()
            except Exception:
                pass

        print("[main] Done.")


if __name__ == "__main__":
    main()