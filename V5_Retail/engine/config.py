"""Loads a project settings file (YAML) and fills in defaults."""
import copy
import os
import yaml

DEFAULTS = {
    "project": "unnamed",
    "description": "",
    "input": {"path": None},
    "columns": {"month": None, "date": None, "lineage": None},
    "lineage": {"historical": "HISTORICAL", "previous": "PREVIOUS_MONTH", "current": "CURRENT_MONTH"},
    "features": [],
    "not_null": [],
    "derived": [],          # [{name, column, by, how: ratio_to_group_median}]
    "duplicate_key": [],
    "entities": [],
    "rules": [],
    "anomaly": {
        "weights": {"iqr": 0.2, "missing": 0.1, "duplicate": 0.05, "isolation_forest": 0.2, "knn": 0.15},
        "iqr_multiplier": 3.0,
        "iso_contamination": 0.02,
        "iso_estimators": 100,
        "knn_k": 5,
        "knn_percentile": 0.99,
        "max_fit_rows": 30000,
        "random_seed": 42,
    },
    "drift": {
        "enabled": True,
        "weights": {"mahalanobis": 0.7, "psi": 0.2, "welch": 0.1},
        "psi_scale": 0.25,
        "mahal_scale": 3.0,
        "welch_p": 0.01,
        "welch_min_effect": 0.2,
    },
    "business": {
        "method": "severity_weighted",   # or "count"
        "severity_weights": {"CRITICAL": 0.5, "HIGH": 0.3, "MEDIUM": 0.15, "LOW": 0.05},
        "max_flags": None,                # used by method "count"
    },
    "freshness": {"enabled": False, "tau": 5.0},
    "scoring": {"weights": {"anomaly": 0.40, "drift": 0.25, "business": 0.20, "freshness": 0.15}},
    "forecast": {
        "enabled": True, "horizon": 3, "min_points": 8,
        "include_trust": True, "deviation_alert_pct": 30.0, "series": [],
    },
    "alerts": {"trust_below": 0.75, "pillar_below": 0.75, "mom_drop": 0.05, "high_risk_trust": 0.6, "max_entity_alerts": 25},
    "churn": {"enabled": False},
    "notify": {"email": False, "subject_prefix": "DeltaMax"},
    "storage": {"bigquery": None},
    "output_dir": "results",
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(ValueError):
    pass


def load_config(path, root):
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = _merge(DEFAULTS, raw)
    # The retail UI stores validated tuning changes separately from the base
    # YAML so the checked-in business rules remain easy to review.
    runtime_path = os.path.join(root, "results", "retail", "hyperparams.json")
    if cfg.get("project") == "retail" and os.path.exists(runtime_path):
        with open(runtime_path, "r", encoding="utf-8") as fh:
            runtime = yaml.safe_load(fh) or {}
        cfg = _merge(cfg, runtime)
    cfg["_root"] = root
    cfg["_config_path"] = os.path.abspath(path)
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    problems = []
    if not cfg["input"].get("path"):
        problems.append("input.path is required")
    if not (cfg["columns"].get("month") or cfg["columns"].get("date")):
        problems.append("columns.month or columns.date is required")
    if not cfg["features"]:
        problems.append("features: list at least one numeric column to monitor")
    if not cfg["entities"]:
        problems.append("entities: define at least one entity (type + column)")
    for i, e in enumerate(cfg["entities"]):
        if "type" not in e or "column" not in e:
            problems.append(f"entities[{i}] needs 'type' and 'column'")
    for i, r in enumerate(cfg["rules"]):
        for k in ("name", "severity", "when"):
            if k not in r:
                problems.append(f"rules[{i}] is missing '{k}'")
        if str(r.get("severity", "")).upper() not in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            problems.append(f"rules[{i}] severity must be CRITICAL/HIGH/MEDIUM/LOW")
    f = cfg["freshness"]
    if f.get("enabled") and not (f.get("days_late_column") or f.get("expected_date_column")
                                  or f.get("due_day_of_next_month")):
        problems.append("freshness.enabled needs days_late_column, expected_date_column "
                        "or due_day_of_next_month + actual_date_column")
    if problems:
        raise ConfigError(f"Settings file {cfg.get('_config_path')} has problems:\n  - " + "\n  - ".join(problems))


def resolve_path(cfg, p):
    return p if os.path.isabs(p) else os.path.join(cfg["_root"], p)
