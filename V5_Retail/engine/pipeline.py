"""Runs the whole DeltaMax flow for ONE project settings file."""
import json
import os
import time
import hashlib
from datetime import datetime

import pandas as pd

from . import __version__
from .config import load_config, resolve_path
from .loader import load_data
from . import checks, scoring, forecast, alerts, churn, notify, storage


def run_project(config_path, root, send_email=True, validate_only=False, output_dir=None, verbose=True):
    log = print if verbose else (lambda *a, **k: None)
    t0 = time.time()
    cfg = load_config(config_path, root)
    log(f"\n=== DeltaMax | project: {cfg['project']} ===")
    df, warns = load_data(cfg)
    log(f"[1/8] Loaded {len(df):,} rows, {df['data_month'].nunique()} months. "
        f"Lineage: {df['data_lineage'].value_counts().to_dict()}")
    if validate_only:
        log("[OK] Settings file and input data are valid.")
        return {"project": cfg["project"], "rows": len(df), "warnings": warns}

    base = checks.baseline_mask(df, cfg)

    rules_df, rule_hits = checks.apply_rules(df, cfg)
    df = pd.concat([df, rules_df], axis=1)
    log(f"[2/8] Business rules: {len(cfg['rules'])} rules, {int((df['rule_violations'] > 0).sum()):,} rows broke at least one")

    an = checks.anomaly_flags(df, cfg, base)
    df = pd.concat([df, an], axis=1)
    log(f"[3/8] Anomaly: flags iqr={an.iqr_flag.sum()}, iso={an.isolation_forest_flag.sum()}, "
        f"knn={an.knn_flag.sum()}, dup={an.duplicate_flag.sum()}, missing={an.missing_flag.sum()}")

    dr, drift_info = checks.drift_health(df, cfg, base)
    df = pd.concat([df, dr], axis=1)
    log(f"[4/8] Drift health by lineage: " + ", ".join(f"{k}={v['psi_health']:.2f}/{v['welch_health']:.2f}" for k, v in drift_info.items()))

    fr = checks.freshness_health(df, cfg)
    if fr is not None:
        df = pd.concat([df, fr], axis=1)
    else:
        warns.append("Freshness is NOT configured for this project: it is excluded from the trust score "
                     "(weights re-balanced) instead of being counted as perfect.")
    norm = scoring.combine(df, cfg)
    pillars = scoring.active_pillars(cfg)
    pcols = [scoring.PILLAR_COL[p] for p in pillars]
    log(f"[5/8] Trust score built from pillars {pillars}; mean = {df['overall_trust_score'].mean():.4f}")

    lineage_df = scoring.lineage_summary(df, pcols)
    monthly_df = scoring.monthly_pillars(df, pcols)
    entity_df = scoring.entity_summaries(df, cfg, pcols)

    fc_df, devs = (pd.DataFrame(), [])
    if cfg["forecast"]["enabled"]:
        fc_df, devs = forecast.run_forecasts(df, cfg, monthly_df, pcols)
        log(f"[6/8] Forecast: {fc_df['series'].nunique() if len(fc_df) else 0} series, "
            f"{len(devs)} entity/series combinations")
    else:
        log("[6/8] Forecast disabled")

    alerts_df = alerts.build_alerts(cfg, lineage_df, monthly_df, entity_df, devs, pcols)
    churn_df = churn.run_churn(df, cfg)
    log(f"[7/8] Alerts: {len(alerts_df)}" + (f"; churn scored for {len(churn_df):,} entities" if churn_df is not None else ""))

    outdir = output_dir or os.path.join(resolve_path(cfg, cfg["output_dir"]), cfg["project"])
    row_cols = ["row_id", "data_month", "data_lineage"] + [c for c in df.columns if c not in
                ("row_id", "data_month", "data_lineage")]
    tables = {"row_scores": df[row_cols], "trust_by_lineage": lineage_df, "trust_by_month": monthly_df,
              "entity_trust": entity_df, "forecasts": fc_df, "alerts": alerts_df, "churn_risk": churn_df}
    paths, st_msgs = storage.save_tables(cfg, outdir, tables)
    with open(os.path.join(outdir, "drift_summary.json"), "w") as fh:
        json.dump(drift_info, fh, indent=2)
    rep = alerts.write_report(os.path.join(outdir, "alert_report.txt"), cfg, lineage_df, alerts_df, norm, warns, rule_hits)

    email_msg = "email not requested"
    if send_email and cfg["notify"].get("email") and len(alerts_df):
        ok, email_msg = notify.send_alert_email(cfg, f"{len(alerts_df)} alert(s)", rep, paths.get("alerts"))
    elif cfg["notify"].get("email") and not len(alerts_df):
        email_msg = "no alerts, so no email sent"

    manifest = {"engine_version": __version__, "project": cfg["project"], "run_at": datetime.now().isoformat(timespec="seconds"),
                "settings_file": os.path.basename(config_path),
                "settings_hash": hashlib.sha1(open(config_path, "rb").read()).hexdigest()[:10],
                "rows": len(df), "pillars_used": pillars, "pillar_weights": {k: round(v, 4) for k, v in norm.items()},
                "mean_trust": round(float(df["overall_trust_score"].mean()), 4),
                "alerts": len(alerts_df), "rule_hits": rule_hits, "warnings": warns, "storage": st_msgs,
                "email": email_msg, "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(outdir, "run_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    log(f"[8/8] Saved {len(paths)} tables to {outdir}  ({manifest['seconds']}s). {email_msg}")
    for w in warns:
        log(f"   NOTE: {w}")
    return {"manifest": manifest, "df": df, "lineage": lineage_df, "alerts": alerts_df, "entity": entity_df,
            "forecasts": fc_df, "churn": churn_df, "outdir": outdir}
