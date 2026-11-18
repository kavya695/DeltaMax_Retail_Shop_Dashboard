#!/usr/bin/env python3
"""
DeltaMax - Retail Shop Data Generator (V5 style)
=================================================
Generates Jan-Dec of synthetic retail transactions the same way the V5 credit
bureau generator does:

  * PERSISTENT entities  - shops, products and customers carry forward month to
                           month; new shops/products/customers appear; some
                           customers genuinely stop buying (churn).
  * EVENT-DRIVEN months  - each month has its own business event (price rise,
                           shop outage, heavy discounting, supplier shortage,
                           returns spike, unit-price error, festival surge ...).
  * RANDOM-INTENSITY data-quality problems - missing customer, duplicate
                           sales, zero quantity, inflated totals (rates vary).
  * TIME-AWARE columns   - every shop sends one file per month with an
                           expected arrival date, a real ingestion date and
                           days-late, so FRESHNESS can be measured.
  * SCHEMA EVOLUTION     - new columns appear in later months.

Outputs (in ./data):
  retail_monthly/retail_data_<month>_.csv    one file per month (like V5)
  retail_shop_data.csv                       all months combined + data_lineage
                                             (Jan-Oct HISTORICAL, Nov PREVIOUS, Dec CURRENT)
  retail_planted_issues.csv                  every row-level problem we planted
  retail_ground_truth.json                   events, churners, late shop-months

Run:   python generate_retail_data.py                 (same data every time, seed 42)
       python generate_retail_data.py --random        (different data every run)
       python generate_retail_data.py --seed 7 --year 2026
Needs: pandas, numpy
"""
import argparse
import json
import os
import calendar

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december"]

# (id, name, category, base_price, base_stock, popularity, launch_month)
PRODUCTS = [
    ("P1001", "Rice 5kg", "Grocery", 320.0, 80, 1.0, 1),
    ("P1002", "Milk 1L", "Dairy", 32.0, 120, 1.6, 1),
    ("P1003", "Bread", "Bakery", 40.0, 70, 1.3, 1),
    ("P1004", "Cooking Oil 1L", "Grocery", 145.0, 60, 0.9, 1),
    ("P1005", "Bath Soap", "Personal Care", 38.0, 100, 1.2, 1),
    ("P1006", "Shampoo 180ml", "Personal Care", 115.0, 45, 0.7, 1),
    ("P1007", "Biscuits", "Snacks", 20.0, 150, 1.8, 1),
    ("P1008", "Tea 250g", "Beverages", 95.0, 55, 0.8, 1),
    ("P1009", "Notebook", "Stationery", 55.0, 90, 0.9, 1),
    ("P1010", "Detergent 1kg", "Household", 110.0, 50, 0.8, 1),
    ("P1011", "Eggs 12 pack", "Dairy", 85.0, 65, 1.0, 1),
    ("P1012", "Toothpaste", "Personal Care", 72.0, 75, 1.0, 1),
    ("P1013", "Biscuits Family Pack", "Snacks", 45.0, 80, 0.9, 1),
    ("P1014", "Soft Drink 1L", "Beverages", 60.0, 90, 1.1, 1),
    ("P1015", "Floor Cleaner", "Household", 130.0, 35, 0.5, 1),
    ("P1016", "Peanut Butter 340g", "Grocery", 210.0, 40, 0.6, 7),   # new product in July
    ("P1017", "Green Tea 100g", "Beverages", 150.0, 40, 0.6, 9),     # new product in September
]
# (id, city, size_weight, open_month, data_due_day_of_next_month)
SHOPS = [
    ("SHOP_001", "Bengaluru", 1.3, 1, 3),
    ("SHOP_002", "Mysuru", 0.8, 1, 5),
    ("SHOP_003", "Hyderabad", 1.0, 1, 5),
    ("SHOP_004", "Chennai", 1.0, 1, 7),
    ("SHOP_005", "Pune", 0.9, 4, 7),      # new shop opens in April
    ("SHOP_006", "Kochi", 0.7, 7, 10),    # new shop opens in July
]
PAYMENT = ["Cash", "UPI", "Card", "Wallet"]
PAY_P = [0.25, 0.45, 0.22, 0.08]
SEASON = {10: 1.35, 11: 1.20, 12: 1.10}          # festive months
BASE_TX_PER_SHOP_UNIT = 150

