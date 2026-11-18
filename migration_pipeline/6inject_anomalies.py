import sys
import pandas as pd
import os
import json
from datetime import datetime
from pathlib import Path
import boto3
from io import StringIO

# ============================================================
# WINDOWS CONSOLE ENCODING FIX
# ============================================================
# On Windows, when this script's stdout is piped through a subprocess
# (as the dashboard does to stream logs), Python defaults to the 'cp1252'
# codec instead of UTF-8. cp1252 can't encode emoji like 🗑️ or ⚖️, so any
# print() using them crashes with UnicodeEncodeError even after the actual
# work already succeeded. Forcing UTF-8 here fixes that for every print()
# below, without having to strip the symbols.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# 🎛️ ANOMALY CONFIGURATION
# ============================================================

ENABLE_ANOMALIES = True  # ⚠️ SET TO FALSE TO DISABLE ANOMALY INJECTION

# Input/Output files
INPUT_HAB_FILE = "results/hab_clean.csv"  # Load from results/hab_clean.csv
OUTPUT_HAB_FILE = "results/hab_clean_with_anomalies.csv"  # Save as new file

# ============================================================
# AWS S3 Storage Configuration
# ============================================================

S3_BUCKET = "deltamax-dashboard"
S3_FOLDER = "migration-pipeline"  # Destination folder inside the bucket

# ============================================================
# 🎛️ ANOMALY CONFIGURATION
# ============================================================

ANOMALY_CONFIG = {
    # 1. DROP ENTIRE FILES (from results/ subdirectories)
    "drop_files": {
        "enabled": False,
        "files": [
        ]
    },

    # 2. DROP RECORDS (by percentage or count)
    "drop_records": {
        "enabled": True,
        "strategy": "percentage",  # "percentage" or "count"
        "value": 5 ,                # Drop 5% of records OR drop this many records
        "filter_condition": None,  # Optional: "df['creditScore'] < 350" to drop specific rows
        "files": [
            # Use combined HAB file
            INPUT_HAB_FILE,
        ]
    },

    # 3. INTRODUCE DUPLICATES (intentional duplicates)
    "introduce_duplicates": {
        "enabled": True,
        "percentage": 10,           # Duplicate 10% of records
        "files": [
            INPUT_HAB_FILE,
        ]
    },

    # 4. CORRUPT DATA (introduce NaN values)
    "corrupt_values": {
        "enabled": True,
        "percentage": 20,           # Corrupt 20% of records
        "columns": [
            "creditScore",
            "annualRevenue",
            "outstandingDebt",
        ],
        "files": [
            INPUT_HAB_FILE,
        ]
    },

    # 5. CREATE OUTLIERS (extreme values)
    "create_outliers": {
        "enabled": True,
        "count": 5000,               # Number of outliers to create
        "outlier_columns": [
            {"column": "creditScore", "value": 9999},
            {"column": "annualRevenue", "value": 1e15},
        ],
        "files": [
            INPUT_HAB_FILE,
        ]
    },

    # 6. INTRODUCE BUSINESS RULE VIOLATIONS
    "business_rule_violations": {
        "enabled": True,
        "count": 500,               # Number of records to violate
        "violations": [
            {"column": "creditScore", "invalid_value": -100},
            {"column": "creditScore", "invalid_value": 2000},
            {"column": "annualRevenue", "invalid_value": -50000},
        ],
        "files": [
            INPUT_HAB_FILE,
        ]
    },
}

# ============================================================
# ☁️ AWS S3 UPLOAD HELPERS
# ============================================================

def download_csv_from_s3(filename, bucket=S3_BUCKET, folder=S3_FOLDER):
    """Read a CSV object from s3://bucket/folder/<basename(filename)> into a
    DataFrame. This is the missing counterpart to the upload_* helpers below —
    without it, the pipeline has no way to load its S3-hosted input at all."""
    s3 = boto3.client("s3")
    key = f"{folder}/{os.path.basename(filename)}" if folder else os.path.basename(filename)
    obj = s3.get_object(Bucket=bucket, Key=key)
    return pd.read_csv(obj["Body"], low_memory=False)


