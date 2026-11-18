"""
3run_golden.py — Retail Shop MDM
==================================
Reads the scored customer-store records, groups them by customer_id
(cluster), applies the survivorship policy to pick one golden master
record per customer, and writes:
  • data/mdm_customers_golden.csv   — one row per unique customer
  • data/mdm_cluster_summary.csv    — cluster stats for the MDM UI
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from dataclasses import asdict, fields as dc_fields
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import CustomerRecord
from golden_record_policy import GoldenRecordPolicyEngine

# ── Paths ──────────────────────────────────────────────────────────────────
HERE         = Path(__file__).resolve().parent
INPUT_CSV    = HERE / "data" / "mdm_customers_scored.csv"
GOLDEN_CSV   = HERE / "data" / "mdm_customers_golden.csv"
SUMMARY_CSV  = HERE / "data" / "mdm_cluster_summary.csv"

RECORD_FIELDS = {f.name for f in dc_fields(CustomerRecord)}


def run():
    if not INPUT_CSV.exists():
        print(f"ERROR: {INPUT_CSV} not found — run 2run_scoring.py first.")
        sys.exit(1)

    print(f"Loading {INPUT_CSV} …")
    df = pd.read_csv(INPUT_CSV, low_memory=False)
    print(f"  {len(df):,} scored records")

    # Reconstruct CustomerRecord objects
    records: list[CustomerRecord] = []
    for row in df.to_dict(orient="records"):
        clean = {k: v for k, v in row.items() if k in RECORD_FIELDS}
        for int_col in ("total_transactions",):
            clean[int_col] = int(clean.get(int_col) or 0)
        for float_col in ("total_spend", "avg_order_value",
                          "total_loyalty_points", "return_rate", "trust_score"):
            val = clean.get(float_col)
            clean[float_col] = float(val) if val is not None and str(val) != "nan" else 0.0
        for bool_col in ("is_online_buyer",):
            val = clean.get(bool_col)
            clean[bool_col] = None if (val is None or (isinstance(val, float) and str(val) == "nan")) else bool(val)
        records.append(CustomerRecord(**clean))

    # Attach scored fields from the DataFrame
    scored_map = {}
    for row in df.to_dict(orient="records"):
        scored_map[row["record_id"]] = row
    for r in records:
        sm = scored_map.get(r.record_id, {})
        r.trust_score = float(sm.get("trust_score") or 0)

    # Cluster by customer_id
    clusters: dict[str, list[CustomerRecord]] = defaultdict(list)
    for r in records:
        clusters[r.cluster_id].append(r)
    print(f"  {len(clusters):,} unique customers (clusters)")

    # Apply survivorship
    engine = GoldenRecordPolicyEngine()
    golden_records = [engine.build_golden(v) for v in clusters.values()]

    # Golden CSV
    df_golden = pd.DataFrame([asdict(r) for r in golden_records])
    GOLDEN_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_golden.to_csv(GOLDEN_CSV, index=False)
    print(f"Saved golden → {GOLDEN_CSV}  ({len(df_golden):,} records)")

    # Cluster summary CSV (useful for the MDM UI)
    summary_rows = []
    for cid, cluster in clusters.items():
        trust_scores = [r.trust_score for r in cluster]
        summary_rows.append({
            "cluster_id":        cid,
            "record_count":      len(cluster),
            "source_systems":    ", ".join(sorted({r.source_system for r in cluster})),
            "max_trust_score":   round(max(trust_scores), 1),
            "avg_trust_score":   round(sum(trust_scores) / len(trust_scores), 1),
            "golden_record_id":  next(r.record_id for r in cluster if r.trust_score == max(trust_scores)),
            "last_purchase_date": max(
                (r.last_purchase_date or "" for r in cluster), default=""
            ),
        })
    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(SUMMARY_CSV, index=False)
    print(f"Saved summary → {SUMMARY_CSV}  ({len(df_summary):,} clusters)")

    # Print stats
    multi = sum(1 for v in clusters.values() if len(v) > 1)
    avg_t = round(df_golden["trust_score"].mean(), 1)
    print(f"\n  Customers seen in >1 store : {multi:,}")
    print(f"  Avg golden trust score     : {avg_t}")
    print(f"  High-trust (≥80) customers : {int((df_golden['trust_score'] >= 80).sum()):,}")
    print(f"  Low-trust  (<50) customers : {int((df_golden['trust_score'] <  50).sum()):,}")


if __name__ == "__main__":
    run()
