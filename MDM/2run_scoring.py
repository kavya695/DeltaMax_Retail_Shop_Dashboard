"""
2run_scoring.py — Retail Shop MDM
===================================
Loads the raw customer-store records produced by 1generate_data.py,
scores every record with the retail trust engine, and writes the
scored CSV to data/mdm_customers_scored.csv.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from dataclasses import asdict

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import CustomerRecord
from trust_score_engine import TrustScoreEngine

# ── Paths ──────────────────────────────────────────────────────────────────
HERE       = Path(__file__).resolve().parent
INPUT_CSV  = HERE / "data" / "mdm_customers_raw.csv"
OUTPUT_CSV = HERE / "data" / "mdm_customers_scored.csv"

# Fields that belong to CustomerRecord (used for safe reconstruction)
from dataclasses import fields as dc_fields
RECORD_FIELDS = {f.name for f in dc_fields(CustomerRecord)}


def run():
    if not INPUT_CSV.exists():
        print(f"ERROR: {INPUT_CSV} not found — run 1generate_data.py first.")
        sys.exit(1)

    print(f"Loading {INPUT_CSV} …")
    df = pd.read_csv(INPUT_CSV, low_memory=False)
    print(f"  {len(df):,} customer-store records loaded")

    # Build CustomerRecord objects safely
    records: list[CustomerRecord] = []
    for row in df.to_dict(orient="records"):
        clean = {k: v for k, v in row.items() if k in RECORD_FIELDS}
        # Coerce numeric types
        for int_col in ("total_transactions",):
            clean[int_col] = int(clean.get(int_col) or 0)
        for float_col in ("total_spend", "avg_order_value",
                          "total_loyalty_points", "return_rate", "trust_score"):
            val = clean.get(float_col)
            clean[float_col] = float(val) if val is not None and str(val) != "nan" else 0.0
        for bool_col in ("is_online_buyer",):
            val = clean.get(bool_col)
            if isinstance(val, float) and str(val) == "nan":
                clean[bool_col] = None
            elif val is not None:
                clean[bool_col] = bool(val)
        records.append(CustomerRecord(**clean))

    # Score
    engine = TrustScoreEngine()
    reports = engine.score_all(records)
    print(f"  Generated {len(reports)} trust score reports")

    report_map = {rp.record_id: rp for rp in reports}

    # Build output rows
    rows = []
    for r in records:
        base = asdict(r)
        rp   = report_map.get(r.record_id)
        if rp:
            base["rules_score"]          = rp.rules_score
            base["drift_score"]          = rp.drift_score
            base["anomaly_score"]        = rp.anomaly_score
            base["trust_score"]          = rp.trust_score
            base["is_golden"]            = rp.is_golden
            base["survivorship_reason"]  = rp.survivorship_reason
            base["anomaly_flags"]        = ", ".join(rp.anomaly_result.flags) if rp.anomaly_result else ""
            base["drift_detail"]         = rp.drift_result.detail if rp.drift_result else ""
        else:
            for col in ("rules_score","drift_score","anomaly_score","trust_score",
                        "is_golden","survivorship_reason","anomaly_flags","drift_detail"):
                base[col] = None
        rows.append(base)

    df_out = pd.DataFrame(rows)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved → {OUTPUT_CSV}  ({len(df_out):,} rows)")

    # Summary
    golden_count = int(df_out["is_golden"].fillna(False).astype(bool).sum())
    avg_trust    = round(float(df_out["trust_score"].dropna().mean()), 1)
    print(f"  Golden records : {golden_count:,}")
    print(f"  Avg trust score: {avg_trust}")


if __name__ == "__main__":
    run()