# What happens each month (documentation + ground truth)
EVENTS = {
    1: "Baseline - clean month",
    2: "Baseline - clean month",
    3: "Grocery prices rise 10-16% at every shop (price drift)",
    4: "SHOP_003 outage - volume falls to ~30%; SHOP_005 opens",
    5: "SHOP_002 heavy discounting (10-25%); loyalty_points column appears",
    6: "Supplier shortage on Dairy - stock very low",
    7: "Returns spike on Personal Care; SHOP_006 opens; new product P1016",
    8: "SHOP_004 unit-price entry error (x100) on ~3% of sales; online_order_flag appears",
    9: "Recovery month; new product P1017",
    10: "Festival surge - volume +50%, higher discounts",
    11: "Festive tail (PREVIOUS month) - volume +25%",
    12: "CURRENT month: SHOP_004 sends data ~18 days late, Shampoo price entered x10 at SHOP_002, "
        "4x more missing customer IDs",
}


def month_last_day(year, month):
    return calendar.monthrange(year, month)[1]


class RetailWorld:
    """Persistent state carried from month to month (customers, suppliers)."""

    def __init__(self, rng):
        self.rng = rng
        self.supplier = {p[0]: f"SUP{int(rng.integers(1, 31)):03d}" for p in PRODUCTS}
        self.customers = pd.DataFrame(columns=["customer_id", "home_shop", "propensity", "created", "churn_month"])
        self.next_cust = 1

    def add_customers(self, month, n, open_shops):
        r = self.rng
        ids = [f"CUST{self.next_cust + i:06d}" for i in range(n)]
        self.next_cust += n
        churn = np.full(n, 99)
        if month <= 9:                                   # ~8% will stop buying at some point
            will = r.random(n) < 0.08
            churn[will] = r.integers(month + 2, 12, will.sum())
        new = pd.DataFrame({"customer_id": ids, "home_shop": r.choice(open_shops, n),
                            "propensity": r.gamma(2.0, 0.5, n) + 0.1, "created": month, "churn_month": churn})
        self.customers = new if self.customers.empty else pd.concat([self.customers, new], ignore_index=True)

    def active(self, month):
        c = self.customers
        return c[(c["created"] <= month) & (c["churn_month"] > month)]


def volume_multiplier(month, shop_id):
    m = SEASON.get(month, 1.0) * (1.02 ** month)
    if month == 4 and shop_id == "SHOP_003":
        m *= 0.3
    if month == 10:
        m *= 1.5 / 1.35
    if month == 11:
        m *= 1.25 / 1.20
    return m


