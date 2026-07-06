import os
import re
import pandas as pd

# Frequencies to process
TARGET_FREQUENCIES = [500, 1000, 2000, 4000, 8000]


def extract_frequency(filename):
    """
    Extract frequency from filename.

    Example:
    'case 4000hz.csv' -> 4000
    """

    match = re.search(r"(\d+)\s*hz", filename.lower())

    if match:
        return int(match.group(1))

    return None


def extract_db_mean(filepath):
    """
    Read CSV and calculate mean of mems_dba column.
    """

    df = pd.read_csv(filepath)

    # Normalize column names
    df.columns = [c.strip().lower() for c in df.columns]

    if "mems_dba" not in df.columns:
        raise ValueError(
            f"'mems_dba' column missing in {filepath}"
        )

    return df["mems_dba"].mean()


def scan_directory(folder):
    """
    Scan directory and organize results by frequency.
    """

    results = {}

    for filename in os.listdir(folder):

        if not filename.endswith(".csv"):
            continue

        filepath = os.path.join(folder, filename)

        lower_name = filename.lower()

        freq = extract_frequency(filename)

        if freq not in TARGET_FREQUENCIES:
            continue

        mean_db = extract_db_mean(filepath)

        if freq not in results:
            results[freq] = {}

        if "nocase" in lower_name:
            results[freq]["nocase"] = mean_db

        elif "case" in lower_name:
            results[freq]["case"] = mean_db

    return results


def compare_results(results):

    output = []

    print("\n===== AUDIO COMPARISON =====\n")

    for freq in TARGET_FREQUENCIES:

        if freq not in results:
            continue

        case_value = results[freq].get("case")
        nocase_value = results[freq].get("nocase")

        if case_value is None or nocase_value is None:
            print(f"{freq} Hz -> Missing case or nocase file")
            continue

        difference = case_value - nocase_value

        print(
            f"{freq} Hz | "
            f"Case: {case_value:.2f} dBA | "
            f"NoCase: {nocase_value:.2f} dBA | "
            f"Difference: {difference:.2f} dBA"
        )

        output.append({
            "frequency_hz": freq,
            "case_mean_dba": round(case_value, 2),
            "nocase_mean_dba": round(nocase_value, 2),
            "difference_dba": round(difference, 2)
        })

    output_df = pd.DataFrame(output)

    # Save summary
    output_df.to_csv("frequency_comparison.csv", index=False)

    print("\nSaved frequency_comparison.csv")


if __name__ == "__main__":

    # Folder containing CSV files
    DATA_FOLDER = "sound_retention"

    results = scan_directory(DATA_FOLDER)

    compare_results(results)