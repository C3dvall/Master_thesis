from pathlib import Path
import csv

# Change this to your dataset folder
BASE_PATH = Path("soundfiles/ESC50/rain")

# Change this if you want the renamed files copied somewhere else
# If None, files are renamed in-place
OUTPUT_PATH = None  # Example: Path("path/to/output")

# Mapping from original folder labels to final Edge Impulse class
LABEL_TO_CLASS = {
    "rain": "weather",
    "wind": "weather",
    "thunder": "weather",

    # Add more here, for example:
    # "dog": "animals",
    # "cat": "animals",
    # "chainsaw": "construction",
}

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}

metadata_rows = []

target_base = OUTPUT_PATH if OUTPUT_PATH else BASE_PATH

for folder_path in BASE_PATH.iterdir():
    if not folder_path.is_dir():
        continue

    original_label = folder_path.name
    final_class = LABEL_TO_CLASS.get(original_label)

    if final_class is None:
        print(f"Skipping unknown label folder: {original_label}")
        continue

    for file_path in folder_path.iterdir():
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        stem = file_path.stem
        suffix = file_path.suffix

        # Avoid adding the suffix twice
        if stem.endswith(f"_{original_label}"):
            new_filename = file_path.name
        else:
            new_filename = f"{stem}_{original_label}{suffix}"

        target_folder = target_base / final_class
        target_folder.mkdir(parents=True, exist_ok=True)

        new_path = target_folder / new_filename

        if OUTPUT_PATH:
            # Copy to new structure
            new_path.write_bytes(file_path.read_bytes())
        else:
            # Rename in-place, but still place files inside final class folder
            new_path = folder_path / new_filename
            file_path.rename(new_path)

        metadata_rows.append({
            "new_filename": new_filename,
            "original_filename": file_path.name,
            "original_label": original_label,
            "final_class": final_class,
            "source_folder": str(folder_path),
            "new_path": str(new_path)
        })

        print(f"{file_path.name} -> {new_filename} | class: {final_class}")

metadata_path = target_base / "metadata_labels.csv"

with metadata_path.open("w", newline="", encoding="utf-8") as csvfile:
    fieldnames = [
        "new_filename",
        "original_filename",
        "original_label",
        "final_class",
        "source_folder",
        "new_path"
    ]

    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(metadata_rows)

print(f"\nDone. Metadata saved to: {metadata_path}")