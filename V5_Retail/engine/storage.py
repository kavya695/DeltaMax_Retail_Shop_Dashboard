"""Writes result tables to CSV, and optionally uploads them to BigQuery (settings: storage.bigquery)."""
import os
import pandas as pd


def save_tables(cfg, outdir, tables):
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    for name, df in tables.items():
        if df is None:
            continue
        p = os.path.join(outdir, f"{name}.csv")
        df.to_csv(p, index=False)
        paths[name] = p
    bq = cfg["storage"].get("bigquery")
    msgs = []
    if bq:
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=bq.get("project"))
            prefix = bq.get("prefix", cfg["project"])
            for name, df in tables.items():
                if df is None or name in bq.get("skip", ["row_scores"]):
                    continue
                table_id = f"{bq.get('project')}.{bq['dataset']}.{prefix}_{name}"
                client.load_table_from_dataframe(df, table_id,
                    job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")).result()
                msgs.append(f"uploaded {table_id}")
        except Exception as exc:
            msgs.append(f"BigQuery upload failed: {exc}")
    return paths, msgs
