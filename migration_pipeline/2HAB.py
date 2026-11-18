"""
HAB Raw Combiner (SIMPLIFIED)

This script ONLY:
• Loads Historical months (Jan–Jun)
• Loads Previous month (July)
• Loads Current month (Aug)
• Appends everything together
• Adds ONE column -> data_lineage
• Saves ONE combined CSV

NO extra columns.
NO rules.
NO trust score.
NO timestamps.
NO hashes.
NO parquet.

Storage
-------
Everything is S3-only — no local disk reads or writes on this VM.
  - Monthly input CSVs are read from:
        s3://<S3_BUCKET>/<S3_PREFIX>/credit_bureau_data_<month>_.csv
    (the same bucket/folder the data generator now writes to)
  - The combined output is written to:
        s3://<S3_BUCKET>/<S3_PREFIX>/hab_combined.csv

Defaults to bucket 'deltamax-dashboard', folder 'migration-pipeline'.
Override either via env vars S3_BUCKET / S3_PREFIX if needed.

Auth uses the standard AWS credential chain (env vars, ~/.aws/credentials,
or an attached IAM role) — no keys are read or stored here. Set:
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION
(omit the key/secret entirely if the host has an IAM role attached).
"""

import logging
import os
import io
import argparse
import pandas as pd

# Initialize logging IMMEDIATELY before any other calls
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)

# ============================================================
# AWS S3 CONFIG + HELPERS (no local disk reads/writes)
# ============================================================
S3_BUCKET = os.environ.get("S3_BUCKET", "deltamax-dashboard").strip()
S3_PREFIX = os.environ.get("S3_PREFIX", "migration-pipeline").strip().strip("/")


def _s3_key(filename: str) -> str:
    return f"{S3_PREFIX}/{filename}" if S3_PREFIX else filename


def read_csv_from_s3(filename: str) -> pd.DataFrame:
    """Read a CSV object from s3://S3_BUCKET/S3_PREFIX/filename into a
    DataFrame. Returns an empty DataFrame if the object doesn't exist or
    can't be read — mirrors the old 'file not found' local-disk behaviour."""
    key = _s3_key(filename)
    try:
        import boto3
        client = boto3.client("s3")
        obj = client.get_object(Bucket=S3_BUCKET, Key=key)
        return pd.read_csv(io.BytesIO(obj["Body"].read()), low_memory=False)
    except client.exceptions.NoSuchKey:
        logging.warning("Not found in S3: s3://%s/%s", S3_BUCKET, key)
        return pd.DataFrame()
    except Exception as e:
        logging.warning("S3 read failed for s3://%s/%s: %s", S3_BUCKET, key, e)
        return pd.DataFrame()


def upload_df_to_s3(df: pd.DataFrame, filename: str) -> bool:
    """Serialise a DataFrame to CSV in memory and upload it to
    s3://S3_BUCKET/S3_PREFIX/filename. Never touches local disk."""
    key = _s3_key(filename)
    try:
        import boto3
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        client = boto3.client("s3")
        client.put_object(
            Bucket=S3_BUCKET, Key=key,
            Body=buf.getvalue().encode("utf-8"),
            ContentType="text/csv",
        )
        logging.info("Uploaded: s3://%s/%s  (%d rows)", S3_BUCKET, key, len(df))
        return True
    except Exception as e:
        logging.error("S3 upload FAILED for s3://%s/%s: %s", S3_BUCKET, key, e)
        return False


class HABRawCombiner:
    def __init__(self):
        pass

    # ---------- LOADERS ----------
    def load_month_file(self, filename: str, lineage: str) -> pd.DataFrame:
        logging.info("Loading %s -> %s (s3://%s/%s)", lineage, filename, S3_BUCKET, _s3_key(filename))
        df = read_csv_from_s3(filename)

        if df.empty:
            return df

        # THE ONLY COLUMN WE ADD
        df["data_lineage"] = lineage

        logging.info("Loaded %d rows", len(df))
        return df

    # ---------- BUILD HISTORICAL ----------
    def build_historical(self) -> pd.DataFrame:
        monthly_files = [
            "credit_bureau_data_january_.csv",
            "credit_bureau_data_february_.csv",
            "credit_bureau_data_march_.csv",
            "credit_bureau_data_april_.csv",
            "credit_bureau_data_may_.csv",
            "credit_bureau_data_june_.csv",
        ]

        parts = []
        for file in monthly_files:
            df = self.load_month_file(file, "HISTORICAL")
            if not df.empty:
                parts.append(df)

        if not parts:
            return pd.DataFrame()

        historical_df = pd.concat(parts, ignore_index=True)
        logging.info("Historical rows: %d", len(historical_df))
        return historical_df

    # ---------- COMBINE ----------
    def combine_all(self) -> pd.DataFrame:
        logging.info("Building Historical dataset...")
        h_df = self.build_historical()

        logging.info("Loading Previous month (July)...")
        a_df = self.load_month_file("credit_bureau_data_july_.csv", "PREVIOUS_MONTH")

        logging.info("Loading Current month (August)...")
        b_df = self.load_month_file("credit_bureau_data_august_.csv", "CURRENT_MONTH")

        parts = [df for df in [h_df, a_df, b_df] if not df.empty]

        if not parts:
            logging.error("No data found!")
            return pd.DataFrame()

        final_df = pd.concat(parts, ignore_index=True)
        logging.info("FINAL ROW COUNT: %d", len(final_df))

        return final_df

    # ---------- SAVE ----------
    def save_csv(self, df: pd.DataFrame):
        """Upload the combined CSV directly to S3 — no local disk write."""
        filename = "hab_combined.csv"
        logging.info("Uploading combined CSV -> s3://%s/%s", S3_BUCKET, _s3_key(filename))
        upload_df_to_s3(df, filename)
        logging.info("Uploaded %d rows", len(df))
        return f"s3://{S3_BUCKET}/{_s3_key(filename)}"


# ---------- MAIN ----------
def main(args):
    logging.info("=== HAB SIMPLE COMBINER START ===")

    combiner = HABRawCombiner()

    final_df = combiner.combine_all()

    if final_df.empty:
        logging.error("No data to save.")
        return

    # 1) Upload combined CSV to S3 (no local disk write)
    output_location = combiner.save_csv(final_df)
    logging.info("Combined CSV available at: %s", output_location)

    logging.info("=== FULL PIPELINE COMPLETE ===")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Combine HAB data into a single CSV, stored only in S3")
    # Accepted for backward compatibility with orchestrators/dashboards that
    # still invoke this script with the old local-disk flags (e.g.
    # "2HAB.py --data-dir . --output-dir ."). Storage is S3-only now, so
    # these values are accepted but intentionally unused — pass S3_BUCKET /
    # S3_PREFIX env vars instead if you need to point at a different
    # bucket/folder.
    parser.add_argument("--data-dir", dest="data_dir", default=None,
                         help="Ignored — storage is S3-only. Kept for CLI compatibility.")
    parser.add_argument("--output-dir", dest="output_dir", default=None,
                         help="Ignored — storage is S3-only. Kept for CLI compatibility.")
    args = parser.parse_args()

    if args.data_dir or args.output_dir:
        logging.warning(
            "--data-dir/--output-dir were passed but are ignored (S3-only storage). "
            "Reading/writing s3://%s/%s/ — set S3_BUCKET/S3_PREFIX env vars to change this.",
            S3_BUCKET, S3_PREFIX
        )

    main(args)