"""ARIMA forecasts of monthly metrics (and the trust score itself)."""
import warnings
import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.arima.model import ARIMA
    HAVE_ARIMA = True
except Exception:       # statsmodels not installed -> moving-average fallback
    HAVE_ARIMA = False

CANDIDATES = [((0, 1, 0), "t"), ((0, 1, 0), "n"), ((1, 1, 0), "n"), ((0, 1, 1), "n"), ((1, 1, 1), "n"),
              ((1, 1, 0), "t"), ((0, 1, 1), "t"), ((1, 0, 0), "c"), ((1, 0, 1), "c"), ((2, 1, 1), "n")]


def next_months(last, n):
    p = pd.Period(last, "M")
    return [str(p + i) for i in range(1, n + 1)]


def _fit(y, order, trend):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ARIMA(np.asarray(y, float), order=order, trend=trend).fit()


def _best(y):
    best = None
    for order, trend in CANDIDATES:
        try:
            m = _fit(y, order, trend)
            if np.isfinite(m.aic) and (best is None or m.aic < best[0]):
                best = (m.aic, order, trend, m)
        except Exception:
            continue
    return best


def forecast_series(y, horizon, min_points):
    """y: list of floats in time order. Returns dict with forecast, CI, method, mape, one-step deviation."""
    n = len(y)
    y = np.asarray(y, float)
    res = {"method": None, "order": "", "mape": np.nan, "pred": None, "lo": None, "hi": None, "dev_pct": np.nan}
    if n < 3:
        return None
    if HAVE_ARIMA and n >= min_points:
        best = _best(y)
        if best:
            _, order, trend, model = best
            fc = model.get_forecast(horizon).summary_frame(alpha=0.05)
            res.update(method="ARIMA", order=f"{order}/{trend}", pred=fc["mean"].values,
                       lo=fc["mean_ci_lower"].values, hi=fc["mean_ci_upper"].values)
            try:                                  # 2-point holdout backtest
                m2 = _fit(y[:-2], order, trend)
                p2 = m2.forecast(2)
                res["mape"] = float(np.mean(np.abs((y[-2:] - p2) / np.where(y[-2:] == 0, np.nan, y[-2:]))) * 100)
                m1 = _fit(y[:-1], order, trend)
                p1 = float(m1.forecast(1)[0])
                res["dev_pct"] = (y[-1] - p1) / abs(p1) * 100 if p1 else np.nan
            except Exception:
                pass
            return res
    pred = float(np.mean(y[-3:]))
    res.update(method="MOVING_AVG_3", pred=np.repeat(pred, horizon), lo=None, hi=None)
    base = float(np.mean(y[-4:-1])) if n >= 4 else pred
    res["dev_pct"] = (y[-1] - base) / abs(base) * 100 if base else np.nan
    return res


def _monthly(df, col, agg, months, group=None):
    if group is None:
        s = df.groupby("data_month")[col].agg(agg).reindex(months)
        return {("ALL", "ALL"): s}
    out = {}
    for key, sub in df.groupby(group):
        out[(None, key)] = sub.groupby("data_month")[col].agg(agg).reindex(months)
    return out


def run_forecasts(df, cfg, monthly_pillars_df, pillar_cols):
    fc = cfg["forecast"]
    rows, devs = [], []
    months = sorted(df["data_month"].unique())
    horizon, minp = fc["horizon"], fc["min_points"]
    ent_col = {e["type"]: e["column"] for e in cfg["entities"]}
    series_jobs = []   # (name, level, entity_id, series, spec)

    for spec in fc["series"]:
        level = spec.get("level", "ALL")
        agg = spec.get("agg", "mean")
        if level == "ALL":
            s = df.groupby("data_month")[spec["column"]].agg(agg).reindex(months)
            series_jobs.append((spec["name"], "ALL", "ALL", s, spec))
        else:
            if level not in ent_col:
                raise ValueError(f"forecast series '{spec['name']}' uses unknown level '{level}'")
            for eid, sub in df.groupby(ent_col[level]):
                s = sub.groupby("data_month")[spec["column"]].agg(agg).reindex(months)
                series_jobs.append((spec["name"], level, str(eid), s, spec))

    if fc["include_trust"]:
        mp = monthly_pillars_df.groupby("data_month")
        w = df.groupby("data_month")
        for col in pillar_cols + ["overall_trust_score"]:
            s = w[col].mean().reindex(months)
            thr = cfg["alerts"]["trust_below"] if col == "overall_trust_score" else cfg["alerts"]["pillar_below"]
            series_jobs.append((col, "ALL", "ALL", s, {"name": col, "floor": thr, "bounds": [0, 1], "kind": "trust"}))

    for name, level, eid, s, spec in series_jobs:
        agg = spec.get("agg", "mean")
        first = s.first_valid_index()          # ignore months before this shop/product/etc. existed
        if first is None:
            continue
        s = s.loc[first:]
        if agg in ("sum", "count"):
            s = s.fillna(0)
        s = s.dropna()
        if len(s) < 3:
            continue
        r = forecast_series(s.values, horizon, spec.get("min_points", minp))
        if r is None:
            continue
        fut = next_months(s.index[-1], horizon)
        lo_b, hi_b = (spec.get("bounds") or [None, None])
        pred = np.asarray(r["pred"], float)
        lo = None if r["lo"] is None else np.asarray(r["lo"], float)
        hi = None if r["hi"] is None else np.asarray(r["hi"], float)
        if lo_b is not None:
            pred = np.clip(pred, lo_b, hi_b)
            lo = None if lo is None else np.clip(lo, lo_b, hi_b)
            hi = None if hi is None else np.clip(hi, lo_b, hi_b)
        for m, v in s.items():
            rows.append(dict(series=name, level=level, entity_id=eid, month=m, kind="actual", value=float(v),
                             lower=np.nan, upper=np.nan, method="", order="", backtest_mape_pct=np.nan))
        for i, m in enumerate(fut):
            rows.append(dict(series=name, level=level, entity_id=eid, month=m, kind="forecast", value=float(pred[i]),
                             lower=np.nan if lo is None else float(lo[i]), upper=np.nan if hi is None else float(hi[i]),
                             method=r["method"], order=r["order"], backtest_mape_pct=r["mape"]))
        devs.append(dict(series=name, level=level, entity_id=eid, last_month=s.index[-1],
                         last_actual=float(s.iloc[-1]), deviation_pct=r["dev_pct"], spec=spec,
                         forecast=pred, future_months=fut, method=r["method"]))
    return pd.DataFrame(rows), devs
