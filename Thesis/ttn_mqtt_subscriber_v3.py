import paho.mqtt.client as mqtt
import json
import csv
import os
import base64
from datetime import datetime

# ─── Credentials ────────────────────────────────────────────────────────────
TTN_APP_ID  = "malmo-noise-sensor@ttn"   # Your TTN Application ID (e.g. malmo-noise-sensor)
TTN_API_KEY = "NNSXS.77QAFCXXPJWK36AJ3OEMJL6W452A73F5CBFG66A.TCUFEQZNRWGAJXBXB2XDPCKTX3IZ2P47IA7CX3YMKTI6TGRFTBBA"   # Your TTN API Key (starts with NNSXS...)
TTN_REGION  = "eu1"        # e.g. eu1, nam1, au1
# ─────────────────────────────────────────────────────────────────────────────

BROKER = f"{TTN_REGION}.cloud.thethings.network"
PORT   = 8883
TOPIC  = f"v3/{TTN_APP_ID}/devices/+/up"

RAW_CSV     = "raw_data.csv"
DECODED_CSV = "transmission_data.csv"

RAW_COLUMNS = [
    "received_at",
    "device_id",
    "dev_eui",
    "dev_addr",
    "f_cnt",
    "f_port",
    "frm_payload_base64",
    "frm_payload_hex",
    "frequency_hz",
    "spreading_factor",
    "bandwidth",
    "coding_rate",
    "rssi",
    "snr",
    "gateway_id",
    "consumed_airtime_s",
]

# Matches the structure returned by your TTN JS payload formatter
DECODED_COLUMNS = [
    "received_at",
    "device_id",
    "f_cnt",
    "dB (A)",
    "Timestamp",
    "seq_number",
    "coordinates",
    "label",
]


def ensure_csvs():
    for filepath, columns in [(RAW_CSV, RAW_COLUMNS), (DECODED_CSV, DECODED_COLUMNS)]:
        if not os.path.exists(filepath):
            with open(filepath, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=columns).writeheader()
            print(f"[CSV] Created '{filepath}'")


def append_row(filepath, columns, row):
    with open(filepath, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=columns).writerow(row)

