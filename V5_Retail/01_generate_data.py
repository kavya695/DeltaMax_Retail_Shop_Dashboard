"""DeltaMax V5 Retail: generate monthly retail shop data locally."""
import argparse, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "engine_scripts"))
from generate_retail_data import generate_all

def main():
    ap = argparse.ArgumentParser(description="Generate local retail shop data for DeltaMax V5.")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--random", action="store_true")
    args = ap.parse_args()
    if args.random:
        import numpy as np
        seed = int(np.random.default_rng().integers(1, 1_000_000))
    else:
        seed = args.seed
    generate_all(args.year, seed, os.path.join(ROOT, "data"))
if __name__ == "__main__":
    main()
