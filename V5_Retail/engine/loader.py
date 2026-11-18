"""Reads the input file, validates it against the settings file, labels lineage."""
import numpy as np
import pandas as pd
from .config import resolve_path, ConfigError

LIN_H, LIN_P, LIN_C = "HISTORICAL", "PREVIOUS_MONTH", "CURRENT_MONTH"


def load_data(cfg):
    warnings = []
    path = resolve_path(cfg, cfg["input"]["path"])
    try:
        df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path, low_memory=False)
    except FileNotFoundError:
        raise ConfigError(f"Input file not found: {path}")

    c = cfg["columns"]
    needed = set(cfg["features"]) | set(cfg["not_null"]) | set(cfg["duplicate_key"])
    needed |= {e["column"] for e in cfg["entities"]}
    for k in ("month", "date", "lineage"):
        if c.get(k):
            needed.add(c[k])
    for dv in cfg["derived"]:
        needed |= {dv["column"], dv["by"]}
    for sc in cfg["forecast"]["series"]:
        needed.add(sc["column"])
    f = cfg["freshness"]
    for k in ("days_late_column", "expected_date_column", "actual_date_column"):
        if f.get("enabled") and f.get(k):
            needed.add(f[k])
    missing = sorted(needed - set(df.columns))
    if missing:
        raise ConfigError(f"Input file is missing columns named in the settings file: {missing}")

    # ---- month column (YYYY-MM) ----
    if c.get("month"):
        df["data_month"] = df[c["month"]].astype(str).str.slice(0, 7)
    else:
        d = pd.to_datetime(df[c["date"]], errors="coerce")
        bad = int(d.isna().sum())
        if bad:
            warnings.append(f"{bad} rows have an unreadable date in '{c['date']}' and were dropped")
        df = df.loc[d.notna()].copy()
        df["data_month"] = d[d.notna()].dt.strftime("%Y-%m")
    if not df["data_month"].str.match(r"^\d{4}-\d{2}$").all():
        raise ConfigError("Month values must look like YYYY-MM (e.g. 2026-03)")

    # ---- lineage ----
    lin = cfg["lineage"]
    if c.get("lineage"):
        mapping = {lin["historical"]: LIN_H, lin["previous"]: LIN_P, lin["current"]: LIN_C}
        df["data_lineage"] = df[c["lineage"]].map(mapping)
        unknown = int(df["data_lineage"].isna().sum())
        if unknown:
            warnings.append(f"{unknown} rows have a lineage value not in the settings file and were treated as HISTORICAL")
            df["data_lineage"] = df["data_lineage"].fillna(LIN_H)
    else:
        months = sorted(df["data_month"].unique())
        cur = months[-1]
        prev = months[-2] if len(months) > 1 else None
        df["data_lineage"] = np.where(df["data_month"] == cur, LIN_C,
                                      np.where(df["data_month"] == prev, LIN_P, LIN_H))
        warnings.append(f"Lineage auto-derived from months: current={cur}, previous={prev}")

    for col in cfg["features"]:
        before = df[col].notna().sum()
        df[col] = pd.to_numeric(df[col], errors="coerce")
        lost = int(before - df[col].notna().sum())
        if lost:
            warnings.append(f"{lost} non-numeric values in feature '{col}' were treated as missing")

    for dv in cfg["derived"]:             # e.g. price compared with that product's usual price
        how = dv.get("how", "ratio_to_group_median")
        if how != "ratio_to_group_median":
            raise ConfigError(f"derived '{dv['name']}': unsupported how={how!r}")
        col = pd.to_numeric(df[dv["column"]], errors="coerce")
        med = col.groupby(df[dv["by"]]).transform("median")
        df[dv["name"]] = (col / med.where(med != 0)).astype(float)

    df = df.reset_index(drop=True)
    df["row_id"] = np.arange(len(df))
    if (df["data_lineage"] == LIN_H).sum() < 50:
        warnings.append("Fewer than 50 HISTORICAL rows: baseline for anomaly/drift is weak")
    return df, warnings
