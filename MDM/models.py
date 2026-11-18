"""
models.py — Retail Shop MDM data model
Customer master record built from retail transaction data.
Each customer may appear across multiple stores (source systems).
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CustomerRecord:
    record_id:            str            # unique row ID
    source_system:        str            # store that produced this record (SHOP_001 … SHOP_006)
    customer_id:          str            # natural key — same customer across stores
    store_id:             str
    store_city:           str
    first_purchase_date:  Optional[str]
    last_purchase_date:   Optional[str]
    total_transactions:   int
    total_spend:          float
    avg_order_value:      float
    total_loyalty_points: float
    preferred_category:   Optional[str]
    preferred_payment:    Optional[str]
    is_online_buyer:      Optional[bool]
    return_rate:          float          # fraction of purchases returned
    data_month:           Optional[str]  # most recent data_month seen
    data_lineage:         Optional[str]
    cluster_id:           str            # == customer_id (one cluster per customer)
    trust_score:          float = 0.0
