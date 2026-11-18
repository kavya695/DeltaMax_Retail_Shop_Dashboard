"""
golden_record_policy.py — Retail Shop MDM
==========================================
Selects the single best CustomerRecord from a cluster (all records
for the same customer_id) to become the golden master record.

Survivorship rules (applied in order):
  1. Pick the record with the highest trust_score.
  2. Tie-break: prefer the record with the most total_transactions.
  3. Tie-break: prefer the record with the most recent last_purchase_date.
"""
from __future__ import annotations
from datetime import datetime
from models import CustomerRecord


def _parse_date(val: str | None) -> datetime:
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d")
    except Exception:
        return datetime.min


class GoldenRecordPolicyEngine:

    def build_golden(self, cluster: list[CustomerRecord]) -> CustomerRecord:
        """Return the best record from the cluster."""
        if not cluster:
            raise ValueError("Empty cluster passed to build_golden")

        best = max(
            cluster,
            key=lambda r: (
                r.trust_score,
                r.total_transactions,
                _parse_date(r.last_purchase_date),
            )
        )
        # Enrich the golden record with cluster-wide aggregates
        best.total_transactions   = sum(r.total_transactions   for r in cluster)
        best.total_spend          = round(sum(r.total_spend     for r in cluster), 2)
        best.total_loyalty_points = round(sum(r.total_loyalty_points for r in cluster), 1)
        return best
