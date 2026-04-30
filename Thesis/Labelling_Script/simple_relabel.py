from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Change only this folder
BASE_PATH = SCRIPT_DIR.parent.parent / "soundfiles" / "ESC50" / "train"

# Automatically becomes "wind", "rain", "thunder", etc.
LABEL_SUFFIX = BASE_PATH.name

print("Using path:", BASE_PATH.resolve())
print("Using suffix:", LABEL_SUFFIX)

for file_path in BASE_PATH.iterdir():
    if not file_path.is_file():
        continue

    if file_path.suffix.lower() != ".wav":
        continue

    stem = file_path.stem
    suffix = file_path.suffix

    # Skip if already renamed with this folder's suffix
    if stem.endswith(f"_{LABEL_SUFFIX}"):
        continue

    new_name = f"{stem}_{LABEL_SUFFIX}{suffix}"
    new_path = BASE_PATH / new_name

    file_path.rename(new_path)
    print(f"{file_path.name} -> {new_name}")

print("Done.")