def s3_object_exists(filename, bucket=S3_BUCKET, folder=S3_FOLDER):
    from botocore.exceptions import ClientError
    s3 = boto3.client("s3")
    key = f"{folder}/{os.path.basename(filename)}" if folder else os.path.basename(filename)
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def delete_s3_object(filename, bucket=S3_BUCKET, folder=S3_FOLDER):
    s3 = boto3.client("s3")
    key = f"{folder}/{os.path.basename(filename)}" if folder else os.path.basename(filename)
    try:
        s3.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def upload_file_to_s3(local_path, bucket=S3_BUCKET, folder=S3_FOLDER):
    """Upload a local file to s3://bucket/folder/<filename> using boto3."""
    s3 = boto3.client("s3")
    filename = os.path.basename(local_path)
    s3_key = f"{folder}/{filename}"
    s3.upload_file(local_path, bucket, s3_key)
    print(f"  ✓ Uploaded {local_path} -> s3://{bucket}/{s3_key}")
    return s3_key


def upload_dataframe_to_s3(df, filename, bucket=S3_BUCKET, folder=S3_FOLDER):
    """Upload a DataFrame directly to S3 as CSV without ever touching local disk."""
    s3 = boto3.client("s3")
    buffer = StringIO()
    df.to_csv(buffer, index=False)
    s3_key = f"{folder}/{filename}"
    s3.put_object(Bucket=bucket, Key=s3_key, Body=buffer.getvalue())
    print(f"  ✓ Uploaded DataFrame -> s3://{bucket}/{s3_key}")
    return s3_key


def upload_json_to_s3(data, filename, bucket=S3_BUCKET, folder=S3_FOLDER):
    """Upload a JSON-serializable object directly to S3 without touching local disk."""
    s3 = boto3.client("s3")
    s3_key = f"{folder}/{filename}"
    s3.put_object(Bucket=bucket, Key=s3_key, Body=json.dumps(data, indent=2))
    print(f"  ✓ Uploaded JSON -> s3://{bucket}/{s3_key}")
    return s3_key


# ============================================================
# 📋 FUNCTIONS
# ============================================================

