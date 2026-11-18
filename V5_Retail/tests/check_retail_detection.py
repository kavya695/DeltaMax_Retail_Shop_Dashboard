"""Scores the engine against the problems the generator PLANTED.
   Run:  python generate_retail_data.py && python run.py --config configs/retail.yaml
         python tests/check_retail_detection.py
"""
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, "results", "retail")
rows = pd.read_csv(os.path.join(R, "row_scores.csv"), low_memory=False)
planted = pd.read_csv(os.path.join(ROOT, "data", "retail_planted_issues.csv"))
truth = json.load(open(os.path.join(ROOT, "data", "retail_ground_truth.json")))

rows["hard_flag"] = ((rows.rule_violations > 0) | (rows.duplicate_flag == 1) | (rows.missing_flag == 1)).astype(int)
rows["any_flag"] = ((rows.hard_flag == 1) | (rows.iqr_flag == 1) | (rows.isolation_forest_flag == 1) | (rows.knn_flag == 1)).astype(int)
key = rows.groupby(["data_month", "transaction_id"])[["hard_flag", "any_flag"]].max().reset_index()
planted_keys = planted.drop_duplicates(["data_month", "transaction_id"])[["data_month", "transaction_id"]]
m = planted.merge(key, on=["data_month", "transaction_id"], how="left")

print("=" * 72, "\n1) DID IT FIND THE PLANTED ROW-LEVEL PROBLEMS?\n" + "=" * 72)
t = m.groupby("issue").agg(planted=("issue", "size"), found_by_rules_dup_missing=("hard_flag", "sum"),
                           found_by_any_check=("any_flag", "sum")).reset_index()
t["recall_any_%"] = (100 * t.found_by_any_check / t.planted).round(1)
print(t.to_string(index=False))
print(f"\nOVERALL recall (any check): {100 * m.any_flag.mean():.1f}%   (rules/dup/missing only: {100 * m.hard_flag.mean():.1f}%)")

clean = key.merge(planted_keys.assign(p=1), on=["data_month", "transaction_id"], how="left")
clean = clean[clean.p.isna()]
print(f"\nFALSE ALARMS on {len(clean):,} untouched sales:  rules/dup/missing = {100 * clean.hard_flag.mean():.2f}%   "
      f"| any check incl. statistical = {100 * clean.any_flag.mean():.1f}%")

print("\n" + "=" * 72, "\n2) DID IT SEE THE MONTHLY EVENTS?  (trust by month)\n" + "=" * 72)
mo = pd.read_csv(os.path.join(R, "trust_by_month.csv"))
ev = truth["events"]
names = list(ev)
mo["event"] = [ev[names[int(x[-2:]) - 1]][:60] for x in mo.data_month]
cols = [c for c in ["data_month", "records", "anomaly_health", "drift_health", "business_health", "freshness_health", "overall_trust_score", "event"] if c in mo]
print(mo[cols].round(3).to_string(index=False))

# Trust averages alone do not tell us whether each known event is visible. These
# checks use the event's own measurable signal and compare it with its stated
# baseline. They audit the generated data/results; they do not turn ordinary
# seasonal business changes into data-quality errors.
print("\n" + "=" * 72, "\n3) EVENT-BY-EVENT CHECK (expected signal vs observed data)\n" + "=" * 72)
checks = []
def event_check(month, event, observed, expected, passed):
    checks.append(bool(passed))
    print(f"{'PASS' if passed else 'CHECK'}  {month}  {event}\n      Expected: {expected}\n      Observed: {observed}")

def month_rows(month):
    return rows[rows.data_month == month]

jan_feb = rows[rows.data_month.isin(["2026-01", "2026-02"]) & rows.category.eq("Grocery")]
mar_grocery = month_rows("2026-03").query("category == 'Grocery'")
price_lift = mar_grocery.unit_price.mean() / jan_feb.unit_price.mean() if len(jan_feb) and len(mar_grocery) else 0
event_check("2026-03", "Grocery price rise", f"Grocery average unit price {price_lift:.2f}x Jan-Feb",
            "at least 1.08x baseline", price_lift >= 1.08)

april_shop = month_rows("2026-04").query("store_id == 'SHOP_003'")
prior_shop_counts = (rows[rows.data_month.isin(["2026-01", "2026-02", "2026-03"])]
                     .groupby(["data_month", "store_id"]).size().groupby("store_id").mean())
