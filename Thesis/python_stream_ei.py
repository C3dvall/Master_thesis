import serial
import wave
import time
import requests
import os
from datetime import datetime

# ================= CONFIG =================

SERIAL_PORT = "COM21"          # Change this
BAUD_RATE = 115200

SAMPLE_RATE = 16000
RECORD_SECONDS = 5

LABEL = "audio"

API_KEY = "ei_e4079b9f84e2892213602d99a0fc8af698c3c9175776d725"

UPLOAD_URL = (
    "https://ingestion.edgeimpulse.com/api/training/files"
)

# ==========================================

SAMPLES_PER_FILE = SAMPLE_RATE * RECORD_SECONDS

ser = serial.Serial(
    SERIAL_PORT,
    BAUD_RATE,
    timeout=1
)

print("Connected to serial port")

os.makedirs("recordings", exist_ok=True)

while True:

    print("\nRecording 5 seconds...")

    samples = []

    while len(samples) < SAMPLES_PER_FILE:

        try:
            line = (
                ser.readline()
                .decode(errors="ignore")
                .strip()
            )

            if not line:
                continue

            value = int(line)

            # Clamp to signed 16-bit
            value = max(-32768, min(32767, value))

            samples.append(value)

        except:
            continue

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    filename = (
        f"recordings/{LABEL}_{timestamp}.wav"
    )

    print(f"Saving {filename}")

    with wave.open(filename, "w") as wf:

        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)

        audio_bytes = bytearray()

        for s in samples:

            audio_bytes.extend(
                int(s).to_bytes(
                    2,
                    byteorder="little",
                    signed=True
                )
            )

        wf.writeframes(audio_bytes)

    print("Uploading to Edge Impulse...")

    with open(filename, "rb") as f:

        response = requests.post(
            UPLOAD_URL,
            headers={
                "x-api-key": API_KEY
            },
            files={
                "data": (
                    os.path.basename(filename),
                    f,
                    "audio/wav"
                )
            }
        )

    print("Upload status:", response.status_code)

    if response.status_code == 200:

        print("Upload successful")

    else:

        print(response.text)

    time.sleep(1)