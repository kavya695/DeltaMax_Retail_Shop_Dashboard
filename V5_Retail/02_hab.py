"""DeltaMax V5 Retail: validate the combined HAB-style local retail dataset."""
import os
import pandas as pd
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data", "retail_shop_data.csv")

def main():
    if not os.path.exists(DATA):
        raise SystemExit("Retail data not found. Run: python 01_generate_data.py")
    df = pd.read_csv(DATA, low_memory=False)
    print(f"Combined retail rows: {len(df):,}")
    print(f"Columns: {len(df.columns)}")
    if "data_month" in df:
        print("Months:", ", ".join(sorted(df["data_month"].dropna().astype(str).unique())))
    if "data_lineage" in df:
        print("Lineage counts:")
        print(df["data_lineage"].value_counts(dropna=False).to_string())
    print("Local combined dataset:", DATA)
    print("No cloud upload performed.")
if __name__ == "__main__":
    main()