april_ratio = len(april_shop) / prior_shop_counts.get("SHOP_003", 0) if prior_shop_counts.get("SHOP_003", 0) else 0
april_new_shop = "SHOP_005" in set(month_rows("2026-04").store_id)
event_check("2026-04", "SHOP_003 outage and SHOP_005 opening",
            f"SHOP_003 volume {april_ratio:.0%} of Jan-Mar average; SHOP_005 present={april_new_shop}",
            "SHOP_003 below 50%; SHOP_005 appears", april_ratio < 0.5 and april_new_shop)

may_shop = month_rows("2026-05").query("store_id == 'SHOP_002'")
prior_discounts = rows[rows.data_month.isin(["2026-01", "2026-02", "2026-03", "2026-04"])].query("store_id == 'SHOP_002'")
discount_ratio = may_shop.discount_pct.mean() / prior_discounts.discount_pct.mean() if len(prior_discounts) and prior_discounts.discount_pct.mean() else 0
has_loyalty = "loyalty_points" in rows.columns and month_rows("2026-05").loyalty_points.notna().any()
event_check("2026-05", "SHOP_002 discounting and new column",
            f"Average discount {discount_ratio:.2f}x prior baseline; loyalty_points present={has_loyalty}",
            "discount at least 1.5x baseline; loyalty_points appears", discount_ratio >= 1.5 and has_loyalty)

june_dairy = month_rows("2026-06").query("category == 'Dairy'")
prior_dairy = rows[rows.data_month.isin([f"2026-{m:02d}" for m in range(1, 6)])].query("category == 'Dairy'")
june_low_stock = (june_dairy.stock_at_start <= 4).mean() if len(june_dairy) else 0
prior_low_stock = (prior_dairy.stock_at_start <= 4).mean() if len(prior_dairy) else 0
june_rule_hits = june_dairy.rules_broken.fillna("").str.contains("sold_more_than_stock").sum()
event_check("2026-06", "Dairy supplier shortage",
            f"Dairy stock at 4 or less: {june_low_stock:.0%} (baseline {prior_low_stock:.0%}); stock-rule hits={june_rule_hits}",
            "low-stock rate rises and stock rule flags affected sales", june_low_stock > prior_low_stock + 0.2 and june_rule_hits > 0)

july_pc = month_rows("2026-07").query("category == 'Personal Care'")
prior_pc = rows[rows.data_month.isin([f"2026-{m:02d}" for m in range(1, 7)])].query("category == 'Personal Care'")
july_return = july_pc.is_return.mean() if len(july_pc) else 0
prior_return = prior_pc.is_return.mean() if len(prior_pc) else 0
event_check("2026-07", "Personal Care returns spike",
            f"Return rate {july_return:.1%} (Jan-Jun baseline {prior_return:.1%})",
            "return rate at least 2x baseline", july_return >= 2 * prior_return)

aug_shop = month_rows("2026-08").query("store_id == 'SHOP_004'")
aug_extreme = int((aug_shop.price_ratio >= 50).sum()) if len(aug_shop) else 0
aug_rule = int(aug_shop.rules_broken.fillna("").str.contains("price_far_from_normal").sum()) if len(aug_shop) else 0
event_check("2026-08", "SHOP_004 unit-price entry error",
            f"{aug_extreme} sales have price ratio >=50; {aug_rule} flagged by price rule",
            "extreme price rows appear and are flagged", aug_extreme > 0 and aug_rule > 0)

sep_new_product = month_rows("2026-09").query("product_id == 'P1017'")
event_check("2026-09", "New product P1017 launch", f"{len(sep_new_product)} P1017 sales",
            "P1017 appears", len(sep_new_product) > 0)

sep_volume = len(month_rows("2026-09"))
oct_volume = len(month_rows("2026-10"))
oct_ratio = oct_volume / sep_volume if sep_volume else 0
event_check("2026-10", "Festival sales surge", f"{oct_volume:,} rows, {oct_ratio:.2f}x September",
            "volume at least 1.35x September", oct_ratio >= 1.35)

nov_volume = len(month_rows("2026-11"))
nov_ratio = nov_volume / sep_volume if sep_volume else 0
event_check("2026-11", "Festive sales tail", f"{nov_volume:,} rows, {nov_ratio:.2f}x September",
            "volume at least 1.20x September", nov_ratio >= 1.20)

