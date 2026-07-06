import pandas as pd
import glob
import os
import re

path = "."

files = glob.glob(os.path.join(path, "*hz-*.csv"))

all_data = []

print(f"Found {len(files)} files")

for file in files:
    filename = os.path.basename(file)

    match = re.search(r"(\d+)\s*hz", filename, re.IGNORECASE)
    if not match:
        print(f"Skipping file (no frequency match): {filename}")
        continue

    frequency = int(match.group(1))

    try:
        df = pd.read_csv(file)
    except Exception as e:
        print(f"Could not read {filename}: {e}")
        continue

    df["frequency"] = frequency
    df["file"] = filename

    all_data.append(df)

if len(all_data) == 0:
    raise ValueError("No valid data loaded. Check filenames and folder path.")

combined = pd.concat(all_data, ignore_index=True)

std_dev = combined.groupby("frequency")[["sauter_dba", "mems_dba"]].std()
std_dev = std_dev.sort_index()

print("Standard deviation per frequency:")
print(std_dev)
std_dev.to_csv("calculated_std.csv")

means_path = os.path.join(path, "calculated_means.csv")
if os.path.exists(means_path):
    means = pd.read_csv(means_path)
    print("\nMeans file preview:")
    print(means.head())