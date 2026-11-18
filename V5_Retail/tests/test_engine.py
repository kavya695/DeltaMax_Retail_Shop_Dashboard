"""Run: python tests/test_engine.py   (no pytest needed)"""
import os, sys, tempfile, textwrap
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine.pipeline import run_project
from engine.config import ConfigError
from engine.scaffold import draft_settings

passed = 0
def ok(name):
    global passed; passed += 1; print(f"  PASS  {name}")

tmp = tempfile.mkdtemp()
rng = np.random.default_rng(1)

# ---- a brand-new domain the engine has never seen: hospital admissions ----
rows = []
for m in range(1, 13):
    for i in range(300):
        rows.append(dict(admission_id=f"A{m:02d}{i:04d}", month=f"2026-{m:02d}", ward_id=f"W{rng.integers(1,6)}",
                         patient_age=int(rng.integers(1, 90)), stay_days=float(rng.gamma(2, 2)),
                         bill_amount=float(rng.normal(5000, 800))))
df = pd.DataFrame(rows)
# planted problems in the LAST month
last = df.index[df.month == "2026-12"]
df.loc[last[0], "patient_age"] = -5          # impossible age
df.loc[last[1], "stay_days"] = 400           # absurd stay
df.loc[last[2], "bill_amount"] = -100        # negative bill
df.loc[last[3], "admission_id"] = df.loc[last[4], "admission_id"]   # duplicate id
csv = os.path.join(tmp, "hospital.csv"); df.to_csv(csv, index=False)

cfg_text = textwrap.dedent(f"""
project: hospital
input: {{path: {csv}}}
columns: {{month: month}}            # no lineage column -> engine derives it
entities: [{{type: WARD, column: ward_id, grain: month}}]
duplicate_key: [admission_id]
features: [patient_age, stay_days, bill_amount]
rules:
  - {{name: bad_age,  severity: CRITICAL, when: "(patient_age < 0) | (patient_age > 120)"}}
  - {{name: bad_bill, severity: CRITICAL, when: "bill_amount < 0"}}
  - {{name: long_stay, severity: HIGH,    when: "stay_days > 60"}}
forecast: {{series: [{{name: monthly_bill, column: bill_amount, agg: sum, level: ALL}}]}}
output_dir: {tmp}/out
""")
cfg_path = os.path.join(tmp, "hospital.yaml"); open(cfg_path, "w").write(cfg_text)

res = run_project(cfg_path, ROOT, send_email=False, verbose=False)
d = res["df"]
cur = d[d.data_lineage == "CURRENT_MONTH"]
ok("new domain runs with only a settings file (no code change)")
assert (d.data_month.max() == cur.data_month.max()) and len(cur) == 300; ok("lineage auto-derived from months")
bad = cur.iloc[:5]
assert bad.iloc[0].rule_violations >= 1 and bad.iloc[1].rule_violations >= 1 and bad.iloc[2].rule_violations >= 1
ok("rules caught impossible age, absurd stay, negative bill")
assert bad.iloc[3].duplicate_flag == 1 and bad.iloc[4].duplicate_flag == 1; ok("duplicate id caught")
assert bad.iloc[:4].overall_trust_score.max() < cur.iloc[10:].overall_trust_score.median(); ok("bad rows score lower than clean rows")
assert "freshness" not in res["manifest"]["pillars_used"]; ok("unconfigured freshness excluded, not faked as perfect")
assert os.path.exists(os.path.join(res["outdir"], "forecasts.csv")); ok("forecast file written")

# ---- clean data must NOT produce a flood of alerts ----
clean = df.copy(); clean.loc[last[:5], :] = df.loc[last[10:15]].values
clean["admission_id"] = [f"C{i}" for i in range(len(clean))]
c2 = os.path.join(tmp, "clean.csv"); clean.to_csv(c2, index=False)
open(os.path.join(tmp, "clean.yaml"), "w").write(cfg_text.replace(csv, c2).replace("project: hospital", "project: clean"))
r2 = run_project(os.path.join(tmp, "clean.yaml"), ROOT, send_email=False, verbose=False)
assert r2["manifest"]["mean_trust"] > 0.9 and len(r2["alerts"]) <= 3, (r2["manifest"]["mean_trust"], len(r2["alerts"]))
ok(f"clean data stays quiet ({len(r2['alerts'])} alerts, trust {r2['manifest']['mean_trust']})")

# ---- friendly failures ----
def expect_error(text, label, contains):
    p = os.path.join(tmp, "x.yaml"); open(p, "w").write(text)
    try:
        run_project(p, ROOT, send_email=False, verbose=False)
    except ConfigError as e:
        assert contains in str(e), str(e); ok(label); return
    raise AssertionError(label + " did not raise")

expect_error(cfg_text.replace("features: [patient_age, stay_days, bill_amount]", "features: [patient_age, nope_column]"),
             "missing column gives a clear message", "nope_column")
expect_error(cfg_text.replace("bill_amount < 0", "bill_amount <<< 0"), "broken rule gives a clear message", "bad_bill")
expect_error(cfg_text.replace("severity: HIGH", "severity: SERIOUS"), "bad severity rejected", "severity")
expect_error("project: x\n", "empty settings rejected", "input.path")

# ---- scaffold ----
out = draft_settings(csv, "hospital2", os.path.join(tmp, "hospital2.yaml"))
txt = open(out).read(); assert "TODO" in txt and "patient_age" in txt and "month: month" in txt; ok("scaffold drafts a settings file")


# ---- retail data generator (V5 style) ----
import subprocess
gdir = os.path.join(tmp, "gen"); os.makedirs(gdir)
subprocess.run([sys.executable, os.path.join(ROOT, "generate_retail_data.py"), "--data-dir", gdir, "--seed", "7"],
               check=True, capture_output=True)
g = pd.read_csv(os.path.join(gdir, "retail_shop_data.csv"))
assert g.data_month.nunique() == 12 and len(os.listdir(os.path.join(gdir, "retail_monthly"))) == 12
assert set(g.data_lineage) == {"HISTORICAL", "PREVIOUS_MONTH", "CURRENT_MONTH"}
assert g.store_id.nunique() == 6 and g.loc[g.data_month == "2026-01", "store_id"].nunique() == 4; ok("generator: 12 months, shops open mid-year, 3 lineages")
assert g.loc[g.data_month < "2026-05", "loyalty_points"].isna().all() and g.loc[g.data_month >= "2026-05", "loyalty_points"].notna().all(); ok("generator: new column appears mid-year (schema evolution)")
g1 = pd.read_csv(os.path.join(gdir, "retail_shop_data.csv"))
subprocess.run([sys.executable, os.path.join(ROOT, "generate_retail_data.py"), "--data-dir", gdir, "--seed", "7"], check=True, capture_output=True)
assert g1.equals(pd.read_csv(os.path.join(gdir, "retail_shop_data.csv"))); ok("generator: same seed gives same data")

# ---- shipped projects ----
for name, mn in (("retail", 0.9), ("credit_bureau", 0.8)):
    r = run_project(os.path.join(ROOT, "configs", f"{name}.yaml"), ROOT, send_email=False, verbose=False,
                    output_dir=os.path.join(tmp, name))
    assert r["manifest"]["mean_trust"] > mn; ok(f"shipped project '{name}' runs")
print(f"\nALL {passed} CHECKS PASSED")
