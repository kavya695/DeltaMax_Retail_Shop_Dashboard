"""
Migration script - DEPRECATED

This script is no longer used. The old GCP cloud integration path has been
replaced end-to-end by an AWS-based pipeline.

Previous functionality:
- Upload results to GCP Cloud Storage        ✗ REMOVED
- Load results to BigQuery                    ✗ REMOVED
- GCP-based pipeline storage                  ✗ REMOVED

Current workflow (AWS):
- All pipeline outputs are written directly to S3 —
  s3://deltamax-dashboard/migration-pipeline/ — with no local disk storage.
- The scripts in this pipeline (credit_bureau_data_generator.py,
  hab_raw_combiner.py, 3pre-migration_check.py, 4cleaning.py,
  7run_scoring_cleaned.py / 8post-migration_check.py) each read their
  input from and write their output to that S3 location.
- See backend/config.py for data access configuration.
"""

if __name__ == "__main__":
    print("This script is deprecated and has been disabled.")
    print("The GCP-based pipeline storage has been removed.")
    print("All pipeline data now lives in S3 (s3://deltamax-dashboard/migration-pipeline/).")