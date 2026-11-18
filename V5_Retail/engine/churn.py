"""Generic repeat-purchase / inactivity risk for transaction-style data (RFM score)."""
import numpy as np
import pandas as pd


def run_churn(df, cfg):
    c = cfg["churn"]
    if not c.get("enabled"):
        return None
    ent, date_col, val = c["entity_column"], c["date_column"], c["value_column"]
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d[val] = pd.to_numeric(d[val], errors="coerce").fillna(0)
    end = d[date_col].max()
    g = d.groupby(ent).agg(purchase_count=("row_id", "count"), total_value=(val, "sum"),
                           last_activity=(date_col, "max")).reset_index().rename(columns={ent: "entity_id"})
    g["recency_days"] = (end - g["last_activity"]).dt.days.clip(lower=0)
    rec = g["recency_days"].rank(pct=True)
    freq = 1 - g["purchase_count"].rank(pct=True)
    spend = 1 - g["total_value"].rank(pct=True)
    g["churn_risk_score"] = (0.55 * rec + 0.30 * freq + 0.15 * spend).clip(0, 1)
    inactive = c.get("inactive_days", 60)
    g["inactive_flag"] = (g["recency_days"] >= inactive).astype(int)
    g["risk_level"] = np.select([g["churn_risk_score"] >= .75, g["churn_risk_score"] >= .5], ["HIGH", "MEDIUM"], "LOW")
    g.insert(0, "entity_type", c.get("entity_type", "ENTITY"))
    return g.sort_values("churn_risk_score", ascending=False)
