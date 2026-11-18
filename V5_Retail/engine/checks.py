"""The four pillars: anomaly, drift, business rules, freshness. Domain-agnostic."""
import json
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import NearestNeighbors
from .config import ConfigError
from .loader import LIN_H, LIN_P, LIN_C

_LIN_KEY = {"historical": LIN_H, "previous": LIN_P, "current": LIN_C}


def baseline_mask(df, cfg):
    keys = cfg["anomaly"].get("baseline", ["historical"])
    wanted = [_LIN_KEY.get(k, k) for k in keys]
    m = df["data_lineage"].isin(wanted)
    return m if m.sum() >= 20 else pd.Series(True, index=df.index)


# ------------------------------------------------------------------ business rules
def apply_rules(df, cfg):
    out = pd.DataFrame(index=df.index)
    counts = {s: pd.Series(0, index=df.index) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    names = {}
    for r in cfg["rules"]:
        try:
            hit = df.eval(r["when"], engine="python")
        except Exception as exc:
            raise ConfigError(f"Rule '{r['name']}' could not be evaluated: {r['when']!r} -> {exc}")
        hit = pd.Series(hit, index=df.index).fillna(False).astype(bool)
        sev = r["severity"].upper()
        counts[sev] += hit.astype(int)
        names[r["name"]] = hit
    for s, v in counts.items():
        out[f"{s}_flags"] = v
    out["rule_violations"] = sum(counts.values()) if counts else 0
    out["rules_broken"] = ""
    if names:
        broken = pd.DataFrame(names)
        out["rules_broken"] = broken.apply(lambda row: "; ".join(row.index[row.values]), axis=1)

    b = cfg["business"]
    if b["method"] == "count":
        mx = b.get("max_flags") or max(1, len(cfg["rules"]))
        out["business_health"] = (1 - out["rule_violations"] / mx).clip(0, 1)
    else:
        w = b["severity_weights"]
        pen = sum(out[f"{s}_flags"] * w.get(s, 0) for s in counts)
        out["business_health"] = (1 - pen).clip(0, 1)
    rule_hits = {n: int(h.sum()) for n, h in names.items()}
    return out, rule_hits


# ------------------------------------------------------------------ anomaly
def _prep(df, feats, base):
    X = df[feats].apply(pd.to_numeric, errors="coerce")
    med = X[base].median().fillna(0)
    Xf = X.fillna(med)
    mu, sd = Xf[base].mean(), Xf[base].std()
    keep = [c for c in feats if np.isfinite(sd[c]) and sd[c] > 0]
    Z = ((Xf[keep] - mu[keep]) / sd[keep]).clip(-10, 10)
    return X, Z, keep


def anomaly_flags(df, cfg, base):
    a, feats = cfg["anomaly"], cfg["features"]
    w = a["weights"]
    rng = a["random_seed"]
    out = pd.DataFrame(index=df.index)
    X, Z, keep = _prep(df, feats, base)

    # IQR (baseline quartiles)
    q1, q3 = X[base].quantile(.25), X[base].quantile(.75)
    iqr = q3 - q1
    k = a["iqr_multiplier"]
    cols = [c for c in feats if iqr[c] > 0]
    lo, hi = q1[cols] - k * iqr[cols], q3[cols] + k * iqr[cols]
    out["iqr_flag"] = ((X[cols] < lo) | (X[cols] > hi)).any(axis=1).astype(int)

    # Persist the exact per-field fences used above so the API can explain
    # each result without recomputing the detector for every modal click.
    iqr_diagnostics = []
    for position in range(len(df)):
        fields = []
        for feature in feats:
            if feature not in cols:
                continue
            value = X[feature].iloc[position]
            q1_value, q3_value = float(q1[feature]), float(q3[feature])
            spread = float(iqr[feature])
            lower, upper = float(lo[feature]), float(hi[feature])
            evaluated = bool(pd.notna(value) and np.isfinite(float(value)))
            actual = float(value) if evaluated else None
            flagged = bool(evaluated and (actual < lower or actual > upper))
            fields.append({
                "field": str(feature), "actual_value": actual, "q1": q1_value,
                "q3": q3_value, "iqr": spread, "multiplier": float(k),
                "lower_fence": lower, "upper_fence": upper,
                "baseline_count": int(base.sum()),
                "evaluated": evaluated, "fired": flagged,
                "status": "FLAGGED" if flagged else "PASSED" if evaluated else "N/A",
            })
        iqr_diagnostics.append(json.dumps(fields, allow_nan=False, separators=(",", ":")))
    out["iqr_details_json"] = iqr_diagnostics

    # Missing
    miss_cols = list(dict.fromkeys(feats + cfg["not_null"]))
    out["missing_flag"] = df[miss_cols].isna().any(axis=1).astype(int)
    out["missing_fields_json"] = [json.dumps(
        [str(column) for column in miss_cols if pd.isna(df[column].iloc[position])],
        separators=(",", ":")) for position in range(len(df))]

    # Duplicates
    if cfg["duplicate_key"]:
        out["duplicate_flag"] = df.duplicated(subset=cfg["duplicate_key"], keep=False).astype(int)
        duplicate_matches = [[] for _ in range(len(df))]
        duplicate_counts = np.zeros(len(df), dtype=int)
        grouped = df.groupby(cfg["duplicate_key"], dropna=False, sort=False).indices
        for positions in grouped.values():
            positions = list(map(int, positions))
            if len(positions) < 2:
                continue
            for position in positions:
                others = [other for other in positions if other != position]
                duplicate_counts[position] = len(others)
                duplicate_matches[position] = [{
                    "record_id": (str(df["record_id"].iloc[other]) if "record_id" in df and pd.notna(df["record_id"].iloc[other]) else None),
                    "transaction_id": (str(df["transaction_id"].iloc[other]) if "transaction_id" in df and pd.notna(df["transaction_id"].iloc[other]) else None),
                } for other in others]
        out["duplicate_match_count"] = duplicate_counts
        out["duplicate_matches_json"] = [json.dumps(matches, separators=(",", ":")) for matches in duplicate_matches]
    else:
        out["duplicate_flag"] = 0
        out["duplicate_match_count"] = 0
        out["duplicate_matches_json"] = "[]"

    # Fit sample (speed)
    base_idx = np.flatnonzero(base.values)
    if len(base_idx) > a["max_fit_rows"]:
        base_idx = np.random.default_rng(rng).choice(base_idx, a["max_fit_rows"], replace=False)
    Zv = Z.values

    # Isolation Forest
    if keep and w.get("isolation_forest", 0) > 0:
        iso = IsolationForest(n_estimators=a["iso_estimators"], contamination=a["iso_contamination"],
                              random_state=rng, n_jobs=-1).fit(Zv[base_idx])
        isolation_scores = iso.decision_function(Zv)
        out["isolation_forest_score"] = isolation_scores
        out["isolation_forest_fit_count"] = int(len(base_idx))
        out["isolation_forest_flag"] = (isolation_scores < 0).astype(int)
    else:
        out["isolation_forest_score"] = np.nan
        out["isolation_forest_fit_count"] = 0
        out["isolation_forest_flag"] = 0

    # KNN distance
    if keep and w.get("knn", 0) > 0 and len(base_idx) > a["knn_k"] + 1:
        kk = a["knn_k"]
        nn = NearestNeighbors(n_neighbors=kk + 1, n_jobs=-1).fit(Zv[base_idx])
        dist, _ = nn.kneighbors(Zv)
        in_fit = np.zeros(len(df), dtype=bool)
        in_fit[base_idx] = True
        mean_d = np.where(in_fit, dist[:, 1:].mean(axis=1), dist[:, :kk].mean(axis=1))
        thr = np.quantile(mean_d[in_fit], a["knn_percentile"])
        out["knn_mean_distance"] = mean_d
        out["knn_distance_threshold"] = float(thr)
        out["knn_reference_count"] = int(in_fit.sum())
        out["knn_flag"] = (mean_d > thr).astype(int)
    else:
        out["knn_mean_distance"] = np.nan
        out["knn_distance_threshold"] = np.nan
        out["knn_reference_count"] = 0
        out["knn_flag"] = 0
    out["knn_neighbours"] = int(a["knn_k"])
    out["knn_percentile"] = float(a["knn_percentile"])

    risk = (w.get("iqr", 0) * out["iqr_flag"] + w.get("missing", 0) * out["missing_flag"]
            + w.get("duplicate", 0) * out["duplicate_flag"]
            + w.get("isolation_forest", 0) * out["isolation_forest_flag"]
            + w.get("knn", 0) * out["knn_flag"])
    out["overall_anomaly_risk"] = risk.clip(0, 1)
    out["anomaly_health"] = 1 - out["overall_anomaly_risk"]
    return out


# ------------------------------------------------------------------ drift
def _psi(base_vals, cur_vals, bins=10):
    b, c = base_vals.dropna().values, cur_vals.dropna().values
    if len(b) < 20 or len(c) < 20:
        return np.nan
    edges = np.unique(np.quantile(b, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return np.nan
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(b, edges)[0] / len(b)
    a = np.histogram(c, edges)[0] / len(c)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def drift_health(df, cfg, base):
    d, feats = cfg["drift"], cfg["features"]
    out = pd.DataFrame(index=df.index)
    info = {}
    if not d["enabled"]:
        out["drift_health"] = 1.0
        return out, info
    X, Z, keep = _prep(df, feats, base)
    if not keep:
        out["drift_health"] = 1.0
        return out, info

    Zb = Z[base].values
    cov = np.atleast_2d(np.cov(Zb, rowvar=False)) + 1e-6 * np.eye(len(keep))
    inv = np.linalg.pinv(cov)
    d2 = np.einsum("ij,jk,ik->i", Z.values, inv, Z.values)
    q99 = np.quantile(d2[base.values], 0.99)
    mahal_risk = np.clip(d2 / (d["mahal_scale"] * max(q99, 1e-9)), 0, 1)
    mahal_health = 1 - mahal_risk

    w = d["weights"]
    out["drift_health"] = np.nan
    for lin in (LIN_H, LIN_P, LIN_C):
        m = (df["data_lineage"] == lin).values
        if not m.any():
            continue
        psi_h = welch_h = 1.0
        drifted, psi_vals = [], {}
        cur_mask = m & ~base.values          # rows not used to build the baseline
        if True:
            if cur_mask.sum() >= 20:
                psis = []
                for c in keep:
                    p = _psi(X.loc[base, c], X.loc[cur_mask, c])
                    if np.isfinite(p):
                        psis.append(p)
                        psi_vals[c] = round(p, 4)
                if psis:
                    psi_h = float(np.clip(1 - np.mean(psis) / d["psi_scale"], 0, 1))
                for c in keep:
                    bv, cv = X.loc[base, c].dropna(), X.loc[cur_mask, c].dropna()
                    if len(bv) > 5 and len(cv) > 5:
                        _, p = stats.ttest_ind(cv, bv, equal_var=False)
                        pooled = np.sqrt((bv.var() + cv.var()) / 2)
                        eff = abs(cv.mean() - bv.mean()) / pooled if pooled > 0 else 0
                        if p < d["welch_p"] and eff > d["welch_min_effect"]:
                            drifted.append(c)
                welch_h = 1 - len(drifted) / len(keep)
        row_h = (w["mahalanobis"] * mahal_health[m] + w["psi"] * psi_h + w["welch"] * welch_h)
        out.loc[m, "drift_health"] = row_h / (w["mahalanobis"] + w["psi"] + w["welch"])
        info[lin] = {"records": int(m.sum()), "psi_health": round(psi_h, 4), "welch_health": round(welch_h, 4),
                     "mahalanobis_health": round(float(mahal_health[m].mean()), 4),
                     "drifted_features": drifted,
                     "top_psi": dict(sorted(psi_vals.items(), key=lambda kv: -kv[1])[:5])}
    out["drift_health"] = out["drift_health"].fillna(1.0).clip(0, 1)
    return out, info


# ------------------------------------------------------------------ freshness
def freshness_health(df, cfg):
    f = cfg["freshness"]
    if not f["enabled"]:
        return None
    if f.get("days_late_column"):
        late = pd.to_numeric(df[f["days_late_column"]], errors="coerce").fillna(0).clip(lower=0)
    elif f.get("expected_date_column"):
        exp = pd.to_datetime(df[f["expected_date_column"]], errors="coerce")
        act = pd.to_datetime(df[f["actual_date_column"]], errors="coerce")
        late = (act - exp).dt.days.fillna(0).clip(lower=0)
    else:
        month_start = pd.to_datetime(df["data_month"] + "-01")
        exp = month_start + pd.offsets.MonthBegin(1) + pd.to_timedelta(f["due_day_of_next_month"] - 1, unit="D")
        act = pd.to_datetime(df[f["actual_date_column"]], errors="coerce")
        late = (act - exp).dt.days.fillna(0).clip(lower=0)
    out = pd.DataFrame(index=df.index)
    out["freshness_days_late_clean"] = late
    out["freshness_health"] = np.exp(-late / f["tau"])
    return out
