"""Optional SendGrid email. Keys come ONLY from environment variables."""
import base64
import os
from sendgrid_config import require_sendgrid_config, sendgrid_send


def send_alert_email(cfg, subject_tail, body_text, attachment_path=None):
    try:
        _, sender, receiver = require_sendgrid_config(require_receiver=True)
    except RuntimeError as exc:
        return False, str(exc)
    try:
        from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
    except ImportError:
        return False, "email skipped, run: pip install sendgrid"
    msg = Mail(from_email=sender, to_emails=[r.strip() for r in receiver.split(",") if r.strip()],
               subject=f"{cfg['notify'].get('subject_prefix', 'DeltaMax')} | {cfg['project']} | {subject_tail}",
               plain_text_content=body_text)
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as fh:
            enc = base64.b64encode(fh.read()).decode()
        msg.attachment = Attachment(FileContent(enc), FileName(os.path.basename(attachment_path)),
                                    FileType("text/csv"), Disposition("attachment"))
    try:
        status = sendgrid_send(msg)
        return True, f"SendGrid accepted email (HTTP {status})"
    except Exception as exc:
        return False, str(exc)
