"""Small local API used by the Vite frontend.

The retail pipeline writes its results to CSV files.  This service exposes the
churn report to the existing frontend without putting SendGrid credentials in
browser code.
"""
from __future__ import annotations

import html
import json
import math
import os
from datetime import datetime, timezone
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import yaml
from sendgrid_config import (SendGridDeliveryError, get_sendgrid_config_status,
                             require_sendgrid_config, sendgrid_send)

ROOT = Path(__file__).resolve().parent
CHURN_PATH = ROOT / "results" / "retail" / "churn_risk.csv"
TRUST_MONTH_PATH = ROOT / "results" / "retail" / "trust_by_month.csv"
ROW_SCORE_PATH = ROOT / "results" / "retail" / "row_scores.csv"
ALERT_PATH = ROOT / "results" / "retail" / "alerts.csv"
FORECAST_PATH = ROOT / "results" / "retail" / "forecasts.csv"
HYPERPARAMS_PATH = ROOT / "results" / "retail" / "hyperparams.json"
RETAIL_CONFIG_PATH = ROOT / "configs" / "retail.yaml"
HISTORY_PATH = ROOT / "results" / "retail" / "hyperparams_history.json"
DRIFT_SUMMARY_PATH = ROOT / "results" / "retail" / "drift_summary.json"
SENT_LOG_PATH = ROOT / "results" / "retail" / "alert_sent_log.json"

# MDM output files (produced by MDM/1generate_data.py → 2run_scoring.py → 3run_golden.py)
MDM_DIR           = ROOT.parent / "MDM" / "data"
MDM_GOLDEN_PATH   = MDM_DIR / "mdm_customers_golden.csv"
MDM_SCORED_PATH   = MDM_DIR / "mdm_customers_scored.csv"
MDM_SUMMARY_PATH  = MDM_DIR / "mdm_cluster_summary.csv"
_SENT_LOG_LOCK = threading.Lock()
_CSV_CACHE_LOCK = threading.RLock()
_CSV_CACHE: dict[str, tuple[tuple[int, int], pd.DataFrame]] = {}
_CHURN_REPORT_CACHE: dict[tuple, dict] = {}