def parse_and_save(payload_json: dict):
    try:
        data       = payload_json.get("uplink_message", {})
        end_device = payload_json.get("end_device_ids", {})

        # ── Identity ─────────────────────────────────────────────
        device_id   = end_device.get("device_id", "")
        dev_eui     = end_device.get("dev_eui", "")
        dev_addr    = end_device.get("dev_addr", "")
        f_cnt       = data.get("f_cnt", "")
        f_port      = data.get("f_port", "")
        received_at = data.get("received_at", datetime.utcnow().isoformat())

        # ── Raw payload ──────────────────────────────────────────
        raw_b64 = data.get("frm_payload", "")

        if raw_b64:
            raw_bytes = base64.b64decode(raw_b64)
            raw_hex   = raw_bytes.hex()
            raw_str   = raw_bytes.decode("latin1", errors="replace")
        else:
            raw_bytes = b""
            raw_hex   = ""
            raw_str   = ""

        # ── Radio settings ───────────────────────────────────────
        settings  = data.get("settings", {})
        lora      = settings.get("data_rate", {}).get("lora", {})
        frequency = settings.get("frequency", "")
        sf        = lora.get("spreading_factor", "")
        bw        = lora.get("bandwidth", "")
        cr        = lora.get("coding_rate", "")

        # ── Gateway / signal ─────────────────────────────────────
        rx_meta    = data.get("rx_metadata", [{}])[0]
        rssi       = rx_meta.get("rssi", "")
        snr        = rx_meta.get("snr", "")
        gateway_id = rx_meta.get("gateway_ids", {}).get("gateway_id", "")
        consumed   = data.get("consumed_airtime", "").replace("s", "")

        # ── Save RAW CSV (always complete) ───────────────────────
        append_row(RAW_CSV, RAW_COLUMNS, {
            "received_at":        received_at,
            "device_id":          device_id,
            "dev_eui":            dev_eui,
            "dev_addr":           dev_addr,
            "f_cnt":              f_cnt,
            "f_port":             f_port,
            "frm_payload_base64": raw_b64,
            "frm_payload_hex":    raw_hex,
            "frequency_hz":       frequency,
            "spreading_factor":   sf,
            "bandwidth":          bw,
            "coding_rate":        cr,
            "rssi":               rssi,
            "snr":                snr,
            "gateway_id":         gateway_id,
            "consumed_airtime_s": consumed,
        })

        # ── Decode payload safely ────────────────────────────────
        db = hour = minute = second = sequence = ""
        lat = lon = ""
        label = ""

        if len(raw_bytes) > 0:
            print(f"[DEBUG] Payload length: {len(raw_bytes)} bytes")
            print(f"[DEBUG] HEX: {raw_hex}")

        # Flexible decoding (does NOT assume fixed 14 bytes)
        try:
            if len(raw_bytes) >= 1:
                db = raw_bytes[0]

            if len(raw_bytes) >= 4:
                hour   = raw_bytes[1]
                minute = raw_bytes[2]
                second = raw_bytes[3]

            if len(raw_bytes) >= 5:
                sequence = raw_bytes[4]

            if len(raw_bytes) >= 13:
                lat = (raw_bytes[5] << 24 | raw_bytes[6] << 16 |
                       raw_bytes[7] << 8 | raw_bytes[8]) / 1e6

                lon = (raw_bytes[9] << 24 | raw_bytes[10] << 16 |
                       raw_bytes[11] << 8 | raw_bytes[12]) / 1e6

            ## CHANGE LABELS HERE CORRESPONDING TO MACHINE LEARNING MODEL
            if len(raw_bytes) >= 14:
                labels = ["unknown", "traffic_transport", "construction", "nature", "industrial"]
                idx = raw_bytes[13]
                label = labels[idx] if idx < len(labels) else "unknown"

        except Exception as e:
            print(f"[WARN] Decoding error: {e}")

        # ── Formatting ───────────────────────────────────────────
        timestamp = ""
        if hour != "":
            timestamp = f"{int(hour):02}:{int(minute):02}:{int(second):02}"

        coordinates = ""
        if lat != "":
            coordinates = f"{lat}, {lon}"

        # ── Save decoded CSV ─────────────────────────────────────
        append_row(DECODED_CSV, DECODED_COLUMNS, {
            "received_at": received_at,
            "device_id":   device_id,
            "f_cnt":       f_cnt,
            "dB (A)":      db,
            "Timestamp":   timestamp,
            "seq_number":  sequence,
            "coordinates": coordinates,
            "label":       label,
        })

        # ── Debug output ─────────────────────────────────────────
        print(f"\n[{received_at}] {device_id}")
        print(f"RAW (base64): {raw_b64}")
        print(f"RAW (hex):    {raw_hex}")
        print(f"RAW (str):    {raw_str}")
        print(f"Decoded → dB={db}, time={timestamp}, seq={sequence}, coords={coordinates}, label={label}")
        print(f"RSSI={rssi} dBm | SNR={snr}\n")

    except Exception as e:
        print(f"[ERROR] Failed to parse message: {e}")
        print(json.dumps(payload_json, indent=2))

# ─── MQTT Callbacks ───────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[MQTT] Connected to {BROKER}")
        client.subscribe(TOPIC)
        print(f"[MQTT] Subscribed to: {TOPIC}")
    else:
        print(f"[MQTT] Connection failed with code {rc}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        parse_and_save(payload)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Could not decode JSON: {e}")


def on_disconnect(client, userdata, rc, properties=None):
    if rc != 0:
        print(f"[MQTT] Unexpected disconnect (rc={rc}). Will attempt reconnect...")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ensure_csvs()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(TTN_APP_ID, TTN_API_KEY)
    client.tls_set()

    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    print(f"[MQTT] Connecting to {BROKER}:{PORT} ...")
    client.connect(BROKER, PORT, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[MQTT] Disconnecting...")
        client.disconnect()


if __name__ == "__main__":
    main()
