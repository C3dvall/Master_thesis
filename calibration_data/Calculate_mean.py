import os
import pandas as pd
from collections import defaultdict

INPUT_FOLDER = "."
OUTPUT_FILE = "calculated_means.csv"

grouped_data = defaultdict(lambda: {"mems": [], "sauter": []})

print("Files found:", os.listdir(INPUT_FOLDER))

for file in os.listdir(INPUT_FOLDER):
    if file.endswith(".csv"):
        print(f"Processing: {file}")
        file_path = os.path.join(INPUT_FOLDER, file)

        try:
            df = pd.read_csv(file_path)
            df.columns = df.columns.str.strip()

            if "mems_dba" not in df.columns or "sauter_dba" not in df.columns:
                print(f"Skipping {file} (missing columns)")
                continue

            mems_mean = df["mems_dba"].mean()
            sauter_mean = df["sauter_dba"].mean()

            freq = file.split("-")[0]

            grouped_data[freq]["mems"].append(mems_mean)
            grouped_data[freq]["sauter"].append(sauter_mean)

        except Exception as e:
            print(f"Error processing {file}: {e}")

print("Grouped data:", grouped_data)

results = []
for freq, values in grouped_data.items():
    mems_avg = round(sum(values["mems"]) / len(values["mems"]), 2)
    sauter_avg = round(sum(values["sauter"]) / len(values["sauter"]), 2)
    results.append([freq, mems_avg, sauter_avg])

output_df = pd.DataFrame(results, columns=["csv_name", "mems_mean", "sauter_mean"])

if not output_df.empty:
    output_df["freq_num"] = output_df["csv_name"].str.replace("hz", "").astype(int)
    output_df = output_df.sort_values("freq_num").drop(columns="freq_num")

# Add difference column (sauter - mems)
output_df["difference"] = round(output_df["sauter_mean"] - output_df["mems_mean"], 2)

output_df.to_csv(OUTPUT_FILE, index=False)

print(f"Saved summary to {OUTPUT_FILE}")