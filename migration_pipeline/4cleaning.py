import sys
import io
import pandas as pd
import os

# ============================================================
# WINDOWS CONSOLE ENCODING FIX
# ============================================================
# On Windows, when this script's stdout is piped through a subprocess
# (as the dashboard does to stream logs), Python defaults to the 'cp1252'
# codec instead of UTF-8. cp1252 can't encode characters like ✓ or ✗, so
# any print() using them crashes with UnicodeEncodeError even after the
# actual work (e.g. the S3 upload) already succeeded. Forcing UTF-8 here
# fixes that for every print() below, without having to strip the symbols.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# AWS S3 CONFIG + HELPERS (no local disk reads/writes)
# ============================================================
# Auth uses the standard AWS credential chain (env vars, ~/.aws/credentials,
# or an attached IAM role) — no keys are read or stored here. Set:
#     AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION
# (omit the key/secret entirely if the host has an IAM role attached).
S3_BUCKET = os.environ.get("S3_BUCKET", "deltamax-dashboard").strip()
S3_PREFIX = os.environ.get("S3_PREFIX", "migration-pipeline").strip().strip("/")


def _s3_key(filename: str) -> str:
    return f"{S3_PREFIX}/{filename}" if S3_PREFIX else filename


def read_csv_from_s3(filename: str) -> pd.DataFrame:
    """Read a CSV object from s3://S3_BUCKET/S3_PREFIX/filename into a
    DataFrame. Returns an empty DataFrame if the object doesn't exist."""
    key = _s3_key(filename)
    try:
        import boto3
        client = boto3.client("s3")
        obj = client.get_object(Bucket=S3_BUCKET, Key=key)
        return pd.read_csv(io.BytesIO(obj["Body"].read()), low_memory=False)
    except Exception as e:
        print(f"[s3] read failed for s3://{S3_BUCKET}/{key}: {e}")
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
        print(f"[OK] Uploaded s3://{S3_BUCKET}/{key} ({len(df)} rows)")
        return True
    except Exception as e:
        print(f"[s3] upload FAILED for {key}: {e}")
        return False


# ============================================================
# CLEAN DATA: REMOVE ANOMALOUS BUSINESSES FROM CURRENT MONTH
# ============================================================

# All inputs/outputs live in S3, under s3://S3_BUCKET/S3_PREFIX/...
INPUT_FILE = "hab_combined.csv"
TRUST_FILE = "hab_with_trust_pre_migration.csv"

print("Loading HAB data from S3...")
df = read_csv_from_s3(INPUT_FILE)
if df.empty:
    print(f"✗ {INPUT_FILE} not found in s3://{S3_BUCKET}/{S3_PREFIX}/.")
    print(f"   Please run 2HAB.py or 3pre-migration_check.py first to generate {INPUT_FILE}")
    sys.exit(1)
print(f"[OK] Loaded {INPUT_FILE} ({len(df)} rows)")

df["record_id"] = df.index

print("Loading trust scores from S3...")
trust_df = read_csv_from_s3(TRUST_FILE)
if trust_df.empty:
    print(f"✗ {TRUST_FILE} not found in s3://{S3_BUCKET}/{S3_PREFIX}/.")
    sys.exit(1)

print("Merging trust scores into HAB data...")
df = df.merge(trust_df, on=["record_id", "businessID", "data_lineage"], how="left")

print("Cleaning HAB data by removing anomalous businesses...")

# Risk threshold (same as in pre-migration_check.py)
RISK_THRESHOLD = 0.30


# Define main data columns (original HAB columns)
main_columns = [
    'businessID', 'segment', 'businessName', 'industry', 'legalStructure', 'yearEstablished',
    'registeredAddress_Street', 'registeredAddress_City', 'registeredAddress_State', 'registeredAddress_ZipCode',
    'contactPerson_Name', 'contactPerson_Title', 'contactPerson_Email', 'phoneNumber', 'website',
    'employeeCount', 'annualRevenue', 'creditScore', 'outstandingDebt', 'totalCreditLimit', 'availableCredit',
    'paymentHistoryScore', 'creditUtilizationRatio', 'averageAccountAge', 'numberOfAccounts', 'numberOfInquiries',
    'hasDelinquency', 'hasDefaulted', 'hasBankruptcy', 'delinquentAccountCount', 'sixtyDayDelinquent',
    'ninetyDayDelinquent', 'oneHundredTwentyDayDelinquent', 'accountStatus_Active', 'accountStatus_Closed',
    'accountStatus_Dormant', 'collectionsCount', 'chargeoffCount', 'judgmentCount', 'taxLienCount',
    'publicRecordCount', 'monthsSinceLastPayment', 'monthsSinceFirstAccount', 'monthsSinceLastDelinquency',
    'lastPaymentAmount', 'highestCreditAmountEver', 'totalAccountBalance', 'totalMonthlyPayment',
    'debtToIncomeRatio', 'creditWorthinessScore', 'riskProfile', 'data_lineage', 'auditScore', 'riskRating',
    'lastAuditDate', 'complianceFlag', 'regulatoryStatus', 'expectedDefaultRate', 'systemHealthScore'
]
# The hab_with_trust_pre_migration.csv already has the unified anomaly_health column
# No need to create it from lineage-specific columns

# Clean data: H + A + B using anomaly health thresholds

clean_H = df[
    (df["data_lineage"] == "HISTORICAL") &
    (df["anomaly_health"] >= (1 - RISK_THRESHOLD))
].copy()

clean_A = df[
    (df["data_lineage"] == "PREVIOUS_MONTH") &
    (df["anomaly_health"] >= (1 - RISK_THRESHOLD))
].copy()

clean_B = df[
    (df["data_lineage"] == "CURRENT_MONTH") &
    (df["anomaly_health"] >= (1 - RISK_THRESHOLD))
].copy()

# Combine cleaned datasets
clean_df = pd.concat([clean_H, clean_A, clean_B], ignore_index=True)

# Select only main columns that exist in the dataframe
available_cols = [c for c in main_columns if c in clean_df.columns]
clean_df = clean_df[available_cols]


# ============================================================
# Discarded records (anomalies)
# ============================================================

discarded_H = df[
    (df["data_lineage"] == "HISTORICAL") &
    (df["anomaly_health"] < (1 - RISK_THRESHOLD))
].copy()

discarded_A = df[
    (df["data_lineage"] == "PREVIOUS_MONTH") &
    (df["anomaly_health"] < (1 - RISK_THRESHOLD))
].copy()

discarded_B = df[
    (df["data_lineage"] == "CURRENT_MONTH") &
    (df["anomaly_health"] < (1 - RISK_THRESHOLD))
].copy()

discarded_df = pd.concat([discarded_H, discarded_A, discarded_B], ignore_index=True)

# Select only main columns that exist in the dataframe
discarded_df = discarded_df[available_cols]


# Print counts
print("\nData lineage counts in cleaned data:")
print(clean_df["data_lineage"].value_counts())

print("\nData lineage counts in discarded data:")
print(discarded_df["data_lineage"].value_counts())

# Upload clean data to S3 (no local disk write)
upload_df_to_s3(clean_df, "hab_clean.csv")
print(f"✓ Clean data uploaded to S3 ({len(clean_df)} records)")

# Upload discarded data to S3 (no local disk write)
upload_df_to_s3(discarded_df, "hab_discarded.csv")
print(f"✓ Discarded data uploaded to S3 ({len(discarded_df)} records)")

print("="*80)
print("✓ Cleaning process complete! Files saved to S3 only.")
print("=" * 80)