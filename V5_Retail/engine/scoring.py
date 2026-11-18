"""Combines the pillars into one trust score and builds the summaries."""
import pandas as pd

PILLAR_COL = {"anomaly": "anomaly_health", "drift": "drift_health",
              "business": "business_health", "freshness": "freshness_health"}


def active_pillars(cfg):
    pillars = ["anomaly", "drift", "business"]
    if cfg["freshness"]["enabled"]:
        pillars.append("freshness")
    if not cfg["drift"]["enabled"]:
        pillars.remove("drift")
    return pillars


def combine(df, cfg):
    pillars = active_pillars(cfg)
    w = {p: cfg["scoring"]["weights"][p] for p in pillars}
    tot = sum(w.values())
    norm = {p: v / tot for p, v in w.items()}
    df["overall_trust_score"] = sum(norm[p] * df[PILLAR_COL[p]] for p in pillars).clip(0, 1)
    return norm


def _agg(cols):
    d = {"records": ("row_id", "count")}
    for c in cols:
        d[c] = (c, "mean")
    d["overall_trust_score"] = ("overall_trust_score", "mean")
    return d


def lineage_summary(df, pcols):
    g = df.groupby("data_lineage").agg(**_agg(pcols)).reset_index()
    allrow = df.agg({c: "mean" for c in pcols + ["overall_trust_score"]}).to_dict()
    allrow.update(data_lineage="ALL", records=len(df))
    return pd.concat([g, pd.DataFrame([allrow])], ignore_index=True)


def monthly_pillars(df, pcols):
    return df.groupby(["data_month", "data_lineage"]).agg(**_agg(pcols)).reset_index()


def entity_summaries(df, cfg, pcols):
    hr = cfg["alerts"]["high_risk_trust"]
    parts = []
    for ent in cfg["entities"]:
        if not ent.get("summarize", True):
            continue
        col, grain = ent["column"], ent.get("grain", "month")
        keys = [col, "data_month", "data_lineage"] if grain == "month" else [col]
        spec = _agg(pcols)
        spec["high_risk_records"] = ("overall_trust_score", lambda x: int((x < hr).sum()))
        s = df.groupby(keys, dropna=True).agg(**spec).reset_index().rename(columns={col: "entity_id"})
        if grain != "month":
            cur = (df[df["data_lineage"] == "CURRENT_MONTH"].groupby(col)["overall_trust_score"]
                   .mean().rename("trust_current_month").reset_index().rename(columns={col: "entity_id"}))
            s = s.merge(cur, on="entity_id", how="left")
            s["data_month"], s["data_lineage"] = "ALL", "ALL"
        s.insert(0, "entity_type", ent["type"])
        parts.append(s)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