def generate_month(world, year, month):
    """Returns (rows, issues). issues = list of (issue_name, uid) for row-level problems we plant."""
    r = world.rng
    issues = []
    open_shops = [s for s in SHOPS if s[3] <= month]
    world.add_customers(month, 1500 if month == 1 else int(r.integers(80, 140)), [s[0] for s in open_shops])
    cust = world.active(month)
    prods = [p for p in PRODUCTS if p[6] <= month]
    p_w = np.array([p[5] for p in prods], float)
    p_w /= p_w.sum()
    infl = 1.004 ** (month - 1)
    last_day = month_last_day(year, month)

    frames = []
    for shop_id, city, size, _, due_day in open_shops:
        n = int(round(BASE_TX_PER_SHOP_UNIT * size * volume_multiplier(month, shop_id) * r.uniform(0.95, 1.05)))
        pidx = r.choice(len(prods), n, p=p_w)
        w = cust["propensity"].values * np.where(cust["home_shop"].values == shop_id, 3.0, 0.3)
        base = np.array([prods[i][3] for i in pidx])
        days = r.integers(1, last_day + 1, n)
        frames.append(pd.DataFrame({
            "store_id": shop_id, "store_city": city,
            "customer_id": r.choice(cust["customer_id"].values, n, p=w / w.sum()),
            "product_id": [prods[i][0] for i in pidx],
            "product_name": [prods[i][1] for i in pidx],
            "category": [prods[i][2] for i in pidx],
            "quantity": r.choice(np.arange(1, 8), n, p=[.30, .25, .18, .12, .08, .05, .02]),
            "unit_price": np.round(base * infl * r.uniform(0.97, 1.05, n), 2),
            "discount_pct": r.choice([0, 0, 0, 5, 10, 15], n).astype(float),
            "sale_date": pd.to_datetime([f"{year}-{month:02d}-{int(d):02d}" for d in days]),
            "_base_stock": [prods[i][4] for i in pidx],
            "_due_day": due_day,
        }))
    m = pd.concat(frames, ignore_index=True)
    m["_uid"] = m.index                       # stable key, survives sorting

    # ---- price / discount events ----
    if month == 3:
        k = m["category"].eq("Grocery")
        m.loc[k, "unit_price"] = (m.loc[k, "unit_price"] * r.uniform(1.10, 1.16)).round(2)
    if month == 5:
        k = m["store_id"].eq("SHOP_002")
        m.loc[k, "discount_pct"] = r.choice([10, 15, 20, 25], int(k.sum())).astype(float)
    if month == 8:
        pool = m.index[m["store_id"].eq("SHOP_004")]
        hit = r.choice(pool, max(1, int(len(pool) * 0.03)), replace=False)
        m.loc[hit, "unit_price"] = (m.loc[hit, "unit_price"] * 100).round(2)
        issues += [("price_error_x100", i) for i in hit]
    if month == 10:
        m["discount_pct"] = r.choice([0, 5, 10, 15, 20], len(m)).astype(float)
    if month == 12:
        k = m.index[m["store_id"].eq("SHOP_002") & m["product_id"].eq("P1006")]
        m.loc[k, "unit_price"] = (m.loc[k, "unit_price"] * 10).round(2)
        issues += [("price_error_x10", i) for i in k]

    # ---- amounts (consistent BEFORE problems are injected) ----
    sub = m["quantity"] * m["unit_price"]
    m["discount_amount"] = (sub * m["discount_pct"] / 100).round(2)
    m["total_amount"] = (sub - m["discount_amount"]).round(2)

    # ---- stock, returns, supplier, payment ----
    m["stock_at_start"] = m["_base_stock"] + r.integers(-10, 25, len(m))
    if month == 6:
        k = m["category"].eq("Dairy")
        m.loc[k, "stock_at_start"] = r.integers(0, 5, int(k.sum()))
        short = m.index[k & (m["quantity"] > m["stock_at_start"])]
        issues += [("stock_shortage", i) for i in short]
    m["stock_sold"] = m["quantity"]
    m["stock_remaining"] = (m["stock_at_start"] - m["stock_sold"]).clip(lower=0)
    ret_p = np.where(m["category"].eq("Personal Care") & (month == 7), 0.12, 0.03)
    m["is_return"] = (r.random(len(m)) < ret_p).astype(int)
    m["supplier_id"] = m["product_id"].map(world.supplier)
    m["payment_method"] = r.choice(PAYMENT, len(m), p=PAY_P)

    # ---- random data-quality problems (intensity changes every run, like V5) ----
    q = 0.5 if month <= 2 else 1.0
    rate = float(r.uniform(0.004, 0.015)) * q * (4 if month == 12 else 1)
    k = r.choice(m.index, max(1, int(len(m) * rate)), replace=False)
    m.loc[k, "customer_id"] = None
    issues += [("missing_customer", i) for i in k]

    k = r.choice(m.index, max(1, int(len(m) * float(r.uniform(0.001, 0.004)) * q)), replace=False)
    m.loc[k, "quantity"] = 0                                    # total unchanged -> does not add up
    issues += [("zero_quantity", i) for i in k]

    k = r.choice(m.index, max(1, int(len(m) * float(r.uniform(0.001, 0.004)) * q)), replace=False)
    m.loc[k, "total_amount"] = (m.loc[k, "total_amount"] * r.uniform(5, 15, len(k))).round(2)
    issues += [("inflated_total", i) for i in k]
    return m, issues


