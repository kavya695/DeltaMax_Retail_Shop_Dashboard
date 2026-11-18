# DeltaMax V5 – Retail Shop Edition (Local Only)

This folder keeps the six-script naming convention from the V5 credit-bureau project, but uses the **retail shop data and engine logic** from `deltamax_engine`.

## What changed
- Credit-bureau data and Oracle database dependencies are not used by these scripts.
- Retail transaction data is generated and processed locally.
- Results are written under `data/` and `results/retail/`.
- Retail data and result files are processed/stored locally; alert emails are sent through SendGrid only when `results/retail/alerts.csv` contains alerts.
- The shared engine in `engine/` contains the retail quality checks, anomaly detection, drift, freshness, trust scoring, forecasts, alerts, and customer churn logic.

## Script names and purpose
1. `01_generate_data.py` – generates January–December retail data and local monthly CSVs.
2. `02_hab.py` – checks the combined retail dataset and its month/lineage counts.
3. `03_Trust_Score.py` – runs the retail trust engine and writes local scoring/report outputs.
4. `04_ARIMA_Predictive_Trust_Alerts.py` – displays saved forecast and alert outputs created by the engine.
5. `05_alert_notifier.py` – writes a local text alert report and emails non-empty alerts through SendGrid.
6. `06_Business_Churn_Risk_Prediction.py` – displays customer churn-risk results produced by the engine.

## Setup (Windows PowerShell)
Run these commands from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run the pipeline
Run in order:

```powershell
python 01_generate_data.py
python 02_hab.py
python 03_Trust_Score.py
python 04_ARIMA_Predictive_Trust_Alerts.py
python 05_alert_notifier.py
python 06_Business_Churn_Risk_Prediction.py
```

## Connect the frontend

The existing Vite frontend requests `GET /api/churn-risk`. Start the local API
from this folder, then start the frontend in a second PowerShell window:

```powershell
python api.py
cd frontend
npm run dev
```

From the `frontend` folder, `npm run dev` now starts both the retail API and
Vite together. Use `npm run dev:vite` only when the API is already running.

The API reads `results/retail/churn_risk.csv` and also exposes
`POST /api/churn-risk/email`; SendGrid credentials remain server-side in the
same environment variables used by the churn report script.

The Core Engine > Hyperparameters page is connected to the retail backend:
`GET /api/hyperparams` returns only retail-supported tuning fields, while
`POST /api/hyperparams` validates and saves anomaly weights, trust-pillar
weights, the IQR multiplier, and freshness tau. Saved values are written to
`results/retail/hyperparams.json` and are applied by the next run of
`03_Trust_Score.py`; changes are also recorded in
`results/retail/hyperparams_history.json`.

To regenerate a different deterministic dataset:
`python 01_generate_data.py --year 2026 --seed 7`

To use a different random seed:
`python 01_generate_data.py --random`

**Note:** `04` and `06` read outputs created by `03`; run `03` first. Generated data and result files are local CSV/JSON/TXT files.


## SendGrid alert email setup

The notifier reads `results/retail/alerts.csv`, saves `results/retail/retail_alert_report.txt`, and sends an email only when the CSV contains one or more alert rows.

The backend loads SendGrid settings from `V5_Retail/.env` at startup. The separate `frontend/.env` file is for Vite and is not read by the Python backend. Open `V5_Retail/.env` and set:

```dotenv
DELTAMAX_SENDGRID_API_KEY=YOUR_NEW_SENDGRID_API_KEY
DELTAMAX_ALERT_SENDER=your-verified-sender@example.com
DELTAMAX_ALERT_RECEIVER=recipient@example.com
```

Keep the API key only in this backend `.env` file; never place it in frontend code, `frontend/.env`, API responses, logs, or Git. The backend prefers process environment values if they were explicitly set; otherwise it reads this file with `python-dotenv` (or the built-in compatible fallback until dependencies are installed). Restart the backend after editing it. In SendGrid, the key needs Mail Send permission and the sender address must be verified. If no alerts are present, no email is sent. Local CSV/report storage remains local; SendGrid receives the alert email content.
