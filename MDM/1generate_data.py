"""
1generate_data.py — Retail Shop MDM
====================================
Reads the retail transaction CSV and aggregates one CustomerRecord
per (customer_id × store_id) combination so the MDM can find the
golden record for each customer across all stores they visited.

Output: data/mdm_customers_raw.csv  (local, relative to this script)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from dataclasses import asdict

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import CustomerRecord

# ── Paths ──────────────────────────────────────────────────────────────────
HERE         = Path(__file__).resolve().parent
RETAIL_CSV   = HERE.parent / "V5_Retail" / "data" / "retail_shop_data.csv"
OUTPUT_CSV   = HERE / "data" / "mdm_customers_raw.csv"


def build_records(df: pd.DataFrame) -> list[CustomerRecord]:
    """
    One CustomerRecord per (customer_id, store_id) pair.
    If a customer only visited one store they still get one record.
    If they visited multiple stores they get one record per store,
    so the golden-record engine can pick the best / most complete one.
    """
    df = df.copy()
    df["sale_date"]  = pd.to_datetime(df["sale_date"],  errors="coerce")
    df["total_amount"] = pd.to_numeric(df["total_amount"],  errors="coerce").fillna(0)
    df["loyalty_points"] = pd.to_numeric(df["loyalty_points"], errors="coerce").fillna(0)
    df["online_order_flag"] = pd.to_numeric(df["online_order_flag"], errors="coerce")
    df["is_return"] = pd.to_numeric(df["is_return"], errors="coerce").fillna(0)

    records: list[CustomerRecord] = []

    for (cid, store_id), grp in df.groupby(["customer_id", "store_id"], sort=False):
        city          = str(grp["store_city"].iloc[0])
        lineage       = str(grp["data_lineage"].iloc[-1])
        data_month    = str(grp["data_month"].max())

        txn_count     = len(grp)
        total_spend   = round(float(grp["total_amount"].sum()), 2)
        avg_order     = round(float(grp["total_amount"].mean()), 2)
        loyalty_pts   = round(float(grp["loyalty_points"].sum()), 1)
        return_rate   = round(float(grp["is_return"].mean()), 4)

        # Preferred category — most frequent
        pref_cat  = (grp["category"].value_counts().index[0]
                     if "category" in grp.columns and grp["category"].notna().any()
                     else None)
        pref_pay  = (grp["payment_method"].value_counts().index[0]
                     if "payment_method" in grp.columns and grp["payment_method"].notna().any()
                     else None)

        online_vals = grp["online_order_flag"].dropna()
        is_online   = (bool(online_vals.mean() >= 0.5) if not online_vals.empty else None)

        first_date = str(grp["sale_date"].min().date()) if grp["sale_date"].notna().any() else None
        last_date  = str(grp["sale_date"].max().date()) if grp["sale_date"].notna().any() else None

        records.append(CustomerRecord(
            record_id            = f"{cid}__{store_id}",
            source_system        = str(store_id),
            customer_id          = str(cid),
            store_id             = str(store_id),
            store_city           = city,
            first_purchase_date  = first_date,
            last_purchase_date   = last_date,
            total_transactions   = txn_count,
            total_spend          = total_spend,
            avg_order_value      = avg_order,
            total_loyalty_points = loyalty_pts,
            preferred_category   = str(pref_cat) if pref_cat else None,
            preferred_payment    = str(pref_pay) if pref_pay else None,
            is_online_buyer      = is_online,
            return_rate          = return_rate,
            data_month           = data_month,
            data_lineage         = lineage,
            cluster_id           = str(cid),   # cluster = customer
        ))

    return records


def run():
    if not RETAIL_CSV.exists():
        print(f"ERROR: Retail CSV not found at {RETAIL_CSV}")
        sys.exit(1)

    print(f"Reading {RETAIL_CSV} …")
    df = pd.read_csv(RETAIL_CSV, low_memory=False)
    print(f"  {len(df):,} transactions — "
          f"{df['customer_id'].nunique():,} customers — "
          f"{df['store_id'].nunique()} stores")

    records = build_records(df)
    print(f"Built {len(records):,} customer-store records")

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame([asdict(r) for r in records])
    out_df.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved → {OUTPUT_CSV}")


if __name__ == "__main__":
    run()