class AnomalyInjector:
    def __init__(self, config, enable=True, save_mode="modify"):
        self.config = config
        self.enable = enable
        self.save_mode = save_mode
        self.log = {
            "timestamp": datetime.now().isoformat(),
            "enabled": enable,
            "anomalies": {}
        }
        self.results_dir = "./results"
        self.working_file = INPUT_HAB_FILE  # Always track the current file being processed
        self.final_df = None  # Holds the final anomalous DataFrame in memory for S3 upload

    def log_anomaly(self, category, details):
        """Log anomaly for tracking"""
        if category not in self.log["anomalies"]:
            self.log["anomalies"][category] = []
        self.log["anomalies"][category].append(details)

    def get_output_path(self, input_path):
        """Get output path based on save mode"""
        # Special handling for hab_clean.csv
        if "hab_clean.csv" in input_path and "with_anomalies" not in input_path:
            return OUTPUT_HAB_FILE

        if self.save_mode == "new":
            # Insert _with_anomalies before .csv extension
            base = input_path.replace(".csv", "")
            return base + "_with_anomalies.csv"
        else:
            # Overwrite original
            return input_path

    def inject_anomalies(self):
        """Main orchestrator function"""
        if not self.enable:
            print("  Anomaly injection DISABLED")
            return

        print("\n" + "="*80)
        print("[INJECT] ANOMALY INJECTION STARTED")
        print("="*80)

        # Execute enabled anomalies in order
        if self.config["drop_files"]["enabled"]:
            self.drop_files()

        if self.config["drop_records"]["enabled"]:
            self.drop_records()

        if self.config["introduce_duplicates"]["enabled"]:
            self.introduce_duplicates()

        if self.config["corrupt_values"]["enabled"]:
            self.corrupt_values()

        if self.config["create_outliers"]["enabled"]:
            self.create_outliers()

        if self.config["business_rule_violations"]["enabled"]:
            self.business_rule_violations()

        print("\n" + "="*80)
        print(" ANOMALY INJECTION COMPLETE")
        print("="*80 + "\n")

    def drop_files(self):
        """Drop entire S3 objects (this section is disabled by default in
        ANOMALY_CONFIG; fixed to target S3 instead of local disk in case
        it's ever enabled)."""
        print("\n Dropping Files...")
        cfg = self.config["drop_files"]

        for file_path in cfg["files"]:
            if s3_object_exists(file_path):
                if delete_s3_object(file_path):
                    print(f"  [OK] Dropped: {file_path}")
                    self.log_anomaly("drop_files", {"file": file_path, "status": "SUCCESS"})
                else:
                    print(f"  ✗ Failed to drop {file_path}")
                    self.log_anomaly("drop_files", {"file": file_path, "status": "FAILED"})
            else:
                print(f"  ⚠️  File not found: {file_path}")
                self.log_anomaly("drop_files", {"file": file_path, "status": "NOT_FOUND"})

    def drop_records(self):
        """Drop records from CSV files"""
        print("\n Dropping Records...")
        cfg = self.config["drop_records"]

        if not s3_object_exists(self.working_file):
            print(f"  ⚠️  File not found in S3: s3://{S3_BUCKET}/{S3_FOLDER}/{os.path.basename(self.working_file)}")
            return

        try:
            df = download_csv_from_s3(self.working_file)
            original_count = len(df)

            # Calculate number of records to drop
            if cfg["strategy"] == "percentage":
                drop_count = max(1, int(original_count * cfg["value"] / 100))
            else:
                drop_count = cfg["value"]

            # Drop records
            if cfg["filter_condition"]:
                # Drop specific filtered records
                mask = eval(cfg["filter_condition"])
                drop_indices = df[mask].sample(min(drop_count, mask.sum())).index
            else:
                # Random drop
                drop_indices = df.sample(min(drop_count, original_count), random_state=42).index

            df_modified = df.drop(drop_indices).reset_index(drop=True)

            self.final_df = df_modified
            self.working_file = OUTPUT_HAB_FILE  # Logical name only, no local write

            print(f"  ✓ Dropped {len(drop_indices)} records ({original_count} → {len(df_modified)})")
            self.log_anomaly("drop_records", {
                "file": self.working_file,
                "original_count": original_count,
                "dropped": len(drop_indices),
                "remaining": len(df_modified),
                "percentage": cfg["value"]
            })
        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            self.log_anomaly("drop_records", {"file": self.working_file, "status": "FAILED", "error": str(e)})

    def introduce_duplicates(self):
        """Introduce duplicate records"""
        print("\n Introducing Duplicates...")
        cfg = self.config["introduce_duplicates"]

        try:
            df = self.final_df if self.final_df is not None else download_csv_from_s3(self.working_file)
            original_count = len(df)

            # Calculate duplicates
            dup_count = max(1, int(original_count * cfg["percentage"] / 100))

            # Select random records to duplicate
            dup_indices = df.sample(min(dup_count, original_count), random_state=42).index
            dup_records = df.loc[dup_indices].copy()

            # Append duplicates
            df_modified = pd.concat([df, dup_records], ignore_index=True)
            self.final_df = df_modified
            self.working_file = OUTPUT_HAB_FILE

            print(f"  [OK] Added {len(dup_records)} duplicates ({original_count} -> {len(df_modified)})")
            self.log_anomaly("introduce_duplicates", {
                "file": self.working_file,
                "original_count": original_count,
                "duplicates_added": len(dup_records),
                "final_count": len(df_modified)
            })
        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            self.log_anomaly("introduce_duplicates", {"file": self.working_file, "status": "FAILED", "error": str(e)})

    def corrupt_values(self):
        """Introduce NaN/missing values"""
        print("\n Corrupting Values (NaN)...")
        cfg = self.config["corrupt_values"]

        try:
            df = self.final_df if self.final_df is not None else download_csv_from_s3(self.working_file)
            original_nans = df.isna().sum().sum()

            for column in cfg["columns"]:
                if column in df.columns:
                    # Get indices to corrupt
                    corrupt_count = max(1, int(len(df) * cfg["percentage"] / 100))
                    corrupt_indices = df.sample(min(corrupt_count, len(df)), random_state=42).index
                    df.loc[corrupt_indices, column] = pd.NA

            self.final_df = df
            self.working_file = OUTPUT_HAB_FILE
            new_nans = df.isna().sum().sum()

            print(f"  [OK] Corrupted {new_nans - original_nans} values")
            self.log_anomaly("corrupt_values", {
                "file": self.working_file,
                "nans_introduced": new_nans - original_nans,
                "total_nans": new_nans
            })
        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            self.log_anomaly("corrupt_values", {"file": self.working_file, "status": "FAILED", "error": str(e)})

    def create_outliers(self):
        """Create extreme outlier values"""
        print("\n Creating Outliers...")
        cfg = self.config["create_outliers"]

        try:
            df = self.final_df if self.final_df is not None else download_csv_from_s3(self.working_file)

            outliers_created = 0
            for outlier in cfg["outlier_columns"]:
                column = outlier["column"]
                value = outlier["value"]

                if column in df.columns:
                    # Replace first N records with outlier value
                    count = min(cfg["count"], len(df))
                    df.loc[:count-1, column] = value
                    outliers_created += count

            self.final_df = df
            self.working_file = OUTPUT_HAB_FILE

            print(f"  [OK] Created {outliers_created} outliers")
            self.log_anomaly("create_outliers", {
                "file": self.working_file,
                "outliers_created": outliers_created
            })
        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            self.log_anomaly("create_outliers", {"file": self.working_file, "status": "FAILED", "error": str(e)})

    def business_rule_violations(self):
        """Introduce business rule violations"""
        print("\n  Creating Business Rule Violations...")
        cfg = self.config["business_rule_violations"]

        try:
            df = self.final_df if self.final_df is not None else download_csv_from_s3(self.working_file)

            violations_created = 0
            for violation in cfg["violations"]:
                column = violation["column"]
                invalid_value = violation["invalid_value"]

                if column in df.columns:
                    # Apply violation to random records
                    count = min(cfg["count"], len(df))
                    violation_indices = df.sample(count, random_state=42).index
                    df.loc[violation_indices, column] = invalid_value
                    violations_created += count

            self.final_df = df
            self.working_file = OUTPUT_HAB_FILE

            print(f"  [OK] Created {violations_created} violations")
            self.log_anomaly("business_rule_violations", {
                "file": self.working_file,
                "violations_created": violations_created
            })
        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            self.log_anomaly("business_rule_violations", {"file": self.working_file, "status": "FAILED", "error": str(e)})

    def convert_to_serializable(self, obj):
        """Convert numpy/pandas types to native Python types for JSON serialization"""
        import numpy as np
        if isinstance(obj, dict):
            return {key: self.convert_to_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self.convert_to_serializable(item) for item in obj]
        elif isinstance(obj, (np.integer, np.floating)):
            return obj.item()
        return obj

    def upload_outputs_to_s3(self):
        """Upload the final anomalous dataset and the anomaly log straight to S3 —
        nothing is written to local disk at any point."""
        if self.final_df is not None:
            upload_dataframe_to_s3(self.final_df, os.path.basename(OUTPUT_HAB_FILE))
            print(f"  Anomalous data uploaded to s3://{S3_BUCKET}/{S3_FOLDER}/{os.path.basename(OUTPUT_HAB_FILE)}")

        serializable_log = self.convert_to_serializable(self.log)
        upload_json_to_s3(serializable_log, "ANOMALY_LOG.json")
        print(f"  Anomaly log uploaded to s3://{S3_BUCKET}/{S3_FOLDER}/ANOMALY_LOG.json")

    def print_detailed_summary(self):
        """Print detailed summary of all injected anomalies with percentages"""
        if not self.enable or not self.log["anomalies"]:
            print("\n No anomalies were injected")
            return

        print("\n" + "="*80)
        print(" DETAILED ANOMALY INJECTION REPORT")
        print("="*80)

        anomalies = self.log["anomalies"]

        # 1. DROP RECORDS
        if "drop_records" in anomalies and anomalies["drop_records"]:
            print("\n  DROP RECORDS:")
            for detail in anomalies["drop_records"]:
                if "percentage" in detail:
                    original = detail["original_count"]
                    dropped = detail["dropped"]
                    remaining = detail["remaining"]
                    pct = detail["percentage"]
                    print(f"    File: {detail['file']}")
                    print(f"      • Original records: {original}")
                    print(f"      • Dropped records: {dropped} ({pct}%)")
                    print(f"      • Remaining records: {remaining}")

        # 2. INTRODUCE DUPLICATES
        if "introduce_duplicates" in anomalies and anomalies["introduce_duplicates"]:
            print("\n INTRODUCE DUPLICATES:")
            for detail in anomalies["introduce_duplicates"]:
                original = detail["original_count"]
                duplicates = detail["duplicates_added"]
                final = detail["final_count"]
                pct = (duplicates / original * 100) if original > 0 else 0
                print(f"    File: {detail['file']}")
                print(f"      • Original records: {original}")
                print(f"      • Duplicates added: {duplicates} ({pct:.2f}%)")
                print(f"      • Final record count: {final}")

        # 3. CORRUPT VALUES (NaN)
        if "corrupt_values" in anomalies and anomalies["corrupt_values"]:
            print("\n CORRUPT VALUES (NaN INJECTION):")
            for detail in anomalies["corrupt_values"]:
                nans_added = detail["nans_introduced"]
                total_nans = detail["total_nans"]
                print(f"    File: {detail['file']}")
                print(f"      • NaN values introduced: {nans_added}")
                print(f"      • Total NaN values: {total_nans}")

        # 4. CREATE OUTLIERS
        if "create_outliers" in anomalies and anomalies["create_outliers"]:
            print("\n CREATE OUTLIERS:")
            for detail in anomalies["create_outliers"]:
                outliers = detail["outliers_created"]
                print(f"    File: {detail['file']}")
                print(f"      • Outliers created: {outliers}")

        # 5. BUSINESS RULE VIOLATIONS
        if "business_rule_violations" in anomalies and anomalies["business_rule_violations"]:
            print("\n  BUSINESS RULE VIOLATIONS:")
            for detail in anomalies["business_rule_violations"]:
                violations = detail["violations_created"]
                print(f"    File: {detail['file']}")
                print(f"      • Violations created: {violations}")

        # 6. DROP FILES
        if "drop_files" in anomalies and anomalies["drop_files"]:
            print("\n DROPPED FILES:")
            for detail in anomalies["drop_files"]:
                status = detail.get("status", "UNKNOWN")
                print(f"    • {detail['file']}: {status}")

        # Summary statistics
        print("\n" + "-"*80)
        print(" ANOMALY INJECTION SUMMARY:")
        total_operations = sum(len(ops) for ops in anomalies.values())
        print(f"  • Total anomaly operations: {total_operations}")
        print(f"  • Anomaly categories affected: {len(anomalies)}")
        for category, ops in anomalies.items():
            print(f"    - {category}: {len(ops)} operation(s)")

        print("\n" + "="*80 + "\n")

# ============================================================
# 🚀 MAIN EXECUTION
# ============================================================
# 💾 SAVE MODE CONFIGURATION
# ============================================================
# "modify" = Overwrite original files (in-place modification)
# "new" = Save anomalous data to new files with "_with_anomalies" suffix
SAVE_MODE = "new"  # Keep original + create new anomalous versions

# ============================================================
# 🚀 MAIN EXECUTION
# ============================================================

if __name__ == "__main__":
    injector = AnomalyInjector(ANOMALY_CONFIG, enable=ENABLE_ANOMALIES, save_mode=SAVE_MODE)
    injector.inject_anomalies()

    if ENABLE_ANOMALIES and injector.log["anomalies"]:
        # Print detailed summary
        injector.print_detailed_summary()

        if SAVE_MODE == "new":
            print(f"[OK] Original files preserved | Anomalous data uploaded to s3://{S3_BUCKET}/{S3_FOLDER}/")

    # Upload outputs directly to S3 (no local files written)
    injector.upload_outputs_to_s3()