dec = month_rows("2026-12")
dec_shop4 = dec.query("store_id == 'SHOP_004'")
shop4_late = dec_shop4.freshness_days_late_clean.max() if len(dec_shop4) else 0
event_check("2026-12", "SHOP_004 late feed", f"Maximum measured lateness {shop4_late:.0f} days",
            "at least 18 days", shop4_late >= 18)

dec_shampoo = dec.query("store_id == 'SHOP_002' and product_id == 'P1006'")
shampoo_extreme = int((dec_shampoo.price_ratio >= 5).sum()) if len(dec_shampoo) else 0
shampoo_flagged = int(dec_shampoo.rules_broken.fillna("").str.contains("price_far_from_normal").sum()) if len(dec_shampoo) else 0
event_check("2026-12", "SHOP_002 shampoo price error",
            f"{shampoo_extreme} sales have price ratio >=5; {shampoo_flagged} flagged by price rule",
            "extreme price rows appear and are flagged", shampoo_extreme > 0 and shampoo_flagged > 0)

dec_missing = dec.customer_id.isna().mean() if len(dec) else 0
prior_months = rows[rows.data_month < "2026-12"]
prior_missing = prior_months.groupby("data_month").customer_id.apply(lambda s: s.isna().mean()).mean() if len(prior_months) else 0
missing_ratio = dec_missing / prior_missing if prior_missing else 0
event_check("2026-12", "Missing customer IDs increase",
            f"Missing rate {dec_missing:.1%}, {missing_ratio:.1f}x prior monthly average",
            "missing rate at least 2x prior average", missing_ratio >= 2)

print(f"\nEvent data checks: {sum(checks)}/{len(checks)} matched the expected signals.")
print("These checks confirm the event patterns are present and, where applicable, show their row-level engine flags.")

print("\n" + "=" * 72, "\n4) LATE SHOP FEEDS (freshness)\n" + "=" * 72)
late = pd.DataFrame(truth["late_shop_months"])
print("Planted late feeds:\n", late.to_string(index=False))
sh = pd.read_csv(os.path.join(R, "entity_trust.csv"))
sh = sh[(sh.entity_type == "SHOP") & (sh.data_month == "2026-12")][["entity_id", "freshness_health", "overall_trust_score"]]
print("\nDecember trust per shop (SHOP_004 should be lowest - its data came 18 days late):\n", sh.round(3).to_string(index=False))

print("\n" + "=" * 72, "\n5) CHURN: did it rank the planted churners higher?\n" + "=" * 72)
ch = pd.read_csv(os.path.join(R, "churn_risk.csv"))
ch["planted_churner"] = ch.entity_id.isin(truth["churned_customers_last_active_before_nov"]).astype(int)
pos, neg = ch[ch.planted_churner == 1].churn_risk_score, ch[ch.planted_churner == 0].churn_risk_score
auc = (pos.values[:, None] > neg.values[None, :]).mean() + 0.5 * (pos.values[:, None] == neg.values[None, :]).mean()
top = ch.head(200)
print(f"Customers scored: {len(ch):,} | planted churners seen in data: {int(ch.planted_churner.sum())}")
print(f"AUC (1.0 perfect, 0.5 = coin flip): {auc:.3f}")
print(f"Of the 200 highest-risk customers, {int(top.planted_churner.sum())} are real planted churners "
      f"({100 * top.planted_churner.mean():.0f}%) vs {100 * ch.planted_churner.mean():.0f}% in the whole base")

print("\n" + "=" * 72, "\n6) ALERTS RAISED\n" + "=" * 72)
al = pd.read_csv(os.path.join(R, "alerts.csv"))
print(al.groupby(["alert_type", "alert_level"]).size().to_string())
print("\nNon-customer alerts:\n", al[al.entity_type != "CUSTOMER"][["alert_level", "alert_type", "entity_type", "entity_id", "month", "metric", "value"]].head(20).to_string(index=False))
drift = json.load(open(os.path.join(R, "drift_summary.json")))
for k in ("PREVIOUS_MONTH", "CURRENT_MONTH"):
    if k in drift:
        print(f"\nDrift {k}: psi_health={drift[k]['psi_health']}, welch_health={drift[k]['welch_health']}, "
              f"drifted features={drift[k]['drifted_features']}")
