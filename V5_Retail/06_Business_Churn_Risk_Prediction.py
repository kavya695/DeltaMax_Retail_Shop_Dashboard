"""DeltaMax V5 Retail customer churn-risk report and SendGrid email notification."""
import os
import html
from collections import Counter

import pandas as pd
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from python_http_client.exceptions import HTTPError

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(ROOT, "results", "retail")
PATH = os.path.join(RESULTS_DIR, "churn_risk.csv")
EMAIL_TOP_N = 20


def send_churn_email(df):
    api_key = os.getenv("DELTAMAX_SENDGRID_API_KEY")
    sender = os.getenv("DELTAMAX_ALERT_SENDER")
    receiver = os.getenv("DELTAMAX_ALERT_RECEIVER")
    missing = [
        name for name, value in (
            ("DELTAMAX_SENDGRID_API_KEY", api_key),
            ("DELTAMAX_ALERT_SENDER", sender),
            ("DELTAMAX_ALERT_RECEIVER", receiver),
        ) if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
            + ". Configure them in PowerShell before running this script."
        )

    risk_col = "risk_level" if "risk_level" in df.columns else None
    if risk_col:
        counts = Counter(df[risk_col].fillna("UNKNOWN").astype(str).str.upper())
        summary = " | ".join(f"{level}: {count}" for level, count in counts.most_common())
    else:
        summary = f"{len(df)} customers scored"

    top = df.head(EMAIL_TOP_N).copy()
    columns = list(top.columns)
    header = "".join(
        f'<th style="padding:8px;text-align:left;background:#263238;color:#fff;">{html.escape(str(c))}</th>'
        for c in columns
    )
    rows = []
    for _, row in top.iterrows():
        cells = "".join(
            f'<td style="padding:7px;border-bottom:1px solid #ddd;">{html.escape(str(v))}</td>'
            for v in row.tolist()
        )
        rows.append(f"<tr>{cells}</tr>")

    body = f"""<!doctype html><html><body style="font-family:Arial,sans-serif;color:#222;">
    <div style="background:#263238;color:white;padding:16px;border-radius:6px;">
      <h2>DeltaMax V5 Retail - Customer Churn Risk</h2>
      <p>Customer churn-risk prediction summary</p>
    </div>
    <p><b>Total customers scored:</b> {len(df):,}</p>
    <p><b>Risk-level summary:</b> {html.escape(summary)}</p>
    <h3>Top {len(top)} customers by churn risk</h3>
    <table style="border-collapse:collapse;width:100%;font-size:12px;">
      <thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody>
    </table>
    <p style="color:#777;font-size:12px;">Full churn results remain in the local results folder.</p>
    </body></html>"""

    message = Mail(
        from_email=sender,
        to_emails=receiver,
        subject=f"DeltaMax V5 Retail - Churn Risk Report ({len(df)} customers)",
        html_content=body,
    )
    try:
        response = SendGridAPIClient(api_key).send(message)
    except HTTPError as exc:
        error_body = exc.body.decode("utf-8", errors="replace") if isinstance(exc.body, bytes) else str(exc.body)
        raise RuntimeError(f"SendGrid returned status {exc.status_code}: {error_body}") from exc

    if response.status_code not in (200, 201, 202):
        raise RuntimeError(f"SendGrid returned status {response.status_code}: {response.body}")
    print(f"[OK] Churn-risk email accepted by SendGrid (HTTP {response.status_code}).")


def main():
    if not os.path.exists(PATH):
        raise SystemExit(f"Churn results not found: {PATH}\\nRun python 03_Trust_Score.py first.")

    df = pd.read_csv(PATH)
    print(f"Customers scored: {len(df):,}")
    if "risk_level" in df:
        print("\\nRisk-level counts:")
        print(df["risk_level"].value_counts(dropna=False).to_string())
    print(f"\\nTop {min(10, len(df))} customers by churn risk:")
    print(df.head(10).to_string(index=False))
    print("\\nLocal results:", PATH)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    report_path = os.path.join(RESULTS_DIR, "retail_customer_churn_risk_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"DeltaMax V5 Retail Customer Churn Risk Report\\nCustomers scored: {len(df):,}\\n\\n")
        if "risk_level" in df:
            f.write("Risk-level counts:\\n")
            f.write(df["risk_level"].value_counts(dropna=False).to_string())
            f.write("\\n\\n")
        f.write(f"Top {min(10, len(df))} customers by churn risk:\\n")
        f.write(df.head(10).to_string(index=False))
        f.write("\\n")
    print("Local report:", report_path)

    if df.empty:
        print("[INFO] No customer rows found; no email was sent.")
        return
    send_churn_email(df)


if __name__ == "__main__":
    main()