def finish_month(world, m, issues, year, month):
    r = world.rng
    m = m.sort_values("sale_date", kind="stable").reset_index(drop=True)
    m["record_id"] = np.arange(1, len(m) + 1)
    m["transaction_id"] = [f"TX{year}{month:02d}{i:06d}" for i in m["record_id"]]
    m["batch_id"] = f"RETAIL_{year}_{month:02d}"
    m["data_month"] = f"{year}-{month:02d}"
    uid2tx = dict(zip(m["_uid"], m["transaction_id"]))
    planted = [(m["data_month"].iloc[0], uid2tx[u], name) for name, u in issues]

    # schema evolution: new columns appear in later months
    if month >= 5:
        m["loyalty_points"] = r.integers(0, 50, len(m))
    if month >= 8:
        m["online_order_flag"] = r.binomial(1, 0.15, len(m))

    # duplicate sales (same transaction twice)
    q = 0.5 if month <= 2 else 1.0
    n_dup = max(1, int(len(m) * float(r.uniform(0.001, 0.008)) * q))
    dup = m.iloc[r.choice(len(m), n_dup, replace=False)].copy()
    planted += [(m["data_month"].iloc[0], t, "duplicate") for t in dup["transaction_id"]]
    m = pd.concat([m, dup], ignore_index=True)

    # one feed per shop per month -> expected vs real arrival -> freshness
    nxt = pd.Timestamp(year, month, 1) + pd.offsets.MonthBegin(1)
    feed, late = [], []
    for shop_id, grp in m.groupby("store_id"):
        expected = nxt + pd.Timedelta(days=int(grp["_due_day"].iloc[0]) - 1)
        anomalous = bool(r.random() < 0.10)
        delay = int(r.integers(8, 21)) if anomalous else int(r.choice([0, 1, 2, 3], p=[0.4, 0.35, 0.15, 0.10]))
        if month == 12 and shop_id == "SHOP_004":
            anomalous, delay = True, 18
        if anomalous:
            late.append({"month": f"{year}-{month:02d}", "shop": shop_id, "days_late": delay})
        feed.append((shop_id, expected, delay, anomalous))
    t = pd.DataFrame(feed, columns=["store_id", "expected_arrival_date", "freshness_days_late", "is_anomalous_delay"])
    t["ingestion_date"] = t["expected_arrival_date"] + pd.to_timedelta(t["freshness_days_late"], unit="D")
    m = m.merge(t, on="store_id", how="left")
    m["loaded_at"] = m["ingestion_date"]

    cols = ["batch_id", "record_id", "transaction_id", "sale_date", "data_month", "store_id", "store_city",
            "customer_id", "product_id", "product_name", "category", "quantity", "unit_price", "discount_pct",
            "discount_amount", "total_amount", "payment_method", "stock_at_start", "stock_sold",
            "stock_remaining", "supplier_id", "is_return", "expected_arrival_date", "ingestion_date",
            "freshness_days_late", "is_anomalous_delay", "loaded_at"]
    cols += [c for c in ("loyalty_points", "online_order_flag") if c in m.columns]
    m = m[cols].copy()
    for c in ("sale_date", "expected_arrival_date", "ingestion_date", "loaded_at"):
        m[c] = pd.to_datetime(m[c]).dt.strftime("%Y-%m-%d")
    return m, planted, late


def generate_all(year, seed, data_dir):
    rng = np.random.default_rng(seed)
    world = RetailWorld(rng)
    mdir = os.path.join(data_dir, "retail_monthly")
    os.makedirs(mdir, exist_ok=True)
    frames, planted, late = [], [], []
    print(f"Generating retail data for {year} (seed={seed})\n")
    for month in range(1, 13):
        m, issues = generate_month(world, year, month)
        m, pl, lt = finish_month(world, m, issues, year, month)
        planted += pl
        late += lt
        m.to_csv(os.path.join(mdir, f"retail_data_{MONTH_NAMES[month - 1]}_.csv"), index=False)
        frames.append(m)
        print(f"[OK] {MONTH_NAMES[month - 1].title():<10} {len(m):>5} rows, {m['store_id'].nunique()} shops, "
              f"{m['product_id'].nunique():>2} products, {len(m.columns)} columns | {EVENTS[month][:70]}")

    all_df = pd.concat(frames, ignore_index=True)
    all_df["data_lineage"] = np.where(all_df["data_month"].str[-2:].astype(int) <= 10, "HISTORICAL",
                                      np.where(all_df["data_month"].str[-2:] == "11", "PREVIOUS_MONTH", "CURRENT_MONTH"))
    all_df.to_csv(os.path.join(data_dir, "retail_shop_data.csv"), index=False)
    pd.DataFrame(planted, columns=["data_month", "transaction_id", "issue"]).to_csv(
        os.path.join(data_dir, "retail_planted_issues.csv"), index=False)
    c = world.customers
    truth = {"seed": seed, "year": year, "events": {MONTH_NAMES[k - 1]: v for k, v in EVENTS.items()},
             "late_shop_months": late,
             "churned_customers_last_active_before_nov": c.loc[c["churn_month"] <= 11, "customer_id"].tolist()}
    with open(os.path.join(data_dir, "retail_ground_truth.json"), "w") as fh:
        json.dump(truth, fh, indent=2)

    print(f"\n[OK] Combined file: data/retail_shop_data.csv  ({len(all_df):,} rows, {len(all_df.columns)} columns)")
    print(f"[OK] Lineage: {all_df['data_lineage'].value_counts().to_dict()}")
    print(f"[OK] Planted row-level problems: {len(planted):,}  -> data/retail_planted_issues.csv")
    print(f"[OK] Late shop-months: {len(late)}  | planted churners: {len(truth['churned_customers_last_active_before_nov'])}")
    return all_df


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="DeltaMax retail data generator (V5 style)")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--random", action="store_true", help="use a different random seed every run")
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "data"))
    a = ap.parse_args()
    seed = int(np.random.default_rng().integers(1, 10**6)) if a.random else a.seed
    generate_all(a.year, seed, a.data_dir)
