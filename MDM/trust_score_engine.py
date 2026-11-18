"""
trust_score_engine.py — Retail Shop MDM
========================================
Scores each CustomerRecord on three axes:
  • Business rules  (40 pts) — completeness & validity checks
  • Drift           (30 pts) — recency + cross-store consistency
  • Anomaly         (30 pts) — outlier detection on spend / return rate
Total trust score is 0–100.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from models import CustomerRecord


# ─────────────────────────────────────────────────────────────
# RULE ENGINE
# ─────────────────────────────────────────────────────────────

@dataclass
class Rule:
    rule_id:     str
    name:        str
    description: str
    field:       str
    severity:    str
    weight:      float
    validator:   Optional[Callable[[dict], tuple[bool, str]]] = field(default=None, repr=False)


SEVERITY_WEIGHTS = {"CRITICAL": 4.0, "HIGH": 2.5, "MEDIUM": 1.5, "LOW": 1.0}

RULES: list[Rule] = [
    Rule("R-001", "Customer ID present",    "customer_id required",          "customer_id",
         "CRITICAL", 1.0, lambda r: (bool(r.get("customer_id")), "Missing customer_id")),
    Rule("R-002", "Store ID present",       "store_id required",             "store_id",
         "CRITICAL", 1.0, lambda r: (bool(r.get("store_id")), "Missing store_id")),
    Rule("R-003", "City present",           "store_city required",           "store_city",
         "HIGH",     0.8, lambda r: (bool(r.get("store_city")), "Missing store_city")),
    Rule("R-004", "Has transactions",       "at least 1 transaction",        "total_transactions",
         "HIGH",     0.9, lambda r: ((r.get("total_transactions") or 0) > 0, "Zero transactions")),
    Rule("R-005", "Positive spend",         "total_spend > 0",               "total_spend",
         "HIGH",     0.9, lambda r: ((r.get("total_spend") or 0) > 0, "Zero or negative spend")),
    Rule("R-006", "Last purchase date set", "last_purchase_date required",   "last_purchase_date",
         "MEDIUM",   0.7, lambda r: (bool(r.get("last_purchase_date")), "Missing last_purchase_date")),
    Rule("R-007", "Preferred category set", "preferred_category required",   "preferred_category",
         "LOW",      0.5, lambda r: (bool(r.get("preferred_category")), "No preferred category")),
    Rule("R-008", "Return rate reasonable", "return_rate < 50 %",            "return_rate",
         "MEDIUM",   0.7, lambda r: ((r.get("return_rate") or 0) < 0.5, f"High return rate {r.get('return_rate','?')}")),
]


@dataclass
class RuleResult:
    rule_id:      str
    rule_name:    str
    severity:     str
    passed:       bool
    detail:       str
    contribution: float


def evaluate_rules(record_dict: dict) -> tuple[float, list[RuleResult]]:
    total_w, earned_w = 0.0, 0.0
    results = []
    for rule in RULES:
        sw = SEVERITY_WEIGHTS.get(rule.severity, 1.0)
        ew = sw * rule.weight
        total_w += ew
        try:
            passed, detail = rule.validator(record_dict)
        except Exception as exc:
            passed, detail = False, str(exc)
        if passed:
            earned_w += ew
        results.append(RuleResult(rule.rule_id, rule.name, rule.severity, passed, detail, ew))
    score = (earned_w / total_w) * 40 if total_w else 40
    return round(score, 2), results


# ─────────────────────────────────────────────────────────────
# DRIFT ANALYSER  (recency + cross-store consistency)
# ─────────────────────────────────────────────────────────────

@dataclass
class DriftResult:
    freshness_score:    float
    stability_score:    float
    total_drift_score:  float
    detail:             str


def analyse_drift(record_dict: dict, cluster: list[dict]) -> DriftResult:
    # Freshness: how recent is the last purchase?
    freshness = 5.0
    try:
        lp = record_dict.get("last_purchase_date") or ""
        dt = datetime.strptime(lp[:10], "%Y-%m-%d")
        age_days = (datetime.now() - dt).days
        freshness = round(max(0, 1 - age_days / 365) * 15, 2)
    except Exception:
        pass

    # Stability: does this record agree with the cluster majority on key fields?
    stability = 15.0
    check_fields = ["preferred_category", "preferred_payment", "store_city"]
    scores = []
    for f in check_fields:
        vals = [str(c.get(f, "")).strip().lower() for c in cluster if c.get(f)]
        mine = str(record_dict.get(f, "")).strip().lower()
        if not vals or not mine:
            continue
        majority = max(set(vals), key=vals.count)
        scores.append(1 if mine == majority else 0)
    if scores:
        stability = round(statistics.mean(scores) * 15, 2)

    total = round(freshness + stability, 2)
    return DriftResult(freshness, stability, total, f"Fresh={freshness:.1f} Stable={stability:.1f}")


# ─────────────────────────────────────────────────────────────
# ANOMALY DETECTOR
# ─────────────────────────────────────────────────────────────

@dataclass
class AnomalyResult:
    anomaly_score: float
    flags:         list[str]
    detail:        str


def detect_anomalies(record_dict: dict, cluster: list[dict]) -> AnomalyResult:
    flags: list[str] = []

    # Numeric outliers on spend and avg_order_value
    for f in ["total_spend", "avg_order_value"]:
        vals = [float(c[f]) for c in cluster if c.get(f) is not None]
        mine = record_dict.get(f)
        if mine is None or len(vals) < 3:
            continue
        mean = statistics.mean(vals)
        std  = statistics.stdev(vals) if len(vals) > 1 else 0
        if std > 0 and abs(float(mine) - mean) / std > 3.0:
            flags.append(f"OUTLIER:{f}")

    # Very high return rate
    rr = record_dict.get("return_rate") or 0
    if rr > 0.3:
        flags.append(f"HIGH_RETURN_RATE:{rr:.0%}")

    # Zero spend but has transactions
    if (record_dict.get("total_spend") or 0) <= 0 and (record_dict.get("total_transactions") or 0) > 0:
        flags.append("ZERO_SPEND_WITH_TRANSACTIONS")

    score = max(0, 30 - len(flags) * 5)
    return AnomalyResult(score, flags, f"{len(flags)} anomalies")


# ─────────────────────────────────────────────────────────────
# TRUST SCORE REPORT
# ─────────────────────────────────────────────────────────────

@dataclass
class TrustScoreReport:
    record_id:          str
    source_system:      str
    rules_score:        float
    drift_score:        float
    anomaly_score:      float
    trust_score:        float
    is_golden:          bool
    survivorship_reason: str
    rule_results:       list
    drift_result:       DriftResult
    anomaly_result:     AnomalyResult


# ─────────────────────────────────────────────────────────────
# MAIN ENGINE
# ─────────────────────────────────────────────────────────────

class TrustScoreEngine:

    def score_cluster(self, records: list[CustomerRecord]) -> list[TrustScoreReport]:
        dicts = [r.__dict__ for r in records]
        reports: list[TrustScoreReport] = []

        for r in records:
            rd = r.__dict__
            rules_score, rule_results = evaluate_rules(rd)
            drift_result              = analyse_drift(rd, dicts)
            anomaly_result            = detect_anomalies(rd, dicts)

            trust = round(rules_score + drift_result.total_drift_score + anomaly_result.anomaly_score, 2)
            r.trust_score = trust

            reports.append(TrustScoreReport(
                record_id=r.record_id,
                source_system=r.source_system,
                rules_score=rules_score,
                drift_score=drift_result.total_drift_score,
                anomaly_score=anomaly_result.anomaly_score,
                trust_score=trust,
                is_golden=False,
                survivorship_reason="",
                rule_results=rule_results,
                drift_result=drift_result,
                anomaly_result=anomaly_result,
            ))

        # Mark the highest-scoring record as golden
        if reports:
            winner = max(reports, key=lambda x: x.trust_score)
            winner.is_golden = True
            winner.survivorship_reason = (
                f"Highest trust score ({winner.trust_score:.1f}) "
                f"among {len(reports)} store record(s)"
            )

        return reports

    def score_all(self, all_records: list[CustomerRecord]) -> list[TrustScoreReport]:
        from collections import defaultdict
        clusters: dict[str, list[CustomerRecord]] = defaultdict(list)
        for r in all_records:
            clusters[r.cluster_id].append(r)
        all_reports: list[TrustScoreReport] = []
        for cluster_records in clusters.values():
            all_reports.extend(self.score_cluster(cluster_records))
        return all_reports