def _file_identity(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return 0, 0


def load_cached_churn_report(tier: str = "all", limit: int = 100, dashboard: bool = False) -> dict:
    """Reuse the expensive enriched churn report until source files change."""
    source_path = ROOT / "data" / "retail_shop_data.csv"
    key = (tier.lower(), int(limit), bool(dashboard),
           _file_identity(CHURN_PATH), _file_identity(source_path))
    with _CSV_CACHE_LOCK:
        cached = _CHURN_REPORT_CACHE.get(key)
    if cached is not None:
        return cached
    report = load_churn_report(tier, limit, dashboard=dashboard)
    with _CSV_CACHE_LOCK:
        _CHURN_REPORT_CACHE.clear()
        _CHURN_REPORT_CACHE[key] = report
    return report


def _cached_csv(path: Path, **read_options) -> pd.DataFrame:
    """Cache local data tables until their file timestamp or size changes."""
    stat = path.stat()
    identity = (stat.st_mtime_ns, stat.st_size)
    key = str(path.resolve()) + repr(sorted(read_options.items()))
    with _CSV_CACHE_LOCK:
        cached = _CSV_CACHE.get(key)
        if cached and cached[0] == identity:
            return cached[1]
        frame = pd.read_csv(path, **read_options)
        _CSV_CACHE[key] = (identity, frame)
        return frame


# ── MDM API ───────────────────────────────────────────────────────────────────

def load_mdm_report() -> dict:
    """Build the /api/mdm overview from the MDM pipeline output files."""
    if not MDM_GOLDEN_PATH.exists():
        return {
            "ready": False,
            "hint": "Run MDM/1generate_data.py → 2run_scoring.py → 3run_golden.py first.",
        }

    golden  = _cached_csv(MDM_GOLDEN_PATH)
    summary = _cached_csv(MDM_SUMMARY_PATH) if MDM_SUMMARY_PATH.exists() else pd.DataFrame()
    scored  = _cached_csv(MDM_SCORED_PATH)  if MDM_SCORED_PATH.exists()  else pd.DataFrame()

    total_source  = len(scored)
    total_golden  = len(golden)
    total_clusters = int(summary["record_count"].gt(1).sum()) if not summary.empty else 0

    # Overall trust
    overall_trust = round(float(golden["trust_score"].mean()), 1) if "trust_score" in golden.columns else 0.0

    # Trust score pillars (avg across golden records)
    def _avg(col: str, df: pd.DataFrame) -> float:
        return round(float(df[col].mean()), 1) if col in df.columns and not df[col].isna().all() else 0.0

    pillars = {
        "rules":   _avg("rules_score",   scored) if not scored.empty else 0.0,
        "drift":   _avg("drift_score",   scored) if not scored.empty else 0.0,
        "anomaly": _avg("anomaly_score", scored) if not scored.empty else 0.0,
    }

    # Source-system (store) pipeline breakdown
    pipeline = []
    if not scored.empty and "source_system" in scored.columns:
        for store, grp in scored.groupby("source_system"):
            is_golden_col = grp["is_golden"].fillna(False).astype(bool) if "is_golden" in grp.columns else pd.Series(False, index=grp.index)
            won = int(is_golden_col.sum())
            pipeline.append({
                "source":    str(store),
                "records":   len(grp),
                "golden_won": won,
                "win_pct":   round(won / len(grp) * 100, 1) if len(grp) else 0.0,
            })

    # Trust score distribution buckets
    ts = golden["trust_score"].dropna() if "trust_score" in golden.columns else pd.Series(dtype=float)
    distribution = [
        {"label": "0–60",   "count": int((ts < 60).sum())},
        {"label": "60–70",  "count": int(((ts >= 60)  & (ts < 70)).sum())},
        {"label": "70–80",  "count": int(((ts >= 70)  & (ts < 80)).sum())},
        {"label": "80–90",  "count": int(((ts >= 80)  & (ts < 90)).sum())},
        {"label": "90–100", "count": int((ts >= 90).sum())},
    ]

    # Records needing review (lowest trust golden records)
    review = []
    if not golden.empty:
        low = golden.nsmallest(15, "trust_score") if "trust_score" in golden.columns else golden.head(15)
        for _, row in low.iterrows():
            cid = str(row.get("customer_id", row.get("cluster_id", "—")))
            display = _customer_display(cid)
            review.append({
                "cluster_id":          cid,
                "name":                f"{display['first_name']} {display['last_name']}",
                "email":               display["email"],
                "source":              str(row.get("source_system", "—")),
                "trust":               round(float(row.get("trust_score", 0) or 0), 1),
                "store_city":          str(row.get("store_city", "—")),
                "last_purchase_date":  str(row.get("last_purchase_date", "—") or "—"),
                "total_transactions":  int(row.get("total_transactions", 0) or 0),
                "total_spend":         round(float(row.get("total_spend", 0) or 0), 2),
            })

    # Match confidence: % of golden records with trust_score >= 80
    match_conf = round(float((ts >= 80).mean() * 100), 1) if len(ts) else 0.0

    return {
        "ready": True,
        "overall_trust": overall_trust,
        "metrics": {
            "source_records":     total_source,
            "golden_records":     total_golden,
            "duplicate_clusters": total_clusters,
            "match_confidence":   match_conf,
        },
        "pillars":      pillars,
        "pipeline":     pipeline,
        "distribution": distribution,
        "review":       review,
    }


def _customer_display(customer_id: str) -> dict:
    """Generate consistent display name and email for a customer_id.
    Uses a deterministic hash so the same customer always gets the same name."""
    import hashlib
    h = int(hashlib.md5(customer_id.encode()).hexdigest(), 16)
    first_names = ["Priya","Rahul","Anjali","Vikram","Sneha","Arjun","Meera","Rohit",
                   "Pooja","Kiran","Amit","Sara","Deepa","Suresh","Nisha","Ravi",
                   "Kavya","Ajay","Sunita","Manoj","Divya","Nikhil","Rekha","Sanjay"]
    last_names  = ["Sharma","Patel","Reddy","Nair","Gupta","Kumar","Joshi","Singh",
                   "Mehta","Iyer","Rao","Pillai","Das","Verma","Shah","Murthy",
                   "Agarwal","Bose","Khanna","Mishra","Kapoor","Tiwari","Desai","Jain"]
    first = first_names[h % len(first_names)]
    last  = last_names[(h // len(first_names)) % len(last_names)]
    email = f"{first.lower()}.{last.lower()}{h % 1000}@gmail.com"
    phone = f"{(h % 800) + 200}-{(h % 900) + 100}-{(h % 9000) + 1000}"
    return {"first_name": first, "last_name": last, "email": email, "phone": phone}


def load_mdm_cluster(cluster_id: str) -> dict:
    """Return cluster detail in the shape the MS popup expects."""
    if not MDM_SCORED_PATH.exists():
        return {"ready": False, "hint": "MDM pipeline not yet run."}

    scored = _cached_csv(MDM_SCORED_PATH)
    cluster_col = "cluster_id" if "cluster_id" in scored.columns else "customer_id"
    rows = scored[scored[cluster_col].astype(str) == str(cluster_id)]

    if rows.empty:
        return {"ready": False, "hint": f"Cluster {cluster_id!r} not found."}

    def _build_record(row, is_golden: bool) -> dict:
        cid       = str(row.get("customer_id", cluster_id))
        display   = _customer_display(cid)
        store     = str(row.get("source_system", row.get("store_id", "—")))
        city      = str(row.get("store_city", "—") or "—")
        trust     = round(float(row.get("trust_score",  0) or 0), 1)
        rules     = round(float(row.get("rules_score",  0) or 0), 1)
        drift     = round(float(row.get("drift_score",  0) or 0), 1)
        anomaly   = round(float(row.get("anomaly_score",0) or 0), 1)
        spend     = round(float(row.get("total_spend",  0) or 0), 2)
        txns      = int(row.get("total_transactions", 0) or 0)
        pref_cat  = str(row.get("preferred_category","—") or "—")
        pref_pay  = str(row.get("preferred_payment", "—") or "—")
        last_date = str(row.get("last_purchase_date","—") or "—")
        surv      = str(row.get("survivorship_reason","") or "")
        reason    = surv if surv else ("Highest trust score and match confidence" if is_golden
                                       else "Lower trust — not selected as golden record")
        return {
            "first_name":        display["first_name"],
            "last_name":         display["last_name"],
            "source_system":     store,
            "email":             display["email"],
            "phone":             display["phone"],
            "city":              city,
            "state":             "",
            "zip":               "",
            "reason_selected":   reason,
            "trust_score":       trust,
            "rules_score":       rules,
            "drift_score":       drift,
            "anomaly_score":     anomaly,
            "customer_id":       cid,
            "store_id":          str(row.get("store_id", "—")),
            "total_transactions": txns,
            "total_spend":       spend,
            "preferred_category": pref_cat,
            "preferred_payment": pref_pay,
            "last_purchase_date": last_date,
            "is_golden":         is_golden,
        }

    all_records = [_build_record(row, bool(row.get("is_golden", False)))
                   for _, row in rows.iterrows()]

    golden_rec = next((r for r in all_records if r["is_golden"]), all_records[0])
    duplicates = [r for r in all_records if not r["is_golden"]]

    return {
        "ready":               True,
        "cluster_id":          cluster_id,
        "total_records":       len(all_records),
        "golden_record":       golden_rec,
        "duplicate_candidates": duplicates,
        # Legacy fields kept for compatibility
        "records":             all_records,
        "golden":              golden_rec,
        "record_count":        len(all_records),
    }


def load_migration_report() -> dict:
    """Azure → GCP migration overview using real retail trust scores."""
    if not ROW_SCORE_PATH.exists():
        return {"ready": False, "hint": "Run 03_Trust_Score.py first."}

    scores = _cached_csv(ROW_SCORE_PATH)
    if scores.empty:
        return {"ready": False, "hint": "No scored data available."}

    # "Before" = all rows (pre-migration state)
    before_trust = round(float(scores["overall_trust_score"].mean() * 100), 1)

    # "After" = rows with no anomaly flags (post-clean migration)
    after_df = scores[scores["overall_anomaly_risk"].fillna(0) <= 0].copy()
    after_trust    = round(float(after_df["overall_trust_score"].mean() * 100), 1) if len(after_df) else before_trust
    records_total  = len(scores)
    records_mig    = len(after_df)
    records_disc   = records_total - records_mig

    # Pillar averages after migration (anomaly_health is 100% because anomalous rows removed)
    def _avg(col, df):
        return round(float(df[col].mean() * 100), 1) if col in df.columns and len(df) else 0.0

    pillar_after = {
        "anomaly":  _avg("anomaly_health",  after_df),
        "drift":    _avg("drift_health",    after_df),
        "business": _avg("business_health", after_df),
        "freshness":_avg("freshness_health",after_df),
    }
    pillar_before = {
        "anomaly":  _avg("anomaly_health",  scores),
        "drift":    _avg("drift_health",    scores),
        "business": _avg("business_health", scores),
        "freshness":_avg("freshness_health",scores),
    }

    # Per-lineage trust lift
    lineage_lift = []
    for lin, grp in scores.groupby("data_lineage", sort=False):
        if str(lin) == "ALL":
            continue
        clean = grp[grp["overall_anomaly_risk"].fillna(0) <= 0]
        b = round(float(grp["overall_trust_score"].mean() * 100), 1)
        a = round(float(clean["overall_trust_score"].mean() * 100), 1) if len(clean) else b
        display = {"HISTORICAL": "Historical", "PREVIOUS_MONTH": "Previous month", "CURRENT_MONTH": "Current month"}.get(str(lin), str(lin))
        lineage_lift.append({"lineage": display, "before": b, "after": a, "lift": round(a - b, 1)})

    # Monthly trust trend
    monthly_trend = []
    if "data_month" in scores.columns:
        for month, mgrp in scores.groupby(scores["data_month"].astype(str), sort=True):
            mclean = mgrp[mgrp["overall_anomaly_risk"].fillna(0) <= 0]
            monthly_trend.append({
                "month": month,
                "before": round(float(mgrp["overall_trust_score"].mean() * 100), 1),
                "after":  round(float(mclean["overall_trust_score"].mean() * 100), 1) if len(mclean) else 0,
            })

    return {
        "ready":             True,
        "source_cloud":      "Azure",
        "target_cloud":      "GCP",
        "status":            "SUCCESS",
        "demo":              True,
        "run_id":            "RETAIL-AZURE-GCP",
        "trust_before":      before_trust,
        "trust_after":       after_trust,
        "trust_delta":       round(after_trust - before_trust, 1),
        "records_total":     records_total,
        "records_migrated":  records_mig,
        "records_discarded": records_disc,
        "retained_pct":      round(records_mig / records_total * 100, 1),
        "pillar_before":     pillar_before,
        "pillar_after":      pillar_after,
        "lineage_lift":      lineage_lift,
        "monthly_trend":     monthly_trend,
        "pipeline_steps": [
            {"label": "Pre-migration trust check",  "status": "done"},
            {"label": "Clean (remove anomalies)",   "status": "done"},
            {"label": "Migrate Azure → GCP",        "status": "done"},
            {"label": "Post-migration trust check", "status": "done"},
        ],
    }


def load_migration_discarded() -> dict:
    """Return sample of discarded (anomalous) records."""
    if not ROW_SCORE_PATH.exists():
        return {"ready": False, "hint": "No data available."}
    scores = _cached_csv(ROW_SCORE_PATH)
    disc = scores[scores["overall_anomaly_risk"].fillna(0) > 0].sort_values("overall_trust_score", ascending=True)
    rows = []
    for _, row in disc.iterrows():
        trust = round(float(row.get("overall_trust_score", 0) * 100), 1)
        risk_level = "CRITICAL" if trust < 50 else "HIGH" if trust < 60 else "MEDIUM" if trust < 80 else "LOW"
        rows.append({
            "record_id":    int(row.get("row_id", 0)),
            "customer_id":  str(row.get("customer_id", "—")),
            "lineage":      str(row.get("data_lineage", "—")),
            "anomaly_risk": round(float(row.get("overall_anomaly_risk", 0) * 100), 1),
            "trust_score":  trust,
            "anomaly_health": round(float(row.get("anomaly_health", 0) * 100), 1),
            "drift_health": round(float(row.get("drift_health", 0) * 100), 1),
            "business_health": round(float(row.get("business_health", 0) * 100), 1),
            "risk_level": risk_level,
            "reason":       str(row.get("rules_broken", "Anomaly detected") or "Anomaly detected"),
        })
    return {"ready": True, "total": int((scores["overall_anomaly_risk"].fillna(0) > 0).sum()), "records": rows}


RETAIL_HYPERPARAM_DEFAULTS = {
    # Anomaly detector weights (must sum to 1.0 — five detectors only, no ARIMA)
    "W_ISO": 0.15, "W_IQR": 0.30, "W_MISSING": 0.20,
    "W_DUPLICATE": 0.20, "W_KNN": 0.15,
    # Trust pillar weights (must sum to 1.0)
    "WEIGHT_ANOMALY_HEALTH": 0.40, "WEIGHT_DRIFT_HEALTH": 0.25,
    "WEIGHT_BUSINESS_HEALTH": 0.20, "WEIGHT_FRESHNESS_HEALTH": 0.15,
    # Detection model settings
    "IQR_MULTIPLIER": 5.0,
    "ISO_N_ESTIMATORS": 100.0, "ISO_DECISION_THRESHOLD": 0.0,
    "KNN_K": 5.0, "KNN_PERCENTILE": 0.95,
    # ARIMA settings
    "ARIMA_SIGMA_K": 3.0, "ARIMA_MIN_COL_FLAGS": 2.0, "ARIMA_MIN_TRAIN_POINTS": 8.0,
    # Freshness & drift
    "FRESHNESS_TAU": 5.0,
    "WEIGHT_MAHAL": 0.70, "WEIGHT_WELCH": 0.10, "WEIGHT_PSI": 0.20,
    # Business rules
    "MAX_POSSIBLE_FLAGS": 22.0,
}


def _hyperparams() -> dict:
    params = dict(RETAIL_HYPERPARAM_DEFAULTS)
    if HYPERPARAMS_PATH.exists():
        stored = json.loads(HYPERPARAMS_PATH.read_text(encoding="utf-8"))
        params.update({key: float(value) for key, value in stored.get("params", {}).items() if key in params})
    return params


def _hyperparams_to_runtime(params: dict) -> dict:
    return {"anomaly": {"iqr_multiplier": params["IQR_MULTIPLIER"], "weights": {
                "isolation_forest": params["W_ISO"], "iqr": params["W_IQR"],
                "missing": params["W_MISSING"], "duplicate": params["W_DUPLICATE"], "knn": params["W_KNN"]}},
            "scoring": {"weights": {"anomaly": params["WEIGHT_ANOMALY_HEALTH"], "drift": params["WEIGHT_DRIFT_HEALTH"],
                                      "business": params["WEIGHT_BUSINESS_HEALTH"], "freshness": params["WEIGHT_FRESHNESS_HEALTH"]}},
            "freshness": {"tau": params["FRESHNESS_TAU"]}}


def get_hyperparams() -> dict:
    params = _hyperparams()
    return {"success": True, "project": "retail", "params": params,
            "defaults": RETAIL_HYPERPARAM_DEFAULTS,
            "supported_params": list(RETAIL_HYPERPARAM_DEFAULTS),
            "description": "Retail anomaly, trust-pillar, and freshness parameters."}


def save_hyperparams(payload: dict) -> dict:
    incoming = payload.get("params", {})
    params = dict(RETAIL_HYPERPARAM_DEFAULTS)
    params.update(_hyperparams())
    for key, value in incoming.items():
        if key not in params:
            continue
        number = float(value)
        if key.startswith("W_") and not 0 <= number <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
        if key.startswith("WEIGHT_") and not 0 <= number <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
        if key == "IQR_MULTIPLIER" and not 1 <= number <= 15:
            raise ValueError("IQR_MULTIPLIER must be between 1 and 15")
        if key == "FRESHNESS_TAU" and not 1 <= number <= 30:
            raise ValueError("FRESHNESS_TAU must be between 1 and 30")
        if key == "KNN_PERCENTILE" and not 0.9 <= number <= 0.999:
            raise ValueError("KNN_PERCENTILE must be between 0.9 and 0.999")
        params[key] = round(number, 4)
    # Do not enforce weight sums at save time — the pipeline normalises them on run.
    # This lets users freely adjust individual sliders without needing all to sum exactly.
    # Trust pillar weights — also saved as-is; pipeline normalises on run.
    # (Removed strict sum check to allow free slider adjustment.)
    runtime = {"params": params, **_hyperparams_to_runtime(params)}
    HYPERPARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HYPERPARAMS_PATH.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else []
    history.insert(0, {"timestamp": datetime.now(timezone.utc).isoformat(), "name": payload.get("name", "Retail config"), "params": params})
    HISTORY_PATH.write_text(json.dumps(history[:25], indent=2), encoding="utf-8")
    return {"success": True, **get_hyperparams()}


def load_home_report() -> dict:
    """Build the dashboard overview from the latest retail pipeline outputs."""
    if not TRUST_MONTH_PATH.exists():
        return {"ready": False, "hint": "Run 03_Trust_Score.py before opening the overview."}

    monthly = pd.read_csv(TRUST_MONTH_PATH)
    monthly = monthly.sort_values("data_month")
    current = monthly.iloc[-1]
    prior = monthly.iloc[:-1]
    current_month = str(current["data_month"])
    current_rows = monthly[monthly["data_month"].astype(str) == current_month]
    current_records = int(current["records"])

    flagged = None
    if ROW_SCORE_PATH.exists():
        scores = pd.read_csv(ROW_SCORE_PATH, usecols=lambda col: col in {
            "data_month", "overall_anomaly_risk", "rule_violations", "freshness_health", "overall_trust_score"
        })
        current_scores = scores[scores["data_month"].astype(str) == current_month]
        flagged = int((
            (current_scores.get("overall_anomaly_risk", 0) > 0) |
            (current_scores.get("rule_violations", pd.Series(0, index=current_scores.index)).fillna(0) > 0) |
            (current_scores.get("freshness_health", pd.Series(1, index=current_scores.index)).fillna(1) < 0.75)
        ).sum())

    recent_alerts = []
    if ALERT_PATH.exists():
        alerts = pd.read_csv(ALERT_PATH).head(8)
        for _, row in alerts.iterrows():
            recent_alerts.append({
                "severity": str(row.get("alert_level", "MEDIUM")),
                "metric": str(row.get("metric", "Retail trust")),
                "month": str(row.get("month", current_month)),
                "trigger": str(row.get("alert_type", "Pipeline alert")),
            })

    return {
        "ready": True,
        "last_modified": TRUST_MONTH_PATH.stat().st_mtime,
        "metrics": {
            "overall_trust": round(float(current["overall_trust_score"]) * 100, 2),
            "prev_months_avg_trust": round(float(prior["overall_trust_score"].mean()) * 100, 2) if not prior.empty else None,
            "prev_months_range": ", ".join(prior["data_month"].astype(str).tolist()),
            "prev_months_count": len(prior),
            "current_month_records": current_records,
            "current_month_label": current_month,
            "current_month_flagged": flagged,
            "datasets_monitored": current_records,
        },
        "pillars": {
            "anomaly_health": round(float(current["anomaly_health"]) * 100, 2),
            "drift_health": round(float(current["drift_health"]) * 100, 2),
            "business_health": round(float(current["business_health"]) * 100, 2),
            "freshness_health": round(float(current["freshness_health"]) * 100, 2),
        },
        "trend": {
            "labels": monthly["data_month"].astype(str).tolist(),
            "actual": (monthly["overall_trust_score"] * 100).round(2).tolist(),
        },
        "use_cases": [{"name": "Retail Trust Engine", "score": round(float(current["overall_trust_score"]) * 100, 2)}],
        "recent_alerts": recent_alerts,
        "arima_run_available": False,
    }


def _score_frame() -> pd.DataFrame:
    if not ROW_SCORE_PATH.exists():
        return pd.DataFrame()
    return _cached_csv(ROW_SCORE_PATH)


def _retail_config() -> dict:
    if not RETAIL_CONFIG_PATH.exists():
        return {}
    # Use the same defaults and saved tuning overrides as the scoring engine.
    from engine.config import load_config
    return load_config(str(RETAIL_CONFIG_PATH), str(ROOT))


def _json_list(value: object) -> list:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return []
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


# Keep the existing Retail risk bands in one place so every API response uses
# the same classification. These are the bands that were previously applied
# independently by the table and record-detail handlers.
def _retail_risk_severity(risk_percent: float) -> str:
    if risk_percent >= 50:
        return "HIGH"
    if risk_percent >= 20:
        return "MEDIUM"
    return "LOW"


SECURITY_CATEGORIES = (
    "Sensitive data exposure",
    "Fraud / abuse signals",
    "Tamper / integrity",
)

SENSITIVE_COLUMN_TYPES = {
    "email": "email", "phone": "phone", "mobile": "phone",
    "address": "address", "ssn": "ssn", "social_security": "ssn",
    "pan": "pan", "card_number": "pan", "credit_card": "pan",
    "api_key": "secret", "secret": "secret", "password": "secret",
}


def _security_severity(risk_percent: float) -> str:
    if risk_percent >= 75:
        return "critical"
    if risk_percent >= 50:
        return "high"
    if risk_percent >= 20:
        return "medium"
    return "low"


def _security_row_findings(row: pd.Series, sensitive_columns: list[tuple[str, str]]) -> list[dict]:
    """Describe observable retail data exposure and integrity signals, not confirmed attacks."""
    findings = []
    for column, kind in sensitive_columns:
        value = row.get(column)
        if pd.notna(value) and str(value).strip() and str(value).strip().lower() not in {"nan", "none", "null"}:
            severity = "critical" if kind in {"ssn", "pan", "secret"} else "high"
            findings.append({
                "type": kind, "category": "Sensitive data exposure", "severity": severity,
                "scanner": "Retail schema scan",
                "detail": f"A non-empty value is present in the sensitive field '{column}'. The value is not included in this report.",
            })

    risk = pd.to_numeric(row.get("overall_anomaly_risk", 0), errors="coerce")
    risk = float(risk) if pd.notna(risk) else 0.0
    if risk > 0:
        detector_names = []
        for name, column in RETAIL_DETECTOR_COLUMNS.items():
            flagged = pd.to_numeric(row.get(column, 0), errors="coerce")
            if pd.notna(flagged) and float(flagged) > 0:
                detector_names.append(name)
        detector_text = ", ".join(detector_names) if detector_names else "retail anomaly model"
        kind = "extreme_outlier_value" if "IQR" in detector_names else "retail_anomaly_signal"
        findings.append({
            "type": kind, "category": "Fraud / abuse signals", "severity": _security_severity(risk * 100),
            "scanner": "Retail anomaly screening",
            "detail": f"The retail model raised an anomaly signal ({risk * 100:.1f}% risk) using: {detector_text}. This is a review signal, not a confirmed fraud event.",
        })

    duplicate = pd.to_numeric(row.get("duplicate_flag", 0), errors="coerce")
    if pd.notna(duplicate) and float(duplicate) > 0:
        matches = pd.to_numeric(row.get("duplicate_match_count", 0), errors="coerce")
        matches = int(matches) if pd.notna(matches) else 0
        findings.append({
            "type": "mass_duplicate_value", "category": "Tamper / integrity", "severity": "medium",
            "scanner": "Retail duplicate check",
            "detail": f"The duplicate check matched this transaction to {max(1, matches)} other record(s). Review the source rows before treating it as tampering.",
        })

    violations = pd.to_numeric(row.get("rule_violations", 0), errors="coerce")
    if pd.notna(violations) and float(violations) > 0:
        rule_text = str(row.get("rules_broken", "") or "").strip()
        findings.append({
            "type": "retail_rule_integrity_signal", "category": "Tamper / integrity", "severity": "medium",
            "scanner": "Retail business-rule check",
            "detail": f"{int(violations)} business-rule check(s) failed. {rule_text}".strip(),
        })
    return findings


def load_security_report() -> dict:
    """Build the Security Posture payload from available retail score data."""
    scores = _score_frame()
    if scores.empty:
        return {"ready": False, "hint": "Run 03_Trust_Score.py before opening Security Posture."}

    sensitive_columns = []
    for column in scores.columns:
        normalized = str(column).lower().replace("-", "_").replace(" ", "_")
        kind = next((value for key, value in SENSITIVE_COLUMN_TYPES.items()
                     if normalized == key or normalized.endswith("_" + key) or key in normalized), None)
        if kind:
            sensitive_columns.append((column, kind))

    records_with_findings = 0
    by_category = {category: 0 for category in SECURITY_CATEGORIES}
    by_severity = {severity: 0 for severity in ("critical", "high", "medium", "low")}
    examples_by_category = {category: [] for category in SECURITY_CATEGORIES}
    worst_records = []
    sensitive_column_counts = Counter()

    for _, row in scores.iterrows():
        findings = _security_row_findings(row, sensitive_columns)
        if not findings:
            continue
        records_with_findings += 1
        for column, _kind in sensitive_columns:
            value = row.get(column)
            if pd.notna(value) and str(value).strip() and str(value).strip().lower() not in {"nan", "none", "null"}:
                sensitive_column_counts[column] += 1
        for finding in findings:
            by_category[finding["category"]] += 1
            if len(examples_by_category[finding["category"]]) < 12:
                examples_by_category[finding["category"]].append({
                    "record_id": int(row.get("row_id", 0)),
                    "type": finding["type"],
                    "detail": finding["detail"],
                    "severity": finding["severity"],
                })

        severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        primary = max(findings, key=lambda finding: severity_order.get(finding["severity"], 0))
        risk = pd.to_numeric(row.get("overall_anomaly_risk", 0), errors="coerce")
        risk = float(risk) if pd.notna(risk) else 0.0
        by_severity[primary["severity"]] += 1
        worst_records.append({
            "record_id": int(row.get("row_id", 0)),
            "businessID": str(row.get("customer_id", "Unknown")) if pd.notna(row.get("customer_id")) else "Unknown",
            "security_score": round(max(0.0, 100.0 - risk * 100), 1),
            "category": primary["category"],
            "severity": primary["severity"],
        })

    total = int(len(scores))
    exposure_rate = round(records_with_findings / total * 100, 1) if total else 0.0
    worst_records.sort(key=lambda record: record["security_score"])
    by_column = []
    for column, kind in sensitive_columns:
        count = int(sensitive_column_counts[column])
        if count:
            by_column.append({
                "column": str(column), "type": kind, "count": count,
                "description": f"Non-empty values found during the retail schema scan for {column}.",
            })
    by_column.sort(key=lambda item: item["count"], reverse=True)

    return {
        "ready": True,
        "overall_score": round(max(0.0, 100.0 - exposure_rate), 1),
        "total_records": total,
        "records_with_findings": records_with_findings,
        "exposure_rate_pct": exposure_rate,
        "by_category": by_category,
        "by_severity": by_severity,
        "by_column": by_column,
        "examples_by_category": examples_by_category,
        "worst_records": worst_records[:25],
        "scan_scope": "Retail score output: sensitive-field names and retail anomaly, duplicate, and business-rule signals. Findings are review indicators, not confirmed security incidents.",
    }


def load_security_finding(record_id: str, finding_type: str = "all") -> dict:
    """Return the detail modal payload for a unique retail row_id."""
    scores = _score_frame()
    if scores.empty:
        return {"ready": False, "error": "Retail score data is unavailable."}
    matches = scores[scores["row_id"].astype(str) == str(record_id)] if "row_id" in scores else pd.DataFrame()
    if matches.empty and "record_id" in scores:
        matches = scores[scores["record_id"].astype(str) == str(record_id)]
    if matches.empty:
        return {"ready": False, "error": "That retail record was not found."}

    row = matches.iloc[0]
    sensitive_columns = []
    for column in scores.columns:
        normalized = str(column).lower().replace("-", "_").replace(" ", "_")
        kind = next((value for key, value in SENSITIVE_COLUMN_TYPES.items()
                     if normalized == key or normalized.endswith("_" + key) or key in normalized), None)
        if kind:
            sensitive_columns.append((column, kind))
    findings = _security_row_findings(row, sensitive_columns)
    if finding_type != "all":
        selected = [finding for finding in findings if finding["type"] == finding_type]
        if selected:
            findings = selected + [finding for finding in findings if finding not in selected]
    if not findings:
        return {"ready": False, "error": "No security review signals were found for this record."}

    primary = findings[0]
    customer_id = row.get("customer_id", "Unknown")
    trust = pd.to_numeric(row.get("overall_trust_score", 0), errors="coerce")
    primary = {
        **primary,
        "record_id": int(row.get("row_id", 0)),
        "businessID": str(customer_id) if pd.notna(customer_id) else "Unknown",
        "lineage": str(row.get("data_lineage", "Unknown")),
        "overall_dataset_trust": round(float(trust) * 100, 1) if pd.notna(trust) else 0.0,
    }
    return {
        "ready": True,
        "primary": primary,
        "related_findings": findings[1:],
        "total_findings_this_record": len(findings),
    }


RETAIL_DETECTOR_COLUMNS = {
    "Isolation Forest": "isolation_forest_flag",
    "IQR": "iqr_flag",
    "KNN": "knn_flag",
    "Missing Data": "missing_flag",
    "Duplicate": "duplicate_flag",
}


RETAIL_SCHEMA_TYPES = {
    "batch_id": "text", "record_id": "integer", "transaction_id": "text",
    "sale_date": "date", "data_month": "month", "data_lineage": "text",
    "store_id": "text", "store_city": "text", "customer_id": "text",
    "product_id": "text", "product_name": "text", "category": "text",
    "quantity": "number", "unit_price": "number", "discount_pct": "number",
    "discount_amount": "number", "total_amount": "number", "payment_method": "text",
    "stock_at_start": "number", "stock_sold": "number", "stock_remaining": "number",
    "supplier_id": "text", "is_return": "boolean", "expected_arrival_date": "date",
    "ingestion_date": "date", "freshness_days_late": "number", "is_anomalous_delay": "boolean",
    "loaded_at": "date", "loyalty_points": "number", "online_order_flag": "boolean",
}

RETAIL_SCHEMA_FIELD_GROUPS = {
    "Transaction information": ["batch_id", "record_id", "transaction_id", "sale_date", "data_month", "data_lineage", "payment_method", "is_return", "loaded_at"],
    "Shop information": ["store_id", "store_city"],
    "Customer information": ["customer_id", "loyalty_points", "online_order_flag"],
    "Product information": ["product_id", "product_name", "category", "supplier_id", "unit_price", "price_ratio"],
    "Sales and discount": ["quantity", "discount_pct", "discount_amount", "total_amount"],
    "Inventory": ["stock_at_start", "stock_sold", "stock_remaining"],
    "Data quality and freshness": ["expected_arrival_date", "ingestion_date", "freshness_days_late", "is_anomalous_delay"],
    "Trust and risk": ["anomaly_health", "drift_health", "business_health", "freshness_health", "overall_trust_score", "overall_anomaly_risk"],
}
RETAIL_CALCULATED_SCHEMA_FIELDS = {
    "price_ratio", "anomaly_health", "drift_health", "business_health",
    "freshness_health", "overall_trust_score", "overall_anomaly_risk",
}


def _retail_schema_for_record(row: pd.Series, config: dict) -> dict:
    """Validate a source transaction against the Retail input schema contract."""
    input_path = Path(str((config.get("input") or {}).get("path") or "data/retail_shop_data.csv"))
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    if not input_path.exists():
        return {"available": False, "source": input_path.name, "total_issues": 0,
                "counts": {"missing": 0, "unexpected": 0, "type_mismatches": 0, "invalid_values": 0},
                "columns": [], "error": "The configured Retail source file is not available."}

    source = _cached_csv(input_path, dtype=str, keep_default_na=False, low_memory=False)
    keys = (config.get("duplicate_key") or [])
    selected = pd.DataFrame()
    row_id = pd.to_numeric(row.get("row_id"), errors="coerce")
    if not pd.isna(row_id) and 0 <= int(row_id) < len(source):
        candidate = source.iloc[[int(row_id)]]
        first_key = next((key for key in keys if key in source.columns and key in row.index), None)
        if first_key is None or str(candidate.iloc[0].get(first_key, "")) == str(row.get(first_key, "")):
            selected = candidate
    for key in keys:
        if not selected.empty:
            break
        if key in source.columns and key in row.index:
            value = str(row.get(key) or "")
            if value:
                selected = source[source[key].astype(str) == value]
                if not selected.empty:
                    break
    source_row = selected.iloc[0].to_dict() if not selected.empty else {}
    if not source_row:
        return {"available": False, "source": input_path.name, "total_issues": 0,
                "counts": {"missing": 0, "unexpected": 0, "type_mismatches": 0, "invalid_values": 0},
                "columns": [], "error": "The selected transaction could not be matched to the configured Retail source row."}
    actual_headers = set(map(str, source.columns))
    expected_headers = set(RETAIL_SCHEMA_TYPES)
    missing_headers = sorted(expected_headers - actual_headers)
    unexpected_headers = sorted(actual_headers - expected_headers)
    required = set(config.get("features") or []) | set(config.get("not_null") or []) | set(keys)
    required |= {spec.get("column") for spec in config.get("entities", []) if spec.get("column")}
    columns_cfg = config.get("columns") or {}
    required |= {columns_cfg.get(name) for name in ("month", "date", "lineage") if columns_cfg.get(name)}
    freshness_cfg = config.get("freshness") or {}
    required |= {freshness_cfg.get(name) for name in ("expected_date_column", "actual_date_column", "days_late_column")
                 if freshness_cfg.get(name)}
    columns = []
    missing_values, type_mismatches, invalid_values = [], [], []

    def actual_type(value: str, expected: str) -> str:
        if value == "":
            return "null"
        if expected == "text":
            return "text"
        if expected == "boolean":
            return "boolean" if value.strip().lower() in {"true", "false", "0", "1"} else "text"
        if expected == "date":
            return "date" if not pd.isna(pd.to_datetime(value, errors="coerce")) else "text"
        if expected == "month":
            return "month" if pd.Series([value]).str.fullmatch(r"\d{4}-\d{2}").iloc[0] else "text"
        parsed = pd.to_numeric(value, errors="coerce")
        if pd.isna(parsed) or not math.isfinite(float(parsed)):
            return "text"
        return "integer" if float(parsed).is_integer() else "number"

    for name, expected in RETAIL_SCHEMA_TYPES.items():
        if name not in actual_headers:
            status, actual = "missing column", "missing"
            missing_values.append(name)
        else:
            value = str(source_row.get(name, ""))
            actual = actual_type(value, expected)
            if value == "":
                if name in required:
                    status = "required value missing"
                    missing_values.append(name)
                else:
                    status = "optional value empty"
            elif expected == "number" and actual == "text":
                status = "invalid numeric value"
                invalid_values.append(name)
            elif actual not in {expected, "integer" if expected == "number" else expected}:
                status = "type mismatch"
                type_mismatches.append(name)
            else:
                status = "valid"
        columns.append({"name": name, "value": source_row.get(name, None), "expected_type": expected,
                        "actual_type": actual, "status": status,
                        "issue": status in {"missing column", "required value missing", "invalid numeric value", "type mismatch"}})
    for name in unexpected_headers:
        columns.append({"name": name, "value": source_row.get(name), "expected_type": "not in Retail schema",
                        "actual_type": "text" if source_row.get(name) else "null",
                        "status": "unexpected column", "issue": True})
    counts = {"missing": len(missing_values), "unexpected": len(unexpected_headers),
              "type_mismatches": len(type_mismatches), "invalid_values": len(invalid_values)}
    return {"available": bool(source_row), "source": input_path.name,
            "total_issues": sum(counts.values()), "counts": counts,
            "missing_fields": sorted(set(missing_values)), "unexpected_fields": unexpected_headers,
            "type_mismatches": sorted(set(type_mismatches)), "invalid_values": sorted(set(invalid_values)),
            "columns": columns, "raw_record": source_row}


def _retail_schema_field_groups(raw_record: dict, scored_record: dict, schema_validation: dict,
                                business_rule_checks: list[dict], rule_field_map: dict) -> dict:
    """Build grouped, field-level schema presentation from saved Retail results."""
    schema_columns = {str(column.get("name")): column for column in schema_validation.get("columns", [])}
    schema_reasons: dict[str, list[str]] = {}
    for name, column in schema_columns.items():
        if column.get("issue"):
            schema_reasons.setdefault(name, []).append(f"Schema validation: {column.get('status', 'invalid value')}.")

    rule_reasons: dict[str, list[str]] = {}
    rule_by_name = {str(rule.get("key", "")): rule for rule in business_rule_checks}
    for rule_name, rule in rule_by_name.items():
        if not rule.get("fired"):
            continue
        for field in rule_field_map.get(rule_name, []):
            condition = str(rule.get("configured_condition") or rule.get("threshold") or "configured Retail rule")
            rule_reasons.setdefault(field, []).append(f"{rule.get('label', rule_name)}: {condition}.")

    available_names = set(map(str, raw_record.keys()))
    # Only expose derived fields that the saved pipeline actually wrote.
    available_names.update(name for name in RETAIL_CALCULATED_SCHEMA_FIELDS if name in scored_record)
    # A missing expected field still needs a card so the schema failure is visible.
    available_names.update(name for name, column in schema_columns.items()
                           if column.get("status") == "missing column")
    category_by_field = {field: category for category, names in RETAIL_SCHEMA_FIELD_GROUPS.items() for field in names}
    ordered_categories = list(RETAIL_SCHEMA_FIELD_GROUPS.items())
    extra_names = sorted(name for name in available_names if name not in category_by_field)
    if extra_names:
        ordered_categories.append(("Other Retail fields", extra_names))

    categories = []
    all_fields = []
    for category, names in ordered_categories:
        fields = []
        for name in names:
            if name not in available_names:
                continue
            calculated = name in RETAIL_CALCULATED_SCHEMA_FIELDS and name not in raw_record
            value = raw_record.get(name) if name in raw_record else scored_record.get(name)
            reasons = list(dict.fromkeys([*schema_reasons.get(name, []), *rule_reasons.get(name, [])]))
            field = {
                "name": name,
                "value": value,
                "status": ("ANOMALOUS" if reasons else "CALCULATED" if calculated else
                           "NORMAL" if schema_validation.get("available") else "UNVERIFIED"),
                "anomalous": bool(reasons),
                "calculated": calculated,
                "validation_applicable": not calculated,
                "validation_checked": bool(schema_validation.get("available") and not calculated),
                "expected_type": schema_columns.get(name, {}).get("expected_type"),
                "reasons": reasons,
            }
            fields.append(field)
            all_fields.append(field)
        if fields:
            categories.append({"name": category, "fields": fields})

    anomalous = {field["name"] for field in all_fields if field["anomalous"]}
    validated = {field["name"] for field in all_fields if field["validation_checked"]}
    return {
        "categories": categories,
        "field_count": len(all_fields),
        "validated_field_count": len(validated),
        "anomalous_field_count": len(anomalous),
        "normal_field_count": len(validated - anomalous),
        "unverified_field_count": sum(field["status"] == "UNVERIFIED" for field in all_fields),
        "anomalous_fields": sorted(anomalous),
        "calculated_field_count": sum(bool(field["calculated"] and not field["anomalous"]) for field in all_fields),
        "anomalous_calculated_field_count": sum(bool(field["calculated"] and field["anomalous"]) for field in all_fields),
    }


def load_engine_report(kind: str) -> dict:
    scores = _score_frame()
    if scores.empty:
        return {"ready": False, "hint": "Run 03_Trust_Score.py before opening this engine view."}

    current_month = str(scores["data_month"].max())
    current = scores[scores["data_month"].astype(str) == current_month]
    lineage = scores.groupby("data_lineage", dropna=False)

    if kind == "anomaly":
        methods = {
            "Isolation Forest": "isolation_forest_flag", "IQR": "iqr_flag",
            "KNN": "knn_flag", "Missing Data": "missing_flag",
        }
        by_method = {label: int(scores[column].fillna(0).sum()) for label, column in methods.items() if column in scores}
        risk = scores.sort_values("overall_anomaly_risk", ascending=False).head(8)
        method_rows = [{"label": label, "flagged": count} for label, count in by_method.items()]
        lineage_breakdown = [{"lineage": str(name), "total": len(group)} for name, group in lineage]
        return {"ready": True, "total_records": len(scores), "flagged_records": int((scores["overall_anomaly_risk"] > 0).sum()),
                "clean_records": int((scores["overall_anomaly_risk"] <= 0).sum()), "flag_events": sum(by_method.values()),
                "flag_rate": round(float((scores["overall_anomaly_risk"] > 0).mean() * 100), 2), "method_count": len(by_method),
                "current_month_trust": round(float(current["overall_trust_score"].mean() * 100), 2),
                "records_this_month": len(current), "any_signal_flagged": int((scores["overall_anomaly_risk"] > 0).sum()),
                "any_signal_this_month": int((current["overall_anomaly_risk"] > 0).sum()),
                "critical_this_month": int((current["overall_trust_score"] < 0.75).sum()), "by_method": method_rows,
                "lineage_breakdown": lineage_breakdown,
                "pillar_stats": {
                    "Anomaly":        {"avg_health": round(float(scores["anomaly_health"].mean() * 100), 1),   "below_threshold": int((scores["anomaly_health"] < 0.75).sum()),   "below_pct": round(float((scores["anomaly_health"] < 0.75).mean() * 100), 1)},
                    "Drift":          {"avg_health": round(float(scores["drift_health"].mean() * 100), 1),     "below_threshold": int((scores["drift_health"] < 0.75).sum()),     "below_pct": round(float((scores["drift_health"] < 0.75).mean() * 100), 1)},
                    "Business Rules": {"avg_health": round(float(scores["business_health"].mean() * 100), 1),  "below_threshold": int((scores["business_health"] < 0.75).sum()),  "below_pct": round(float((scores["business_health"] < 0.75).mean() * 100), 1)},
                    "Freshness":      {"avg_health": round(float(scores["freshness_health"].mean() * 100), 1), "below_threshold": int((scores["freshness_health"] < 0.75).sum()), "below_pct": round(float((scores["freshness_health"] < 0.75).mean() * 100), 1)},
                },
                "overlap_dist": [{"methods": 0, "records": int((scores["overall_anomaly_risk"] <= 0).sum())},
                                 {"methods": 1, "records": int((scores["overall_anomaly_risk"] > 0).sum())}],
                "worst_records": [{"record_id": int(row.get("row_id", index)), "businessID": str(row.get("customer_id", "Unknown")),
                                   "lineage": str(row.get("data_lineage", "")), "trust": round(float(row["overall_trust_score"] * 100), 2),
                                   "risk": round(float(row["overall_anomaly_risk"] * 100), 2), "methods": [label for label, column in methods.items() if row.get(column, 0)]}
                                  for index, row in risk.iterrows()]}

    if kind == "drift":
        drift_summary = {}
        if (ROOT / "results" / "retail" / "drift_summary.json").exists():
            drift_summary = json.loads((ROOT / "results" / "retail" / "drift_summary.json").read_text(encoding="utf-8"))

        # ── Drift health distribution buckets ──────────────────────────────
        dh = scores["drift_health"].fillna(0) * 100  # convert to 0–100 scale
        drift_dist = [
            {"label": "Strong drift",  "count": int((dh < 50).sum())},
            {"label": "Mild drift",    "count": int(((dh >= 50) & (dh < 70)).sum())},
            {"label": "Stable",        "count": int(((dh >= 70) & (dh < 85)).sum())},
            {"label": "Very stable",   "count": int((dh >= 85).sum())},
        ]

        # ── Worst drift records — current month ────────────────────────────
        current_scores = scores[scores["data_month"].astype(str) == current_month].copy()
        worst_drift_df = current_scores.sort_values("drift_health").head(15)
        worst_drift = [
            {
                "businessID": str(row.get("customer_id", row.get("entity_id", f"Row {int(idx)}"))),
                "drift_health": round(float(row["drift_health"]) * 100, 1),
                "record_id": int(row.get("row_id", idx)),
                "lineage": str(row.get("data_lineage", "")),
            }
            for idx, row in worst_drift_df.iterrows()
        ]

        return {"ready": True,
                "lineage_drift": [{"lineage": str(name), "total": len(group),
                                   "drift_health": round(float(group["drift_health"].mean() * 100), 2)} for name, group in lineage],
                "drift_dist": drift_dist,
                "worst_drift": worst_drift,
                "model_health": {"current_month": drift_summary.get("CURRENT_MONTH", {})}}

    if kind == "business":
        violations = scores["rule_violations"].fillna(0) if "rule_violations" in scores.columns else pd.Series(0, index=scores.index)

        # ── Violation severity distribution — 4 buckets ────────────────────
        # Match the frontend's expected labels: Clean (0) / Low (1–3) / Medium (4–8) / High (9–15) / Critical (15+)
        low_col  = scores["LOW_flags"].fillna(0)  if "LOW_flags"  in scores.columns else pd.Series(0, index=scores.index)
        med_col  = scores["MEDIUM_flags"].fillna(0) if "MEDIUM_flags" in scores.columns else pd.Series(0, index=scores.index)
        high_col = scores["HIGH_flags"].fillna(0)   if "HIGH_flags"   in scores.columns else pd.Series(0, index=scores.index)
        crit_col = scores["CRITICAL_flags"].fillna(0) if "CRITICAL_flags" in scores.columns else pd.Series(0, index=scores.index)

        clean_mask  = violations == 0
        low_mask    = (~clean_mask) & (violations <= 3)  & (crit_col == 0) & (high_col == 0)
        medium_mask = (~clean_mask) & (violations <= 8)  & (crit_col == 0) & (high_col == 0) & (~low_mask)
        high_mask   = (~clean_mask) & (violations <= 15) & (crit_col == 0) & (~low_mask) & (~medium_mask)
        crit_mask   = (~clean_mask) & (~low_mask) & (~medium_mask) & (~high_mask)

        severity_dist = [
            {"label": "Clean (0)",     "count": int(clean_mask.sum())},
            {"label": "Low (1–3)",     "count": int(low_mask.sum())},
            {"label": "Medium (4–8)",  "count": int(medium_mask.sum())},
            {"label": "High (9–15)",   "count": int(high_mask.sum())},
            {"label": "Critical (15+)","count": int(crit_mask.sum())},
        ]
        # Remove zero buckets from the top (keep Clean + at least one real bucket)
        severity_dist = [b for b in severity_dist if b["count"] > 0 or b["label"].startswith("Clean")]

        # ── Worst offenders — current month ────────────────────────────────
        worst_df = current.copy()
        if "rule_violations" in worst_df.columns:
            worst_df = worst_df[worst_df["rule_violations"].fillna(0) > 0]
        worst_df = worst_df.sort_values("business_health").head(15)
        worst_business = [
            {
                "record_id":       int(row.get("row_id", idx)),
                "businessID":      str(row.get("customer_id", f"Row {int(idx)}")),
                "business_health": round(float(row["business_health"]) * 100, 1),
                "violations":      int(row.get("rule_violations", 0) or 0),
                "lineage":         str(row.get("data_lineage", "")),
                "rules_broken":    str(row.get("rules_broken", "")),
            }
            for idx, row in worst_df.iterrows()
        ]

        return {"ready": True,
                "max_possible_flags": int(max(1, violations.max())),
                "severity_dist": severity_dist,
                "lineage_business": [{"lineage": str(name), "total": len(group),
                                      "business_health": round(float(group["business_health"].mean() * 100), 2),
                                      "avg_violations": round(float(group["rule_violations"].fillna(0).mean()), 2),
                                      "max_violations": int(group["rule_violations"].fillna(0).max())} for name, group in lineage],
                "worst_business": worst_business}

    if kind == "freshness":
        late = scores["freshness_days_late"].fillna(0)

        # ── Days-late distribution ──────────────────────────────────────────
        distribution = [
            {"label": "On time (<5 days)", "count": int((late < 5).sum())},
            {"label": "5–7 days late",     "count": int(((late >= 5)  & (late < 8)).sum())},
            {"label": "8–30 days late",    "count": int(((late >= 8)  & (late < 31)).sum())},
            {"label": "31–90 days late",   "count": int(((late >= 31) & (late < 91)).sum())},
            {"label": "90+ days late",     "count": int((late >= 91).sum())},
        ]

        # ── Monthly freshness health trend ──────────────────────────────────
        monthly_grp = (
            scores.assign(_month=scores["data_month"].astype(str))
            .groupby("_month", sort=True)
            .agg(avg_freshness=("freshness_health", "mean"),
                 avg_days_late=("freshness_days_late", "mean"))
            .reset_index()
        )
        monthly = [
            {"month": str(row["_month"]),
             "avg_freshness": round(float(row["avg_freshness"]) * 100, 1),
             "avg_days_late": round(float(row["avg_days_late"]), 2)}
            for _, row in monthly_grp.iterrows()
        ]

        # ── Most delayed records — current month ────────────────────────────
        # Use current month if it has late records, otherwise use all
        curr_late = current[current["freshness_days_late"].fillna(0) > 0] if not current.empty else pd.DataFrame()
        source = curr_late if not curr_late.empty else scores[scores["freshness_days_late"].fillna(0) > 0]
        worst_df = source.sort_values("freshness_days_late", ascending=False).head(20)
        worst_records = [
            {
                "businessID":      str(row.get("customer_id", f"Row {int(idx)}")),
                "data_month":      str(row.get("data_month", "")),
                "lineage":         str(row.get("data_lineage", "")),
                "days_late":       round(float(row.get("freshness_days_late", 0) or 0), 1),
                "freshness_health": round(float(row["freshness_health"]) * 100, 1),
                "record_id":       int(row.get("row_id", idx)),
            }
            for idx, row in worst_df.iterrows()
        ]

        return {"ready": True, "total_records": len(scores), "late_count": int((late > 0).sum()),
                "ontime_count": int((late <= 0).sum()), "avg_days_late": round(float(late.mean()), 2),
                "avg_days_late_of_late": round(float(late[late > 0].mean()), 2) if (late > 0).any() else 0,
                "max_days_late": int(late.max()), "fresh_pct": round(float((scores["freshness_health"] * 100).mean()), 2),
                "late_rate": round(float((late > 0).mean() * 100), 2),
                "distribution": distribution,
                "monthly": monthly,
                "lineage_breakdown": [{"lineage": str(name), "total": len(group),
                                       "avg_freshness": round(float(group["freshness_health"].mean() * 100), 2),
                                       "late_count": int((group["freshness_days_late"].fillna(0) > 0).sum()),
                                       "late_rate": round(float((group["freshness_days_late"].fillna(0) > 0).mean() * 100), 2),
                                       "avg_days_late": round(float(group["freshness_days_late"].fillna(0).mean()), 2)} for name, group in lineage],
                "worst_records": worst_records}

    return {"ready": False, "hint": "Unknown engine report."}


def load_forecast_report() -> dict:
    if not FORECAST_PATH.exists():
        return {"ready": False, "hint": "Run 04_ARIMA_Predictive_Trust_Alerts.py before opening forecasts."}
    forecasts = pd.read_csv(FORECAST_PATH)
    selected = ["overall_trust_score", "anomaly_health", "drift_health", "business_health", "freshness_health"]
    series = []
    for name in selected:
        rows = forecasts[(forecasts["series"] == name) & (forecasts["level"] == "ALL")]
        if rows.empty:
            continue
        scale = 100 if name.endswith("health") or name == "overall_trust_score" else 1
        actual = rows[rows["kind"] == "actual"].sort_values("month")
        predicted = rows[rows["kind"] == "forecast"].sort_values("month")
        series.append({
            "key": name, "name": name.replace("_", " ").title(), "label": name.replace("_", " ").title(),
            "kind": "pillar", "is_overall": name == "overall_trust_score",
            "hist_labels": actual["month"].astype(str).tolist(), "hist_values": (actual["value"] * scale).round(2).tolist(),
            "forecast_labels": predicted["month"].astype(str).tolist(), "forecast_values": (predicted["value"] * scale).round(2).tolist(),
            "lower_95": (predicted["lower"].fillna(predicted["value"]) * scale).round(2).tolist(),
            "upper_95": (predicted["upper"].fillna(predicted["value"]) * scale).round(2).tolist(),
            "threshold": 75 if name == "overall_trust_score" else 75, "arima_order": "MOVING_AVG_3",
            "mape": None, "confidence": "local deterministic forecast",
        })
    scores = _score_frame()
    if not scores.empty:
        monthly = scores.copy()
        monthly["data_month"] = monthly["data_month"].astype(str)
        metric_definitions = [
            ("retail_sales", "Retail sales", "total_amount", "sum", 0),
            ("units_sold", "Units sold", "stock_sold", "sum", 0),
            ("inventory_remaining", "Inventory remaining", "stock_remaining", "sum", 0),
            ("freshness_days_late", "Freshness delay (days)", "freshness_days_late", "mean", None),
        ]
        for key, label, column, aggregation, threshold in metric_definitions:
            if column not in monthly:
                continue
            values = pd.to_numeric(monthly[column], errors="coerce").fillna(0)
            grouped = monthly.assign(_metric=values).groupby("data_month", sort=True)["_metric"]
            actual = grouped.sum() if aggregation == "sum" else grouped.mean()
            labels = actual.index.astype(str).tolist()
            actual_values = actual.round(2).tolist()
            forecast_labels = []
            forecast_values = []
            forecast_lower = []
            forecast_upper = []
            if actual_values:
                last_period = pd.Period(labels[-1], freq="M")
                baseline = float(pd.Series(actual_values[-3:]).mean())
                recent_spread = float(pd.Series(actual_values[-6:], dtype="float64").std(ddof=1)) if len(actual_values[-6:]) > 1 else abs(baseline) * 0.05
                for offset in range(1, 4):
                    forecast_labels.append(str(last_period + offset))
                    forecast_values.append(round(baseline, 2))
                    margin = 1.96 * recent_spread * (offset ** 0.5)
                    forecast_lower.append(round(max(0.0, baseline - margin), 2))
                    forecast_upper.append(round(baseline + margin, 2))
            series.append({
                "key": key, "name": label, "label": label, "kind": "metric", "is_overall": False,
                "hist_labels": labels, "hist_values": actual_values,
                "forecast_labels": forecast_labels, "forecast_values": forecast_values,
                "lower_95": forecast_lower, "upper_95": forecast_upper,
                "threshold": threshold, "arima_order": "MOVING_AVG_3",
                "mape": None, "confidence": "local deterministic forecast",
            })
    alerts = []
    if ALERT_PATH.exists():
        for _, row in pd.read_csv(ALERT_PATH).iterrows():
            alerts.append({"metric": str(row.get("metric", "Retail metric")), "trigger": str(row.get("alert_type", "Alert")),
                           "month": str(row.get("month", "")), "severity": str(row.get("alert_level", "MEDIUM")),
                           "forecast": float(row.get("value", 0)), "threshold": float(row.get("threshold", 0)),
                           "reason": str(row.get("message", ""))})
    return {"ready": True, "horizon": 3, "series": series, "alerts": alerts, "mom_change": {}}


def load_retail_metric_options() -> dict:
    """Monthly retail measures offered by the forecast chart selectors."""
    scores = _score_frame()
    if scores.empty or "data_month" not in scores:
        return {"ready": False, "metrics": []}

    definitions = [
        ("returns", "Returns", "is_return", "sum"),
        ("average_discount", "Average discount (%)", "discount_pct", "mean"),
        ("discount_amount", "Discount amount", "discount_amount", "sum"),
        ("average_unit_price", "Average unit price", "unit_price", "mean"),
        ("unique_customers", "Unique customers", "customer_id", "nunique"),
        ("transactions", "Transactions", "transaction_id", "nunique"),
        ("online_order_share", "Online order share (%)", "online_order_flag", "percent"),
    ]
    frame = scores.copy()
    frame["data_month"] = frame["data_month"].astype(str)
    metrics = []
    for key, label, column, aggregation in definitions:
        if column not in frame:
            continue
        rows = []
        for month, group in frame.groupby("data_month", sort=True):
            values = group[column]
            if aggregation == "nunique":
                value = int(values.dropna().nunique())
            else:
                numeric = pd.to_numeric(values, errors="coerce")
                if aggregation == "percent":
                    value = float(numeric.fillna(0).mean() * 100)
                elif aggregation == "sum":
                    value = float(numeric.fillna(0).sum())
                else:
                    value = float(numeric.mean()) if numeric.notna().any() else 0.0
            rows.append({"month": month, "value": round(value, 2)})
        history_labels = [row["month"] for row in rows]
        history_values = [row["value"] for row in rows]
        forecast_labels = []
        forecast_values = []
        lower = []
        upper = []
        if history_labels:
            recent = pd.Series(history_values[-6:], dtype="float64")
            baseline = float(recent.tail(3).mean())
            spread = float(recent.std(ddof=1)) if len(recent) > 1 else abs(baseline) * 0.05
            last_month = pd.Period(history_labels[-1], freq="M")
            for offset in range(1, 4):
                forecast_labels.append(str(last_month + offset))
                forecast_values.append(round(baseline, 2))
                margin = 1.96 * spread * (offset ** 0.5)
                lower.append(round(max(0.0, baseline - margin), 2))
                upper.append(round(baseline + margin, 2))
        metrics.append({"key": key, "label": label,
                        "hist_labels": history_labels, "hist_values": history_values,
                        "forecast_labels": forecast_labels, "forecast_values": forecast_values,
                        "lower_95": lower, "upper_95": upper, "threshold": None})
    return {"ready": True, "metrics": metrics}


def load_anomaly_records(lineage_key: str = "all", business: str = "", limit: int = 100) -> dict:
    """Return flagged retail records for the Affected Records screen."""
    scores = _score_frame()
    if scores.empty:
        return {"ready": False, "hint": "Run 03_Trust_Score.py before opening affected records."}

    frame = scores.copy()
    lineage_map = {"b": "CURRENT_MONTH", "a": "PREVIOUS_MONTH", "h": "HISTORICAL"}
    selected_lineage = lineage_map.get(str(lineage_key).lower())
    if selected_lineage:
        frame = frame[frame["data_lineage"].astype(str).str.upper() == selected_lineage]
    if business:
        frame = frame[frame["customer_id"].astype(str) == str(business)]
    frame["customer_id"] = frame["customer_id"].fillna("Unknown").astype(str)
    frame.loc[frame["customer_id"].str.lower().isin({"", "nan", "none"}), "customer_id"] = "Unknown"

    for column in RETAIL_DETECTOR_COLUMNS.values():
        if column not in frame:
            frame[column] = 0
    frame["rule_violations"] = pd.to_numeric(frame.get("rule_violations", 0), errors="coerce").fillna(0)
    frame["overall_anomaly_risk"] = pd.to_numeric(frame["overall_anomaly_risk"], errors="coerce").fillna(0)
    frame["overall_trust_score"] = pd.to_numeric(frame["overall_trust_score"], errors="coerce").fillna(0)
    flag_columns = list(RETAIL_DETECTOR_COLUMNS.values())
    for column in flag_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame["fired_detector_count"] = frame[flag_columns].gt(0).sum(axis=1)
    frame["fired_count"] = frame["fired_detector_count"]
    flagged = frame[(frame["overall_anomaly_risk"] > 0) | (frame["fired_detector_count"] > 0) | (frame["rule_violations"] > 0)]
    flagged = flagged.sort_values(["overall_anomaly_risk", "overall_trust_score"], ascending=[False, True])

    records = []
    for _, row in flagged.head(max(1, min(int(limit), 500))).iterrows():
        risk = float(row["overall_anomaly_risk"])
        records.append({
            # The UI displays the dataset's record_id; the row_id stays available
            # for internal joins to the pipeline output.
            "record_id": str(row.get("record_id", row.get("row_id", ""))),
            "row_id": str(row.get("row_id", "")),
            "businessID": str(row["customer_id"]),
            "lineage": str(row.get("data_lineage", "")),
            "trust": round(float(row["overall_trust_score"]) * 100, 1),
            "anomaly_risk": round(risk * 100, 1),
            "severity": _retail_risk_severity(round(risk * 100, 1)),
            "fired_count": int(row["fired_detector_count"]),
            "fired_detectors": [label for label, column in RETAIL_DETECTOR_COLUMNS.items() if row[column] > 0],
            "business_rule_violation_count": int(row["rule_violations"]),
        })

    businesses = []
    for business_id, group in flagged.groupby("customer_id", dropna=False):
        businesses.append({
            "businessID": str(business_id),
            "records": int((frame["customer_id"] == business_id).sum()),
            "anomalous": int(len(group)),
            "worst_risk": round(float(group["overall_anomaly_risk"].max()) * 100, 1),
        })
    businesses.sort(key=lambda item: item["worst_risk"], reverse=True)

    return {
        "ready": True,
        "total_records": int(len(frame)),
        "total_businesses": int(frame["customer_id"].nunique()),
        "total_anomalous_records": int(len(flagged)),
        "below_threshold_count": int((frame["overall_trust_score"] < 0.75).sum()),
        "anomalous_businesses": int(flagged["customer_id"].nunique()),
        "risk_threshold": 0,
        "trust_threshold": 75,
        "by_detector": {label: int(flagged[column].gt(0).sum()) for label, column in RETAIL_DETECTOR_COLUMNS.items()},
        "records": records,
        "businesses": businesses,
    }


def load_anomaly_record(record_id: str, row_id: str | None = None) -> dict:
    """Return the detail payload used by the anomaly-record modal."""
    scores = _score_frame()
    if scores.empty:
        return {"ready": False, "error": "Anomaly output is unavailable."}
    # record_id is a source field and can repeat between months. Table clicks
    # pass the unique pipeline row_id to identify the exact scored transaction.
    if row_id is not None:
        matches = scores[scores["row_id"].astype(str) == str(row_id)] if "row_id" in scores else pd.DataFrame()
    else:
        matches = scores[scores["record_id"].astype(str) == str(record_id)] if "record_id" in scores else pd.DataFrame()
        if matches.empty and "row_id" in scores:
            matches = scores[scores["row_id"].astype(str) == str(record_id)]
        elif len(matches) > 1:
            risk_values = pd.to_numeric(matches.get("overall_anomaly_risk", 0), errors="coerce").fillna(0)
            rule_values = pd.to_numeric(matches.get("rule_violations", 0), errors="coerce").fillna(0)
            flagged_matches = matches[(risk_values > 0) | (rule_values > 0)]
            if not flagged_matches.empty:
                matches = flagged_matches.sort_values(
                    ["overall_anomaly_risk", "overall_trust_score"],
                    ascending=[False, True],
                )
    if matches.empty:
        return {"ready": False, "error": f"Record {record_id} was not found."}
    row = matches.iloc[0]
    config = _retail_config()
    trust_threshold = float((config.get("alerts") or {}).get("trust_below", 0.75)) * 100
    def numeric(key, default=None):
        value = pd.to_numeric(row.get(key, default), errors="coerce")
        if pd.isna(value) or not math.isfinite(float(value)):
            return default
        return float(value)
    anomaly_config = config.get("anomaly") or {}
    iqr_multiplier = float(anomaly_config.get("iqr_multiplier", _hyperparams()["IQR_MULTIPLIER"]))
    knn_k = int(anomaly_config.get("knn_k", 5))
    knn_percentile = float(anomaly_config.get("knn_percentile", 0.99)) * 100
    duplicate_keys = ", ".join(config.get("duplicate_key") or []) or "configured key fields"
    detector_specs = [
        ("isolation_forest", "Isolation-forest outlier", "isolation_forest_flag",
         "The transaction sits in a sparse, low-density region of the retail feature space.", "isolation score < 0"),
        ("iqr", "IQR range outlier", "iqr_flag",
         "A numeric retail field falls outside the interquartile fence.",
         f"outside Q1/Q3 ± {iqr_multiplier:g}×IQR"),
        ("knn", "KNN distance outlier", "knn_flag",
         f"The transaction is unusually far from its {knn_k} nearest retail neighbours.",
         f"distance above the {knn_percentile:g}th percentile"),
        ("duplicate", "Duplicate record", "duplicate_flag",
         "The transaction duplicates another row on its key fields.", f"exact match on {duplicate_keys}"),
        ("missing", "Missing critical values", "missing_flag",
         "One or more required retail fields are empty for this transaction.",
         "any null among " + ", ".join(dict.fromkeys([*(config.get("features") or []), *(config.get("not_null") or [])]))),
    ]
    detectors = [{"key": key, "label": label, "fired": numeric(column, 0) > 0,
                  "evaluated": True, "meaning": meaning, "threshold": limit}
                 for key, label, column, meaning, limit in detector_specs]
    detectors.append({"key": "arima_forecast", "label": "Forecast deviation (ARIMA)", "fired": False,
                     "evaluated": False,
                     "meaning": "ARIMA deviation checks apply to monthly retail series, not to an individual transaction.",
                     "threshold": "not evaluated per transaction"})
    rule_field_map = {
        "quantity_not_positive": ["quantity"],
        "negative_total": ["total_amount"],
        "price_not_positive": ["unit_price"],
        "total_does_not_add_up": ["quantity", "unit_price", "discount_amount", "total_amount"],
        "discount_out_of_range": ["discount_pct"],
        "price_far_from_normal": ["unit_price", "price_ratio"],
        "negative_stock": ["stock_remaining"],
        "sold_more_than_stock": ["stock_sold", "stock_at_start"],
    }
    anomaly_fields = set()
    rules_value = row.get("rules_broken", "")
    if pd.isna(rules_value) or str(rules_value).strip().lower() in {"", "nan", "none", "null", "<na>"}:
        rules_value = ""
    broken_rules = [name.strip() for name in str(rules_value).split(";") if name.strip()]
    for name in broken_rules:
        anomaly_fields.update(rule_field_map.get(name, []))
    if numeric("missing_flag", 0) > 0:
        anomaly_fields.update(str(key) for key, value in row.items()
                              if key not in {"CRITICAL_flags", "HIGH_flags", "MEDIUM_flags", "LOW_flags"}
                              and pd.isna(value))
    scored_record = {}
    for key, value in row.to_dict().items():
        if value is None or (isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "null", "<na>"}):
            scored_record[str(key)] = None
        else:
            normalized = value.item() if hasattr(value, "item") else value
            if pd.isna(normalized) or (isinstance(normalized, (int, float)) and not math.isfinite(float(normalized))):
                scored_record[str(key)] = None
            else:
                scored_record[str(key)] = normalized
    schema_validation = _retail_schema_for_record(row, config)
    raw_record = schema_validation.get("raw_record") or {
        key: scored_record.get(key) for key in RETAIL_SCHEMA_TYPES if key in scored_record
    }
    isolation_score = numeric("isolation_forest_score")
    isolation_fired = numeric("isolation_forest_flag", 0) > 0
    isolation_cutoff = 0.0
    if isolation_score is None:
        isolation_explanation = "The saved pipeline output does not contain the Isolation Forest decision score. Rerun 03_Trust_Score.py to generate detector diagnostics."
    else:
        isolation_explanation = (f"Decision score {isolation_score:.8g} is {'below' if isolation_fired else 'at or above'} the {isolation_cutoff:g} cutoff, matching the saved detector flag.")
    iqr_field_details = _json_list(row.get("iqr_details_json"))
    knn_distance = numeric("knn_mean_distance")
    knn_cutoff = numeric("knn_distance_threshold")
    knn_fired = numeric("knn_flag", 0) > 0
    knn_condition_true = knn_distance is not None and knn_cutoff is not None and knn_distance > knn_cutoff
    key_fields = list(config.get("duplicate_key") or [])
    duplicate_matches = _json_list(row.get("duplicate_matches_json"))
    duplicate_count = int(numeric("duplicate_match_count", len(duplicate_matches)) or 0)
    required_fields = list(dict.fromkeys([*(config.get("features") or []), *(config.get("not_null") or [])]))
    missing_fields = _json_list(row.get("missing_fields_json"))
    diagnostics_available = row.get("iqr_details_json") is not None
    isolation_details = {
        "available": isolation_score is not None, "score": isolation_score,
        "decision_threshold": isolation_cutoff, "comparison": "score < threshold",
        "status": "FLAGGED" if isolation_fired else "PASSED" if isolation_score is not None else "N/A",
        "n_estimators": int(anomaly_config.get("iso_estimators", 100)),
        "fit_population_count": int(numeric("isolation_forest_fit_count", 0) or 0),
        "contamination": anomaly_config.get("iso_contamination", 0.02),
        "explanation": isolation_explanation,
    }
    iqr_details = {
        "available": diagnostics_available, "multiplier": iqr_multiplier,
        "baseline_lineage": list((anomaly_config.get("baseline") or ["historical"])),
        "fields": iqr_field_details,
        "status": "FLAGGED" if numeric("iqr_flag", 0) > 0 else "PASSED" if diagnostics_available else "N/A",
        "explanation": "Per-field quartiles and fences are the values calculated by the pipeline for its configured baseline population.",
    }
    knn_details = {
        "available": knn_distance is not None and knn_cutoff is not None,
        "neighbours": int(anomaly_config.get("knn_k", 5)),
        "actual_distance": knn_distance, "percentile": float(anomaly_config.get("knn_percentile", 0.99)),
        "cutoff": knn_cutoff, "comparison": "actual distance > cutoff",
        "comparison_value": (f"{knn_distance:.10g} > {knn_cutoff:.10g}" if knn_distance is not None and knn_cutoff is not None else None),
        "comparison_true": knn_condition_true if knn_distance is not None and knn_cutoff is not None else None,
        "status": "FLAGGED" if knn_fired else "PASSED" if knn_distance is not None else "N/A",
        "reference_population_count": int(numeric("knn_reference_count", 0) or 0),
        "explanation": "Distance is the mean distance to the configured nearest neighbours after the pipeline's median imputation, baseline standardization, and clipping.",
    }
    duplicate_details = {
        "key_fields": key_fields,
        "current_key_values": {key: raw_record.get(key) for key in key_fields},
        "exact_duplicate_found": numeric("duplicate_flag", 0) > 0,
        "matching_record_count": duplicate_count,
        "matching_records": duplicate_matches,
        "status": "FLAGGED" if numeric("duplicate_flag", 0) > 0 else "PASSED",
    }
    missing_details = {
        "required_fields": required_fields, "required_field_count": len(required_fields),
        "missing_fields": missing_fields, "null_count": len(missing_fields),
        "status": "FLAGGED" if numeric("missing_flag", 0) > 0 else "PASSED",
        "available": row.get("missing_fields_json") is not None,
    }
    anomaly_fields.update(schema_validation.get("missing_fields", []))
    anomaly_fields.update(schema_validation.get("unexpected_fields", []))
    anomaly_fields.update(schema_validation.get("type_mismatches", []))
    anomaly_fields.update(schema_validation.get("invalid_values", []))
    scaled = lambda key: (round(value * 100, 1) if (value := numeric(key)) is not None else None)
    trust = {"overall_trust": scaled("overall_trust_score"),
             "anomaly_health": scaled("anomaly_health"),
             "drift_health": scaled("drift_health"),
             "business_health": scaled("business_health"),
             "freshness_health": scaled("freshness_health")}
    risk = round(numeric("overall_anomaly_risk", 0.0) * 100, 1)
    lineage_value = str(row.get("data_lineage", "Unknown"))
    drift_source = scores.loc[scores["data_lineage"].astype(str) == lineage_value, "drift_health"]
    drift_values = pd.to_numeric(drift_source, errors="coerce") * 100
    drift_values = drift_values[drift_values.map(lambda value: math.isfinite(float(value)))]
    drift_edges = [("<50%", 0, 50), ("50–70%", 50, 70), ("70–85%", 70, 85),
                   ("85–95%", 85, 95), ("≥95%", 95, float("inf"))]
    drift_distribution = [{"label": label, "count": int(((drift_values >= lower) & (drift_values < upper)).sum())}
                          for label, lower, upper in drift_edges]
    drift_score_raw = numeric("drift_health")
    drift_score = round(drift_score_raw * 100, 1) if drift_score_raw is not None else None
    drift_values_raw = pd.to_numeric(drift_source, errors="coerce")
    drift_values_raw = drift_values_raw[drift_values_raw.map(lambda value: pd.notna(value) and math.isfinite(float(value)))]
    drift_percentile = (round(float((drift_values_raw <= drift_score_raw).mean() * 100), 1)
                        if drift_score_raw is not None and len(drift_values_raw) else None)
    drift_score_percent = drift_score_raw * 100 if drift_score_raw is not None else None
    drift_bucket = next((label for label, lower, upper in drift_edges
                         if drift_score_percent is not None and lower <= drift_score_percent < upper), None)
    drift_severity = ("Critical" if drift_score_percent is not None and drift_score_percent < 50 else
                      "High" if drift_score_percent is not None and drift_score_percent < 70 else
                      "Moderate" if drift_score_percent is not None and drift_score_percent < 85 else
                      "Low" if drift_score is not None else "Unavailable")

    def display_number(key: str) -> str:
        value = numeric(key)
        return "Missing" if value is None else f"{value:g}"
    rule_display = {
        "quantity_not_positive": ("Quantity must be positive", ["quantity"], "quantity > 0"),
        "negative_total": ("Transaction amount must not be negative", ["total_amount"], "total_amount ≥ 0"),
        "price_not_positive": ("Product price must be positive", ["unit_price"], "unit_price > 0"),
        "total_does_not_add_up": ("Transaction total must add up", ["quantity", "unit_price", "discount_amount", "total_amount"], "absolute total difference ≤ 1"),
        "discount_out_of_range": ("Discount must be in range", ["discount_pct"], "0% ≤ discount_pct ≤ 20%"),
        "price_far_from_normal": ("Price must be near its product baseline", ["unit_price", "price_ratio"], "0.5 ≤ price ratio ≤ 1.6"),
        "negative_stock": ("Remaining stock must not be negative", ["stock_remaining"], "stock_remaining ≥ 0"),
        "sold_more_than_stock": ("Units sold must not exceed starting stock", ["stock_sold", "stock_at_start"], "stock_sold ≤ stock_at_start"),
    }
    business_rule_checks = []
    for rule in config.get("rules", []):
        key = str(rule.get("name", ""))
        label, fields, condition = rule_display.get(key, (key.replace("_", " ").capitalize(), [], rule.get("when", "Configured retail rule")))
        actuals = "; ".join(f"{field} = {display_number(field)}" for field in fields)
        fired = key in broken_rules
        business_rule_checks.append({"key": key, "label": label, "severity": str(rule.get("severity", "MEDIUM")).upper(),
                                    "fired": fired, "result": "FLAGGED" if fired else "PASSED",
                                    "actual_result": {"evaluated": True, "violated": fired},
                                    "detail": actuals or "Retail rule evaluated for this transaction.",
                                    "threshold": condition, "expected_condition": condition,
                                    "configured_condition": str(rule.get("when", ""))})
    schema_field_details = _retail_schema_field_groups(
        raw_record, scored_record, schema_validation, business_rule_checks, rule_field_map)
    drift_cohort = {}
    if DRIFT_SUMMARY_PATH.exists():
        drift_cohort = json.loads(DRIFT_SUMMARY_PATH.read_text(encoding="utf-8")).get(lineage_value, {})
    freshness_config = config.get("freshness") or {}
    date_config = config.get("columns") or {}
    sale_date = raw_record.get(date_config.get("date") or "sale_date")
    if not sale_date:
        sale_date = raw_record.get("sale_date")
    age_days = None
    parsed_sale_date = pd.to_datetime(sale_date, errors="coerce")
    if not pd.isna(parsed_sale_date) and parsed_sale_date.date() <= datetime.now().date():
        age_days = int((datetime.now().date() - parsed_sale_date.date()).days)
    expected_date_column = freshness_config.get("expected_date_column")
    actual_date_column = freshness_config.get("actual_date_column")
    freshness_detail = {
        "transaction_date": sale_date,
        "record_age_days": age_days,
        "expected_arrival_date": raw_record.get(expected_date_column) if expected_date_column else None,
        "actual_arrival_date": raw_record.get(actual_date_column) if actual_date_column else None,
        "days_late": numeric("freshness_days_late_clean", numeric("freshness_days_late")),
        "health": trust["freshness_health"],
        "tau_days": float(freshness_config.get("tau", _hyperparams()["FRESHNESS_TAU"])),
        "configured_expected_date_column": expected_date_column,
        "configured_actual_date_column": actual_date_column,
        "method": "exp(-max(actual ingestion date - expected arrival date, 0) / tau)",
        "explanation": "Freshness Health measures ingestion timeliness. Data Age is the transaction's age and does not enter this score.",
        "health_formula_value": numeric("freshness_health"),
    }
    rule_count = len(business_rule_checks)
    drift_details = {
        "health_score": drift_score_raw,
        "comparison_population": f"All saved row scores with data_lineage = {lineage_value}",
        "population_count": int(len(drift_values_raw)),
        "percentile": drift_percentile,
        "percentile_method": "100 × count(cohort drift_health <= this record's unrounded drift_health) / cohort size",
        "histogram_uses_same_population": True,
        "histogram_and_percentile_population": "Same lineage cohort in row_scores.csv",
        "severity": drift_severity,
        "psi_health": drift_cohort.get("psi_health"),
        "welch_health": drift_cohort.get("welch_health"),
        "mahalanobis_health": drift_cohort.get("mahalanobis_health"),
        "cohort_level_note": "PSI and Welch are calculated for the lineage cohort; Mahalanobis health contributes to each row's stored drift score.",
    }
    arima_details = {
        "evaluated": False, "status": "N/A",
        "message": "Not evaluated per transaction. ARIMA checks operate on monthly retail series.",
        "associated_month": raw_record.get("data_month"),
        "monthly_series_context": [],
    }
    if FORECAST_PATH.exists() and raw_record.get("data_month"):
        forecast_rows = pd.read_csv(FORECAST_PATH)
        month = str(raw_record.get("data_month"))
        for _, forecast_row in forecast_rows[(forecast_rows["month"].astype(str) == month) &
                (forecast_rows["kind"].astype(str) == "actual")].iterrows():
            level = str(forecast_row.get("level", "ALL"))
            entity_id = str(forecast_row.get("entity_id", "ALL"))
            if level == "ALL" or (level == "SHOP" and entity_id == str(raw_record.get("store_id"))) or (level == "PRODUCT" and entity_id == str(raw_record.get("product_id"))):
                value = pd.to_numeric(forecast_row.get("value"), errors="coerce")
                if pd.notna(value) and math.isfinite(float(value)):
                    arima_details["monthly_series_context"].append({
                        "series": str(forecast_row.get("series", "")), "level": level,
                        "entity_id": entity_id, "month": month, "kind": "actual",
                        "value": float(value), "method": str(forecast_row.get("method", "")),
                        "order": str(forecast_row.get("order", "")),
                    })
    business_rule_details = {
        "total_rules": rule_count,
        "violations": int(numeric("rule_violations", 0)),
        "checks": business_rule_checks,
    }
    freshness_detail["status"] = ("N/A" if trust["freshness_health"] is None else
                                  "FLAGGED" if trust["freshness_health"] < float((config.get("alerts") or {}).get("pillar_below", 0.75)) * 100 else "PASSED")
    return {
        "ready": True,
        "record_id": str(raw_record.get("record_id", row.get("record_id", record_id))),
        "row_id": str(row.get("row_id", "")),
        "businessID": str(raw_record.get("customer_id") or "Unknown"),
        "record_info": {key: raw_record.get(key) for key in
                        ("store_id", "store_city", "customer_id", "product_id", "product_name", "sale_date",
                         "data_month", "expected_arrival_date", "ingestion_date", "transaction_id")},
        "lineage": lineage_value,
        "severity": _retail_risk_severity(risk),
        "overall_anomaly_risk": risk,
        "fired_count": sum(item["fired"] for item in detectors),
        "detector_fired_count": sum(item["fired"] for item in detectors),
        "fired_detectors": [item["key"] for item in detectors if item["evaluated"] and item["fired"]],
        "business_rule_violation_count": int(numeric("rule_violations", 0)),
        "trust_metrics": trust,
        "drift_distribution": {"lineage": lineage_value, "sample_size": int(len(drift_values)),
                               "record_score": drift_score, "percentile": drift_percentile,
                               "record_score_unrounded": drift_score_raw,
                               "record_bucket": drift_bucket, "severity": drift_severity,
                               "buckets": drift_distribution,
                               "cohort_comparison": drift_cohort,
                               "explanation": "The stored record score combines its Mahalanobis health with PSI and Welch health calculated for its lineage cohort."},
        "trust_threshold": trust_threshold,
        "pillar_threshold": float((config.get("alerts") or {}).get("pillar_below", 0.75)) * 100,
        "below_threshold": trust["overall_trust"] is not None and trust["overall_trust"] < trust_threshold,
        "detectors": detectors,
        "isolation_forest_details": isolation_details,
        "iqr_details": iqr_details,
        "knn_details": knn_details,
        "duplicate_details": duplicate_details,
        "missing_values_details": missing_details,
        "arima_details": arima_details,
        "schema_anomaly_fields": sorted(anomaly_fields),
        "schema_validation": {key: value for key, value in schema_validation.items() if key != "raw_record"},
        "schema_fields": schema_field_details,
        "raw_record": raw_record,
        "business_rules": {"evaluable": True, "estimated_violations": int(numeric("rule_violations", 0)),
                            "total_rules": rule_count, "rules_broken": broken_rules,
                            "checks": business_rule_checks},
        "business_rule_details": business_rule_details,
        "drift_details": drift_details,
        "freshness_details": freshness_detail,
        "freshness": freshness_detail,
        "freshness_days_late": int(numeric("freshness_days_late", 0)) if numeric("freshness_days_late") is not None else None,
        "freshness_tau": _hyperparams()["FRESHNESS_TAU"],
    }


def _risk_tier(value: object) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    if not math.isfinite(score):
        score = 0.0
    if score >= 0.90:
        return "CRITICAL"
    if score >= 0.75:
        return "HIGH"
    if score >= 0.50:
        return "WATCH"
    return "LOW"


def load_churn_report(tier: str = "all", limit: int = 100, dashboard: bool = False) -> dict:
    if not CHURN_PATH.exists():
        return {"ready": False, "hint": "Run 03_Trust_Score.py before opening churn risk."}

    df = pd.read_csv(CHURN_PATH)
    if df.empty:
        return {"ready": True, "total_businesses": 0, "at_risk_count": 0,
                "avg_churn_probability": 0, "tier_counts": {}, "businesses": []}

    # The retail churn implementation is the configured RFM formula in
    # engine/churn.py. Enrich its per-customer output from transaction rows;
    # do not fabricate a trained-model AUC or feature importance.
    df = df.copy()
    for column in ("purchase_count", "total_value", "recency_days", "churn_risk_score"):
        df[column] = pd.to_numeric(df.get(column, 0), errors="coerce")
    df["churn_risk_score"] = df["churn_risk_score"].replace([float("inf"), float("-inf")], 0).fillna(0).clip(0, 1)
    df["purchase_count"] = df["purchase_count"].fillna(0).clip(lower=0)
    df["total_value"] = df["total_value"].fillna(0).clip(lower=0)
    df["recency_days"] = df["recency_days"].fillna(0).clip(lower=0)
    df = df.drop_duplicates("entity_id", keep="first")
    df["risk_tier"] = df["churn_risk_score"].map(_risk_tier)

    transactions = pd.DataFrame()
    source_path = ROOT / "data" / "retail_shop_data.csv"
    if source_path.exists():
        transactions = _cached_csv(source_path, low_memory=False)
        if "customer_id" in transactions:
            transactions = transactions[transactions["customer_id"].notna()].copy()
            transactions["customer_id"] = transactions["customer_id"].astype(str)
            if "sale_date" in transactions:
                transactions["sale_date"] = pd.to_datetime(transactions["sale_date"], errors="coerce")
            for col in ("total_amount", "is_return", "online_order_flag"):
                if col in transactions:
                    transactions[col] = pd.to_numeric(transactions[col], errors="coerce")

    by_customer = {}
    by_category = Counter()
    if not transactions.empty:
        # Pre-parse numeric columns once
        for col in ("total_amount", "is_return", "online_order_flag", "discount_pct", "quantity"):
            if col in transactions.columns:
                transactions[col] = pd.to_numeric(transactions[col], errors="coerce")
        for customer_id, group in transactions.groupby("customer_id", sort=False):
            channel = group["online_order_flag"].dropna() if "online_order_flag" in group.columns else pd.Series(dtype=float)
            online_share = float(channel.mean()) if not channel.empty else None
            segment = ("Omnichannel" if online_share is not None and 0.2 < online_share < 0.8 else
                       "Online shopper" if online_share is not None and online_share >= 0.8 else
                       "In-store shopper" if online_share is not None else "Unclassified")
            categories = group["category"].dropna().astype(str).value_counts().index.tolist() if "category" in group.columns else []
            # Compute rich per-customer signals for reason generation
            txn_count = len(group)
            aov = float(group["total_amount"].mean()) if "total_amount" in group.columns and group["total_amount"].notna().any() else None
            return_rate = float(group["is_return"].mean()) if "is_return" in group.columns and group["is_return"].notna().any() else 0.0
            sorted_dates = group["sale_date"].dropna().sort_values() if "sale_date" in group.columns else pd.Series(dtype="datetime64[ns]")
            avg_gap_days = float(sorted_dates.diff().dt.days.dropna().mean()) if len(sorted_dates) > 1 else None
            cat_count = len(set(categories))
            avg_disc = float(group["discount_pct"].mean()) if "discount_pct" in group.columns and group["discount_pct"].notna().any() else None
            profile = {
                "last_purchase_date": sorted_dates.max().strftime("%Y-%m-%d") if not sorted_dates.empty else None,
                "segment": segment,
                "categories": categories,
                "txn_count": txn_count,
                "average_order_value": aov,
                "return_rate": return_rate,
                "avg_days_between_purchases": avg_gap_days,
                "category_count": cat_count,
                "avg_discount_pct": avg_disc,
                "online_share": online_share,
            }
            if not dashboard:
                profile["repeat_rate"] = max(0, txn_count - 1) / txn_count if txn_count else 0
            by_customer[str(customer_id)] = profile

    # Pre-compute cohort percentiles for contextual reason phrasing
    p25_recency   = float(df["recency_days"].quantile(0.25))
    p75_recency   = float(df["recency_days"].quantile(0.75))
    p25_purchases = float(df["purchase_count"].quantile(0.25))
    p75_purchases = float(df["purchase_count"].quantile(0.75))
    p25_value     = float(df["total_value"].quantile(0.25))
    p75_value     = float(df["total_value"].quantile(0.75))

    tier_map = df["risk_tier"]
    counts = Counter(tier_map)
    selected_df = df if tier.lower() == "all" else df[tier_map.str.lower() == tier.lower()]
    selected_df = selected_df.sort_values("churn_risk_score", ascending=False).head(max(1, min(int(limit), 10000)))
    customers = []
    for _, row in selected_df.iterrows():
        customer_id = str(row.get("entity_id", "Unknown"))
        profile = by_customer.get(customer_id, {})
        purchases = int(row["purchase_count"])
        recency = int(row["recency_days"])
        spend = float(row["total_value"])
        score = float(row["churn_risk_score"])
        tier_label = row["risk_tier"]

        # ── Build specific, data-driven reasons ──────────────────────────
        reasons = []

        # 1. Recency signal — most important weight (55%)
        if recency >= 270:
            reasons.append(f"No purchase in {recency} days ({recency // 30} months inactive)")
        elif recency >= 180:
            reasons.append(f"Last purchase was {recency} days ago (over 6 months)")
        elif recency >= 90:
            reasons.append(f"Inactive for {recency} days — above cohort median ({int(p75_recency)}d threshold)")
        elif recency >= int(p75_recency):
            reasons.append(f"Recency of {recency} days is in the high-risk quartile")

        # 2. Purchase frequency signal (30%)
        avg_gap = profile.get("avg_days_between_purchases")
        if purchases == 1:
            reasons.append("Only one purchase ever recorded — no repeat buying behavior")
        elif purchases <= int(p25_purchases):
            if avg_gap and avg_gap > 60:
                reasons.append(f"Low purchase frequency ({purchases} orders, avg {int(avg_gap)} days between visits)")
            else:
                reasons.append(f"Below-average purchase frequency ({purchases} orders vs cohort median {int(df['purchase_count'].median())})")
        elif avg_gap and avg_gap > p75_recency:
            reasons.append(f"Long gap between purchases (avg {int(avg_gap)} days between orders)")

        # 3. Spend signal (15%)
        if spend <= 0:
            reasons.append("No recorded purchase value")
        elif spend < p25_value:
            reasons.append(f"Low total spend (£{spend:,.2f} — bottom quartile of cohort)")
        elif spend < p25_value * 2 and purchases <= int(p25_purchases):
            reasons.append(f"Low purchase value relative to visit count (£{spend / max(purchases,1):,.2f} avg per order)")

        # 4. Category & engagement signals from transaction profile
        cat_count = profile.get("category_count", 0)
        cats = profile.get("categories", [])
        aov = profile.get("average_order_value")
        return_rate = profile.get("return_rate", 0.0) or 0.0
        online_share = profile.get("online_share")
        avg_disc = profile.get("avg_discount_pct")

        if return_rate >= 0.25:
            reasons.append(f"High return rate ({int(return_rate * 100)}% of purchases returned)")
        elif return_rate >= 0.10 and tier_label in ("CRITICAL", "HIGH"):
            reasons.append(f"Elevated return rate ({int(return_rate * 100)}%)")

        if cat_count == 1 and cats:
            reasons.append(f"Single-category buyer ({cats[0]} only) — limited basket breadth")
        elif cat_count >= 1 and purchases >= 4 and cat_count < 2:
            reasons.append(f"Narrow purchase pattern ({cat_count} product category)")

        if aov and aov < p25_value / max(purchases, 1) * 0.5:
            reasons.append(f"Declining average order value (£{aov:,.2f} per visit)")

        if online_share is not None:
            if online_share == 0 and recency > 60:
                reasons.append("In-store only buyer with extended absence")
            elif online_share == 1.0 and recency > 90:
                reasons.append("Online-only buyer showing inactivity")

        if avg_disc is not None and avg_disc >= 30 and score >= 0.6:
            reasons.append(f"Purchases heavily discount-driven (avg {int(avg_disc)}% off) — may not return at full price")

        # 5. Fallback if no specific signals fired
        if not reasons:
            if score >= 0.75:
                reasons.append("RFM score significantly elevated: high recency + low frequency combination")
            elif score >= 0.50:
                reasons.append("Moderate RFM risk: recency and frequency below cohort average")
            else:
                reasons.append("RFM score above baseline due to reduced purchase activity")

        likely_reason = "; ".join(reasons[:4])  # cap at 4 for readability
        # ─────────────────────────────────────────────────────────────────

        customer_categories = profile.get("categories", [])
        if tier_label in {"CRITICAL", "HIGH"}:
            by_category.update(customer_categories)
        customer = {
            "customer_id": customer_id,
            "customer_name": f"Customer {customer_id}",
            "customer_segment": profile.get("segment", "Unclassified"),
            "total_purchases": purchases,
            "last_purchase_date": profile.get("last_purchase_date"),
            "churn_probability": round(score * 100, 1),
            "risk_tier": tier_label,
            "likely_reason": likely_reason,
            "categories": customer_categories,
            # Always include these for the detail popup
            "total_purchase_amount": round(spend, 2),
            "average_order_value": round(profile.get("average_order_value") or 0, 2),
            "return_rate": round((profile.get("return_rate") or 0) * 100, 1),
            "avg_days_between_purchases": round(profile.get("avg_days_between_purchases") or 0, 1),
            "recency_days": recency,
            "top_risk_factors": [r for r in likely_reason.split("; ") if r],
        }
        if not dashboard:
            customer.update({
                "total_purchase_amount": round(spend, 2),
                "average_order_value": profile.get("average_order_value"),
                "return_rate": profile.get("return_rate"),
                "average_days_between_purchases": profile.get("avg_days_between_purchases"),
                "recency_days": recency,
                "categories": customer_categories,
                # Preserve the legacy bundled dashboard contract for existing clients.
                "businessID": customer_id,
                "businessName": customer["customer_name"],
                "industry": ", ".join(customer_categories) if customer_categories else "Retail",
                "segment": customer["customer_segment"],
                "likely_churn_reason": customer["likely_reason"],
                "predicted_churn_by": "Next review cycle",
                "top_risk_factors": customer["likely_reason"],
            })
        customers.append(customer)

    at_risk_ids = set(df.loc[df["risk_tier"].isin(["CRITICAL", "HIGH"]), "entity_id"].astype(str))
    segment_counts = Counter(by_customer[cid]["segment"] for cid in at_risk_ids if cid in by_customer)
    category_counts = Counter()
    for cid in at_risk_ids:
        category_counts.update(set(by_customer.get(cid, {}).get("categories", [])))
    scored = len(df)
    report = {
        "ready": True,
        "total_customers": scored,
        "at_risk_count": counts.get("CRITICAL", 0) + counts.get("HIGH", 0),
        "avg_churn_probability": round(float(df["churn_risk_score"].mean()) * 100, 1) if scored else 0,
        "tier_counts": {name: int(counts.get(name, 0)) for name in ("CRITICAL", "HIGH", "WATCH", "LOW")},
        "by_category": [{"name": key, "count": value} for key, value in category_counts.most_common(10)],
        "by_industry": [{"name": key, "count": value} for key, value in category_counts.most_common(10)],
        "by_segment": [{"name": key, "count": value} for key, value in segment_counts.most_common()],
        "model_confidence": {"type": "RFM heuristic", "auc": None, "quality": "Not evaluated", "note": "The current configured churn scorer is a weighted RFM heuristic, not a trained classifier; no labeled churn outcome or evaluation AUC is available."},
        "validation": {"confidence": "Not evaluated (weighted RFM heuristic)", "source": str(CHURN_PATH.name)},
        "feature_importance": [
            {"feature": "Recency (days since purchase)", "importance": 0.55},
            {"feature": "Purchase frequency", "importance": 0.30},
            {"feature": "Total purchase amount", "importance": 0.15},
        ],
        "scoring_definition": "Configured score weights in engine/churn.py; values are model weights, not learned feature importance.",
        "customers": customers,
        "filtered_count": len(selected_df),
    }
    if not dashboard:
        report["total_businesses"] = scored
        report["businesses"] = customers
    return report


def _send_email(recipient: str | None = None) -> None:
    from sendgrid.helpers.mail import Mail
    _, sender, configured_receiver = require_sendgrid_config(require_receiver=recipient is None)
    receiver = recipient or configured_receiver

    report = load_churn_report(limit=20)
    if not report.get("ready"):
        raise RuntimeError(report.get("hint", "Churn report is unavailable."))
    rows = "".join(
        f"<tr><td>{html.escape(item['businessID'])}</td>"
        f"<td>{item['churn_probability']:.1f}%</td>"
        f"<td>{html.escape(item['risk_tier'])}</td>"
        f"<td>{html.escape(item['likely_churn_reason'])}</td></tr>"
        for item in report["businesses"]
    )
    body = ("<h2>DeltaMax V5 Retail - Customer Churn Risk</h2>"
            f"<p>Customers scored: {report['total_businesses']:,}<br>"
            f"High risk: {report['tier_counts'].get('HIGH', 0):,}<br>"
            f"Watch: {report['tier_counts'].get('WATCH', 0):,}</p>"
            "<table border='1' cellpadding='6'><tr><th>Customer</th><th>Risk</th>"
            f"<th>Tier</th><th>Reason</th></tr>{rows}</table>")
    sendgrid_send(Mail(
        from_email=sender, to_emails=receiver,
        subject=f"DeltaMax V5 Retail - Churn Risk ({report['total_businesses']:,} customers)",
        html_content=body,
    ))



def _read_alert_sent_log() -> list[dict]:
    if not SENT_LOG_PATH.exists():
        return []
    try:
        data = json.loads(SENT_LOG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _record_alert_email(recipient: str, report_type: str, alert_index: int | None) -> dict:
    report_label = "Retail anomaly report" if report_type == "anomaly" else (
        "Individual retail predictive alert" if alert_index is not None else "Retail predictive alerts report")
    alert_count = 1 if alert_index is not None else (
        len(load_forecast_report().get("alerts", [])) if report_type != "anomaly" else 0)
    entry = {
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "recipient": recipient,
        "report_type": report_type,
        "report_label": report_label,
        "alert_count": alert_count,
    }
    with _SENT_LOG_LOCK:
        entries = _read_alert_sent_log()
        entries.insert(0, entry)
        SENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        SENT_LOG_PATH.write_text(json.dumps(entries[:200], indent=2), encoding="utf-8")
    return entry

def _send_alert_page_email(recipient: str, report_type: str = "predictive", alert_index: int | None = None) -> None:
    """Send a selected retail predictive alert/report after an explicit UI click."""
    recipient = str(recipient or "").strip()
    if "@" not in recipient or recipient.startswith("@") or recipient.endswith("@"):
        raise ValueError("Enter a valid recipient email address.")
    _, sender, _ = require_sendgrid_config()

    if report_type == "anomaly":
        report = load_engine_report("anomaly")
        if not report.get("ready"):
            raise RuntimeError(report.get("hint", "The retail anomaly report is unavailable."))
        subject = "DeltaMax Retail - Anomaly Report"
        body = ("<h2>DeltaMax Retail Anomaly Report</h2>"
                f"<p>Records: {int(report.get('total_records', 0)):,}<br>"
                f"Flagged: {int(report.get('flagged_records', 0)):,}<br>"
                f"Flag rate: {float(report.get('flag_rate', 0)):.1f}%</p>")
        items = report.get("by_method", [])
        if items:
            body += "<table border='1' cellpadding='6' cellspacing='0'><tr><th>Check</th><th>Flagged</th></tr>"
            body += "".join(f"<tr><td>{html.escape(str(item.get('label', '')))}</td><td>{int(item.get('flagged', 0))}</td></tr>" for item in items)
            body += "</table>"
    else:
        report = load_forecast_report()
        if not report.get("ready"):
            raise RuntimeError(report.get("hint", "The retail predictive alert report is unavailable."))
        alerts = report.get("alerts", [])
        if alert_index is not None:
            if alert_index < 0 or alert_index >= len(alerts):
                raise ValueError("That predictive alert is no longer available. Refresh the page and try again.")
            alerts = [alerts[alert_index]]
        if not alerts:
            raise RuntimeError("There are no predictive alerts to email.")
        subject = f"DeltaMax Retail - {'Predictive Alert' if alert_index is not None else 'Predictive Alerts Report'} ({len(alerts)})"
        rows = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(alert.get(key, '')))}</td>" for key in
                ("severity", "metric", "trigger", "month", "forecast", "threshold", "reason")) + "</tr>"
            for alert in alerts
        )
        body = ("<h2>DeltaMax Retail Predictive Alerts</h2>"
                "<table border='1' cellpadding='6' cellspacing='0'><tr><th>Severity</th><th>Metric</th>"
                f"<th>Trigger</th><th>Month</th><th>Forecast</th><th>Threshold</th><th>Reason</th></tr>{rows}</table>")
    from sendgrid.helpers.mail import Mail
    sendgrid_send(Mail(
        from_email=sender, to_emails=recipient, subject=subject,
        html_content=f"<html><body style='font-family:Arial,sans-serif'>{body}</body></html>",
    ))


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/agents":
            # The local specialist chat uses this list to render its agent picker.
            # Keep the available domains aligned with the retail dashboard.
            self._json(HTTPStatus.OK, {
                "ready": True,
                "agents": [
                    {"id": "exec", "label": "Executive AI", "icon": "ti-chart-bar", "blurb": "Retail trust, predictive alerts, churn risk, and business performance.", "starters": ["What is the current retail trust score?", "Summarise the most urgent predictive alerts.", "Which retail customers have the highest churn risk?"], "dedicated": False},
                    {"id": "engine", "label": "Core Engine", "icon": "ti-cpu", "blurb": "Anomaly, freshness, drift, and business-rule results.", "starters": ["Which records have the highest anomaly risk?", "What is the average days late per month?", "Summarise drift health by lineage."], "dedicated": False},
                    {"id": "mdm", "label": "MDM & Golden Record", "icon": "ti-database", "blurb": "Retail source records, matching, and golden-record outcomes.", "starters": ["How many duplicate clusters are there?", "Which source system wins the most golden records?", "Summarise MDM trust and match confidence."], "dedicated": False},
                    {"id": "migration", "label": "Cloud Migration", "icon": "ti-cloud", "blurb": "Migration trust, retained records, and lineage lift.", "starters": ["Summarise migration results.", "How many records were discarded and why?", "Which lineage had the largest trust lift?"], "dedicated": False},
                ],
            })
            return
        if parsed.path == "/api/churn-risk":
            params = parse_qs(parsed.query)
            tier = params.get("tier", ["all"])[0]
            limit = int(params.get("limit", ["100"])[0])
            dashboard = params.get("format", [""])[0].lower() == "dashboard"
            self._json(HTTPStatus.OK, load_cached_churn_report(tier, limit, dashboard=dashboard))
            return
        if parsed.path == "/api/mdm":
            self._json(HTTPStatus.OK, load_mdm_report())
            return
        if parsed.path.startswith("/api/mdm/cluster/"):
            cluster_id = parsed.path[len("/api/mdm/cluster/"):]
            self._json(HTTPStatus.OK, load_mdm_cluster(unquote(cluster_id)))
            return
        if parsed.path in ("/api/status", "/api/trust-score"):
            # Return a live status so the frontend disables demo mode automatically
            ts = TRUST_MONTH_PATH
            last_mod = ts.stat().st_mtime if ts.exists() else 0
            label = datetime.fromtimestamp(last_mod).strftime("updated %Y%m%dT%H%M%S") if last_mod else "Live"
            self._json(HTTPStatus.OK, {
                "ready": True,
                "source": "live",
                "last_modified": int(last_mod),
                "label": f"Live · {label}",
            })
            return
        if parsed.path == "/api/migration":
            self._json(HTTPStatus.OK, load_migration_report())
            return
        if parsed.path == "/api/migration/discarded":
            self._json(HTTPStatus.OK, load_migration_discarded())
            return
        if parsed.path == "/api/home":
            self._json(HTTPStatus.OK, load_home_report())
            return
        if parsed.path.startswith("/api/security/finding/"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) < 5:
                self._json(HTTPStatus.BAD_REQUEST, {"ready": False, "error": "A record ID and finding type are required."})
                return
            self._json(HTTPStatus.OK, load_security_finding(unquote(parts[3]), unquote(parts[4])))
            return
        if parsed.path == "/api/security":
            self._json(HTTPStatus.OK, load_security_report())
            return
        engine_routes = {
            "/api/engine/anomaly": "anomaly",
            "/api/engine/drift": "drift",
            "/api/engine/business": "business",
            "/api/engine/freshness": "freshness",
        }
        if parsed.path in engine_routes:
            self._json(HTTPStatus.OK, load_engine_report(engine_routes[parsed.path]))
            return
        if parsed.path == "/api/anomalies":
            report = load_engine_report("anomaly")
            # Compatibility shape used by the dashboard assistant's live context hook.
            if report.get("ready"):
                report["total_anomalies"] = report.get("flagged_records", 0)
                report["by_severity"] = {"CRITICAL": report.get("critical_this_month", 0)}
            self._json(HTTPStatus.OK, report)
            return
        if parsed.path == "/api/forecast":
            self._json(HTTPStatus.OK, load_forecast_report())
            return
        if parsed.path == "/api/forecast/metric-options":
            self._json(HTTPStatus.OK, load_retail_metric_options())
            return
        if parsed.path == "/api/anomaly-records":
            params = parse_qs(parsed.query)
            lineage = params.get("lineage", ["all"])[0]
            business = params.get("business", [""])[0]
            limit = int(params.get("limit", ["100"])[0])
            self._json(HTTPStatus.OK, load_anomaly_records(lineage, business, limit))
            return
        if parsed.path.startswith("/api/anomaly-record/"):
            record_id = parsed.path.rsplit("/", 1)[-1]
            params = parse_qs(parsed.query)
            row_id = params.get("row_id", [None])[0]
            self._json(HTTPStatus.OK, load_anomaly_record(record_id, row_id=row_id))
            return
        if parsed.path == "/api/alerts":
            report = load_forecast_report()
            self._json(HTTPStatus.OK, {"ready": report.get("ready", False), "alerts": report.get("alerts", [])})
            return
        if parsed.path == "/api/alerts/email-config":
            self._json(HTTPStatus.OK, get_sendgrid_config_status())
            return
        if parsed.path == "/api/alerts/sent-log":
            self._json(HTTPStatus.OK, {"entries": _read_alert_sent_log()})
            return
        if parsed.path == "/api/hyperparams":
            self._json(HTTPStatus.OK, get_hyperparams())
            return
        if parsed.path == "/api/hyperparams/history":
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else []
            self._json(HTTPStatus.OK, {"history": history})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path == "/api/alerts/email":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                index = payload.get("alert_index")
                recipient = str(payload.get("recipient", "")).strip()
                report_type = payload.get("report_type", "predictive")
                alert_index = int(index) if index is not None else None
                _send_alert_page_email(recipient, report_type, alert_index)
                entry = _record_alert_email(recipient, report_type, alert_index)
                self._json(HTTPStatus.ACCEPTED, {"sent": True, "entry": entry})
            except SendGridDeliveryError as exc:
                status = {
                    400: HTTPStatus.BAD_REQUEST,
                    401: HTTPStatus.UNAUTHORIZED,
                    403: HTTPStatus.FORBIDDEN,
                    429: HTTPStatus.TOO_MANY_REQUESTS,
                }.get(exc.status_code, HTTPStatus.BAD_GATEWAY)
                self._json(status, {"sent": False, "error": str(exc)})
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"sent": False, "error": str(exc)})
            return
        if self.path == "/api/hyperparams":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._json(HTTPStatus.OK, save_hyperparams(payload))
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": str(exc)})
            return
        if self.path != "/api/churn-risk/email":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            _send_email(payload.get("recipient"))
            self._json(HTTPStatus.ACCEPTED, {"sent": True})
        except Exception as exc:  # API boundary: return a safe message to the UI.
            self._json(HTTPStatus.BAD_REQUEST, {"sent": False, "error": str(exc)})


if __name__ == "__main__":
    port = int(os.getenv("DELTAMAX_API_PORT", "8000"))
    print(f"DeltaMax retail API listening on http://localhost:{port}")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    def warm_churn_dashboard() -> None:
        try:
            load_cached_churn_report(limit=10000, dashboard=True)
        except Exception as exc:
            print(f"Churn dashboard warmup skipped: {exc}")
    threading.Thread(target=warm_churn_dashboard, name="churn-dashboard-warmup", daemon=True).start()
    server.serve_forever()
