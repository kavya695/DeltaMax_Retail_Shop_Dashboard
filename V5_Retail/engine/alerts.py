"""Turns scores and forecasts into alerts, and writes the text report."""
import numpy as np
import pandas as pd
from datetime import datetime

COLS = ["alert_level", "alert_type", "entity_type", "entity_id", "month", "metric",
        "value", "threshold", "message"]


def _a(level, typ, etype, eid, month, metric, value, thr, msg):
    return dict(alert_level=level, alert_type=typ, entity_type=etype, entity_id=eid, month=month,
                metric=metric, value=None if value is None or (isinstance(value, float) and np.isnan(value)) else round(float(value), 4),
                threshold=thr, message=msg)


def build_alerts(cfg, lineage_df, monthly_df, entity_df, devs, pcols):
    al = cfg["alerts"]
    out = []
    # 1) lineage-level trust now
    cur = lineage_df[lineage_df["data_lineage"] == "CURRENT_MONTH"]
    cur_month = monthly_df[monthly_df["data_lineage"] == "CURRENT_MONTH"]["data_month"].max() if len(monthly_df) else None
    if len(cur):
        r = cur.iloc[0]
        if r["overall_trust_score"] < al["trust_below"]:
            out.append(_a("HIGH", "TRUST_BELOW_THRESHOLD", "ALL", "ALL", cur_month, "overall_trust_score",
                          r["overall_trust_score"], al["trust_below"], "Current-month overall trust is below the threshold."))
        for p in pcols:
            if r[p] < al["pillar_below"]:
                out.append(_a("MEDIUM", "PILLAR_BELOW_THRESHOLD", "ALL", "ALL", cur_month, p, r[p], al["pillar_below"],
                              f"Current-month {p} is below the threshold."))
    # 2) month-over-month drop
    prev = lineage_df[lineage_df["data_lineage"] == "PREVIOUS_MONTH"]
    if len(cur) and len(prev):
        drop = prev.iloc[0]["overall_trust_score"] - cur.iloc[0]["overall_trust_score"]
        if drop >= al["mom_drop"]:
            out.append(_a("MEDIUM", "TRUST_DROP_MOM", "ALL", "ALL", cur_month, "overall_trust_score",
                          drop, al["mom_drop"], "Trust fell sharply versus the previous month."))
    # 3) entity-level trust (worst N only, the rest summarised in one line)
    if len(entity_df):
        e = entity_df
        cand = []
        em = e[(e["data_lineage"] == "CURRENT_MONTH")]
        for _, r in em[em["overall_trust_score"] < al["trust_below"]].iterrows():
            cand.append((r["overall_trust_score"], _a("HIGH", "ENTITY_TRUST_LOW", r["entity_type"], r["entity_id"],
                         r["data_month"], "overall_trust_score", r["overall_trust_score"], al["trust_below"],
                         f"{r['entity_type']} {r['entity_id']} has low trust this month.")))
        if "trust_current_month" in e:
            eo = e[(e["data_lineage"] == "ALL") & e["trust_current_month"].notna()]
            for _, r in eo[eo["trust_current_month"] < al["trust_below"]].iterrows():
                cand.append((r["trust_current_month"], _a("MEDIUM", "ENTITY_TRUST_LOW", r["entity_type"], r["entity_id"],
                             "CURRENT", "trust_current_month", r["trust_current_month"], al["trust_below"],
                             f"{r['entity_type']} {r['entity_id']} has low current-month trust.")))
        cand.sort(key=lambda t: t[0])
        cap = al.get("max_entity_alerts", 25)
        out += [a for _, a in cand[:cap]]
        if len(cand) > cap:
            out.append(_a("LOW", "ENTITY_TRUST_LOW_SUMMARY", "ALL", "ALL", cur_month, "entities_below_threshold",
                          len(cand), al["trust_below"],
                          f"{len(cand) - cap} more entities are below the threshold; see entity_trust.csv."))
    # 4) forecast alerts + deviation alerts
    dev_thr = cfg["forecast"]["deviation_alert_pct"]
    for d in devs:
        s = d["spec"]
        floor, ceil = s.get("floor"), s.get("ceiling")
        for m, v in zip(d["future_months"], d["forecast"]):
            if floor is not None and v < floor:
                out.append(_a("HIGH" if m == d["future_months"][0] else "MEDIUM", "FORECAST_BELOW_FLOOR",
                              d["level"], d["entity_id"], m, d["series"], v, floor,
                              f"Forecast of {d['series']} falls below its floor."))
                break
            if ceil is not None and v > ceil:
                out.append(_a("HIGH" if m == d["future_months"][0] else "MEDIUM", "FORECAST_ABOVE_CEILING",
                              d["level"], d["entity_id"], m, d["series"], v, ceil,
                              f"Forecast of {d['series']} rises above its ceiling."))
                break
        if s.get("kind") != "trust" and np.isfinite(d["deviation_pct"]) and abs(d["deviation_pct"]) >= dev_thr:
            lvl = "HIGH" if abs(d["deviation_pct"]) >= dev_thr * 1.67 else "MEDIUM"
            out.append(_a(lvl, "ACTUAL_VS_FORECAST", d["level"], d["entity_id"], d["last_month"], d["series"],
                          d["deviation_pct"], dev_thr,
                          f"Latest {d['series']} differs from what the model expected (deviation in %)."))
    df = pd.DataFrame(out, columns=COLS)
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    if len(df):
        df = df.sort_values("alert_level", key=lambda s: s.map(order)).reset_index(drop=True)
    return df


def write_report(path, cfg, lineage_df, alerts_df, norm_weights, warnings, rule_hits):
    L = [f"DeltaMax Alert Report - project: {cfg['project']}",
         f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
         "=" * 60,
         f"Pillar weights used: " + ", ".join(f"{k}={v:.2f}" for k, v in norm_weights.items()),
         "", "Trust by lineage:"]
    for _, r in lineage_df.iterrows():
        L.append(f"  {r['data_lineage']:<15} records={int(r['records']):>7}  trust={r['overall_trust_score']:.4f}")
    L += ["", f"Total alerts: {len(alerts_df)}"]
    if len(alerts_df):
        L.append(alerts_df["alert_level"].value_counts().to_string())
        L.append("")
        for _, r in alerts_df.head(40).iterrows():
            L.append(f"[{r['alert_level']}] {r['alert_type']} | {r['entity_type']} {r['entity_id']} | "
                     f"{r['month']} | {r['metric']} = {r['value']} (limit {r['threshold']})")
        if len(alerts_df) > 40:
            L.append(f"... and {len(alerts_df) - 40} more in alerts.csv")
    else:
        L.append("No alerts were generated.")
    hits = {k: v for k, v in rule_hits.items() if v}
    if hits:
        L += ["", "Business rules broken (row counts):"] + [f"  {k}: {v}" for k, v in hits.items()]
    if warnings:
        L += ["", "Notes:"] + [f"  - {w}" for w in warnings]
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return "\n".join(L)
