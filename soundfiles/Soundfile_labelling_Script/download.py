import csv
import shutil
import subprocess
import tempfile
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

CLASS_LABELS_CSV = "class_labels_indices.csv"
INPUT_SEGMENTS_CSV = "unbalanced_train_segments.csv"

# Root output folder
OUTPUT_ROOT = Path(r"C:\Users\peter\desktop\audioset")

# Download or only prepare metadata/folders
DOWNLOAD_AUDIO = True

# Overwrite existing files
OVERWRITE_EXISTING = False

# Tools
YT_DLP_BIN = "yt-dlp"
FFMPEG_BIN = "ffmpeg"

# Audio output settings
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_EXTENSION = "wav"

# Split each AudioSet clip into this many seconds
SEGMENT_LENGTH_SECONDS = 5.0

# Reject clips with too many labels total
MAX_TOTAL_LABELS = 3  # set to None to disable

# Global unwanted labels
GLOBAL_EXCLUDE_LABELS = {
    "Music",
    "Soundtrack music",
    "Radio",
    "Television",
}

# Optional extra exclusions per class
PER_CLASS_EXCLUDE_LABELS = {
    "vehicle_noise": {
        "Speech",
        "Conversation",
        "Music",
    },
    "traffic": {
        "Music",
        "Soundtrack music",
    },
    "emergency": {
        "Civil defense siren",
    },
}

# ------------------------------------------------------------
# Final class structure
# NOTE:
# The values should be AudioSet display names where possible.
# Replace any placeholder label names with exact AudioSet names if needed.
# ------------------------------------------------------------
CLASS_STRUCTURE = {
    "weather": [
        "Rain",
        "Wind",
        "Thunder",
    ],
    "animals": [
        "Dog",
        "Bark",
        "Cat",
    ],
    "construction": [
        "Chainsaw",
        "Sawing",
        "Drill",
        "Jackhammer",
    ],
    "human_activity": [
        "Crowd"
    ],
    "traffic": [
        "Car",
        "Engine",
        "Train",
        "Motorcycle",
        "Traffic noise, roadway noise",
    ],
    "air_traffic": [
        "Aircraft",
        "Helicopter",
        "Airplane"
    ],
    "emergency": [
        "Siren",
        "Alarm",
    ],
    "joyriding": [
        "Skidding",
        "Tire squeal",
        "Accelerating, revving, vroom"
    ]
}

# Target totals per class
CLASS_TARGET_TOTALS = {
    "weather": 0,
    "animals": 0,
    "construction": 0,
    "human_activity": 0,
    "air_traffic": 600,
    "traffic": 0,
    "emergency": 0,
    "joyriding": 0
}

# ============================================================
# HELPERS
# ============================================================

def check_dependencies():
    missing = []
    if DOWNLOAD_AUDIO:
        if shutil.which(YT_DLP_BIN) is None:
            missing.append(YT_DLP_BIN)
        if shutil.which(FFMPEG_BIN) is None:
            missing.append(FFMPEG_BIN)

    if missing:
        raise EnvironmentError(
            f"Missing required tools: {', '.join(missing)}. "
            f"Install them and make sure they are available in PATH."
        )


