"""DeltaMax V5 Retail: run local quality, anomaly, drift, freshness and trust scoring."""
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from engine.pipeline import run_project

def main():
    data = os.path.join(ROOT, "data", "retail_shop_data.csv")
    config = os.path.join(ROOT, "configs", "retail.yaml")
    if not os.path.exists(data):
        raise SystemExit("Retail data not found. Run: python 01_generate_data.py")
    result = run_project(config, ROOT, send_email=False, verbose=True)
    print("\\nLocal retail trust pipeline completed.")
    print("Results folder:", os.path.join(ROOT, "results", "retail"))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
