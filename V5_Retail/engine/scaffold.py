"""Drafts a settings file for a NEW project by profiling its CSV. A human must still review it."""
import os
import pandas as pd


def draft_settings(csv_path, project, out_path):
    df = pd.read_csv(csv_path, nrows=20000, low_memory=False)
    cols = list(df.columns)
    month = next((c for c in cols if "month" in c.lower()), None)
    date = next((c for c in cols if "date" in c.lower() and c != month), None)
    lineage = next((c for c in cols if "lineage" in c.lower()), None)
    ids = [c for c in cols if c.lower().endswith("id") or c.lower().endswith("_id") or "_id" in c.lower()]
    num = [c for c in cols if pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique() > 5 and c not in ids
           and not any(k in c.lower() for k in ("year", "idx", "index", "zip"))]
    lines = [f"# DRAFT settings for '{project}' - generated from {os.path.basename(csv_path)}.",
             "# Review every TODO. Rules and thresholds must come from the business owner.",
             f"project: {project}", f"description: TODO describe this project", "",
             "input:", f"  path: data/{os.path.basename(csv_path)}", "", "columns:"]
    lines.append(f"  month: {month}" if month else f"  date: {date or 'TODO_date_column'}   # TODO: month column or date column")
    lines.append(f"  lineage: {lineage}" if lineage else "  lineage: null    # null = engine labels last month CURRENT, one before PREVIOUS, rest HISTORICAL")
    lines += ["", "entities:   # TODO: what do you report trust for? one entry per kind of thing"]
    for i, c in enumerate(ids[:3]):
        lines += [f"  - type: {c.upper().replace('_ID','').replace('ID','') or 'ENTITY'}", f"    column: {c}", "    grain: month"]
    if not ids:
        lines += ["  - type: TODO", "    column: TODO"]
    dk = ids[0] if ids else "TODO"
    lines += ["", f"duplicate_key: [{dk}]   # TODO: which column(s) make a row unique?",
              "not_null: []", "", "features:   # numeric columns to monitor for strange values and drift"]
    lines += [f"  - {c}" for c in num[:25]]
    lines += ["", "rules:   # TODO: ask the business owner. 'when' is TRUE when the row is WRONG.",
              "  - name: example_negative_value", "    severity: CRITICAL",
              f"    when: \"{num[0]} < 0\"" if num else "    when: \"TODO\"", "",
              "freshness:", "  enabled: false   # TODO: turn on once you know when data is due",
              "", "forecast:", "  series:   # TODO: metrics to forecast monthly"]
    for c in num[:2]:
        lines += [f"    - {{name: {c}_monthly, column: {c}, agg: mean, level: ALL}}"]
    lines += ["", "alerts:", "  trust_below: 0.75", "", "notify:", "  email: false", ""]
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    open(out_path, "w").write("\n".join(lines))
    return out_path