def load_label_maps(class_labels_csv):
    name_to_mid = {}
    mid_to_name = {}

    with open(class_labels_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mid = row["mid"].strip()
            name = row["display_name"].strip()
            name_to_mid[name] = mid
            mid_to_name[mid] = name

    return name_to_mid, mid_to_name


def parse_positive_labels(label_string):
    if not label_string:
        return []
    label_string = label_string.strip().strip('"')
    return [x.strip() for x in label_string.split(",") if x.strip()]


def iter_audioset_rows(csv_path):
    with open(csv_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            row = next(csv.reader([line], skipinitialspace=True))
            if len(row) != 4:
                print(f"Skipping malformed row: {row}")
                continue

            yield {
                "YTID": row[0].strip(),
                "start_seconds": row[1].strip(),
                "end_seconds": row[2].strip(),
                "positive_labels": row[3].strip().strip('"'),
            }


def convert_label_names_to_mids(label_names, name_to_mid, context=""):
    mids = {}
    for label in label_names:
        if label not in name_to_mid:
            print(f"Warning: label not found in class_labels_indices.csv: {label} ({context})")
            continue
        mids[label] = name_to_mid[label]
    return mids


def ensure_output_dirs(root, class_structure):
    root.mkdir(parents=True, exist_ok=True)
    for class_name, sublabels in class_structure.items():
        class_dir = root / class_name
        class_dir.mkdir(parents=True, exist_ok=True)
        for sublabel in sublabels:
            safe_sublabel = sanitize_name(sublabel)
            (class_dir / safe_sublabel).mkdir(parents=True, exist_ok=True)


def sanitize_name(name):
    return (
        name.lower()
        .replace(",", "")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace("(", "")
        .replace(")", "")
    )


def distribute_targets(total, items):
    """
    Evenly distribute a class target total across its sublabels.
    Example: total=150, items=4 -> [38, 38, 37, 37]
    """
    if items <= 0:
        return []
    base = total // items
    remainder = total % items
    return [base + 1 if i < remainder else base for i in range(items)]


def build_sublabel_targets(class_structure, class_target_totals):
    targets = {}
    for class_name, sublabels in class_structure.items():
        total = class_target_totals[class_name]
        distributed = distribute_targets(total, len(sublabels))
        targets[class_name] = {
            sublabel: quota for sublabel, quota in zip(sublabels, distributed)
        }
    return targets


def download_source_audio(ytid, temp_dir):
    url = f"https://www.youtube.com/watch?v={ytid}"
    output_template = str(Path(temp_dir) / f"{ytid}.%(ext)s")

    cmd = [
        YT_DLP_BIN,
        "-f", "bestaudio/best",
        "-o", output_template,
        "--no-playlist",
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for {ytid}: {result.stderr.strip()}")

    matches = list(Path(temp_dir).glob(f"{ytid}.*"))
    matches = [p for p in matches if p.is_file() and not p.name.endswith(".part")]

    if not matches:
        raise FileNotFoundError(f"Downloaded file not found for {ytid}")

    return matches[0]


def cut_audio_segment(input_path, output_dir, base_filename, start_seconds, end_seconds):
    """
    Cuts audio into multiple fixed-length segments.
    """
    total_duration = float(end_seconds) - float(start_seconds)
    if total_duration <= 0:
        raise ValueError(
            f"Invalid clip duration: start={start_seconds}, end={end_seconds}"
        )

    num_segments = int(total_duration // SEGMENT_LENGTH_SECONDS)
    if num_segments <= 0:
        raise ValueError(
            f"No {SEGMENT_LENGTH_SECONDS:.0f}-second segments can be created from "
            f"clip length {total_duration:.2f}s"
        )

    created_files = []

    for i in range(num_segments):
        seg_start = float(start_seconds) + i * SEGMENT_LENGTH_SECONDS
        output_file = output_dir / f"{base_filename}_seg{i + 1}.{TARGET_EXTENSION}"

        cmd = [
            FFMPEG_BIN,
            "-y" if OVERWRITE_EXISTING else "-n",
            "-i", str(input_path),
            "-ss", str(seg_start),
            "-t", str(SEGMENT_LENGTH_SECONDS),
            "-ac", str(TARGET_CHANNELS),
            "-ar", str(TARGET_SAMPLE_RATE),
            str(output_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed for {output_file.name}: {result.stderr.strip()}"
            )

        created_files.append(output_file)

    return created_files


# ============================================================
# MAIN
# ============================================================

def main():
    if not Path(CLASS_LABELS_CSV).exists():
        raise FileNotFoundError(f"Missing file: {CLASS_LABELS_CSV}")

    if not Path(INPUT_SEGMENTS_CSV).exists():
        raise FileNotFoundError(f"Missing file: {INPUT_SEGMENTS_CSV}")

    check_dependencies()

    name_to_mid, mid_to_name = load_label_maps(CLASS_LABELS_CSV)
    ensure_output_dirs(OUTPUT_ROOT, CLASS_STRUCTURE)

    # Convert class/sublabel names to mids
    class_sublabel_to_mid = {}
    for class_name, sublabels in CLASS_STRUCTURE.items():
        class_sublabel_to_mid[class_name] = convert_label_names_to_mids(
            sublabels, name_to_mid, context=f"class={class_name}"
        )

    global_exclude_mids = set(
        convert_label_names_to_mids(
            GLOBAL_EXCLUDE_LABELS, name_to_mid, context="global exclude"
        ).values()
    )

    per_class_exclude_mids = {}
    for class_name, labels in PER_CLASS_EXCLUDE_LABELS.items():
        per_class_exclude_mids[class_name] = set(
            convert_label_names_to_mids(
                labels, name_to_mid, context=f"exclude={class_name}"
            ).values()
        )

    sublabel_targets = build_sublabel_targets(CLASS_STRUCTURE, CLASS_TARGET_TOTALS)

    # Counters
    sublabel_counts = {
        class_name: {sublabel: 0 for sublabel in sublabels}
        for class_name, sublabels in CLASS_STRUCTURE.items()
    }

    file_counters = {
        class_name: {sublabel: 0 for sublabel in sublabels}
        for class_name, sublabels in CLASS_STRUCTURE.items()
    }

    rows_to_process = []
    seen_segments = set()

    total_rows = 0
    skipped_global_exclude = 0
    skipped_too_many_labels = 0
    skipped_no_match = 0

    # Filter rows and assign to class + sublabel
    for row in iter_audioset_rows(INPUT_SEGMENTS_CSV):
        total_rows += 1

        segment_key = (
            row["YTID"],
            row["start_seconds"],
            row["end_seconds"],
        )

        label_mids = set(parse_positive_labels(row["positive_labels"]))
        label_names = sorted(mid_to_name.get(mid, mid) for mid in label_mids)

        if label_mids & global_exclude_mids:
            skipped_global_exclude += 1
            continue

        if MAX_TOTAL_LABELS is not None and len(label_mids) > MAX_TOTAL_LABELS:
            skipped_too_many_labels += 1
            continue

        matched = None

        # Assign each row to the first sublabel with remaining quota
        for class_name, sublabels in CLASS_STRUCTURE.items():
            class_excludes = per_class_exclude_mids.get(class_name, set())
            if label_mids & class_excludes:
                continue

            for sublabel in sublabels:
                mid = class_sublabel_to_mid[class_name].get(sublabel)
                if mid is None:
                    continue

                if mid not in label_mids:
                    continue

                if sublabel_counts[class_name][sublabel] >= sublabel_targets[class_name][sublabel]:
                    continue

                # Avoid reusing the exact same AudioSet segment
                if segment_key in seen_segments:
                    continue

                matched = (class_name, sublabel)
                break

            if matched:
                break

        if not matched:
            skipped_no_match += 1
            continue

        class_name, sublabel = matched
        seen_segments.add(segment_key)

        sublabel_counts[class_name][sublabel] += 1
        file_counters[class_name][sublabel] += 1

        sublabel_dir = sanitize_name(sublabel)
        file_index = file_counters[class_name][sublabel]
        filename = f"{class_name}_{sublabel_dir}_{file_index}.{TARGET_EXTENSION}"
        output_path = OUTPUT_ROOT / class_name / sublabel_dir / filename

        rows_to_process.append({
            "custom_class": class_name,
            "sublabel": sublabel,
            "sublabel_dir": sublabel_dir,
            "sublabel_target": sublabel_targets[class_name][sublabel],
            "filename": filename,
            "output_path": str(output_path),
            "YTID": row["YTID"],
            "start_seconds": row["start_seconds"],
            "end_seconds": row["end_seconds"],
            "positive_labels": row["positive_labels"],
            "all_label_names": ", ".join(label_names),
        })

    # Download/cut
    metadata_rows = []
    success_count = 0
    failure_count = 0

    for i, item in enumerate(rows_to_process, start=1):
        output_path = Path(item["output_path"])

        print(f"[{i}/{len(rows_to_process)}] {item['custom_class']} / {item['sublabel']} -> {item['filename']}")

        if not DOWNLOAD_AUDIO:
            metadata_rows.append({
                **item,
                "status": "planned_only",
            })
            continue

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                source_audio = download_source_audio(item["YTID"], tmpdir)

                created_files = cut_audio_segment(
                    source_audio,
                    output_path.parent,
                    output_path.stem,
                    item["start_seconds"],
                    item["end_seconds"],
                )

            for created_file in created_files:
                metadata_rows.append({
                    **item,
                    "filename": created_file.name,
                    "output_path": str(created_file),
                    "status": "ok",
                })

            success_count += len(created_files)

        except Exception as e:
            metadata_rows.append({
                **item,
                "status": f"failed: {e}",
            })
            failure_count += 1
            print(f"  Failed: {e}")

    # Save metadata
    metadata_csv = OUTPUT_ROOT / "metadata.csv"
    fieldnames = [
        "custom_class",
        "sublabel",
        "sublabel_dir",
        "sublabel_target",
        "filename",
        "output_path",
        "YTID",
        "start_seconds",
        "end_seconds",
        "positive_labels",
        "all_label_names",
        "status",
    ]

    with open(metadata_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metadata_rows)

    # Summary
    print("\nDone.")
    print(f"Total rows scanned: {total_rows}")
    print(f"Rows selected: {len(rows_to_process)}")
    print(f"Skipped by global exclude: {skipped_global_exclude}")
    print(f"Skipped by label-count filter: {skipped_too_many_labels}")
    print(f"Skipped because no target matched / no quota left: {skipped_no_match}")
    print(f"Successful saved segments: {success_count}")
    print(f"Failed source clips: {failure_count}")
    print(f"Metadata saved to: {metadata_csv}")

    print("\nCollected counts by source clip:")
    for class_name, sublabels in sublabel_counts.items():
        class_total = sum(sublabels.values())
        print(f"  {class_name}: {class_total}")
        for sublabel, count in sublabels.items():
            print(f"    - {sublabel}: {count} / {sublabel_targets[class_name][sublabel]}")


if __name__ == "__main__":
    main()