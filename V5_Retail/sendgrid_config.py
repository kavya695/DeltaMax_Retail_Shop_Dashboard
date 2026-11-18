"""Shared, secret-safe SendGrid configuration for the Retail backend."""
from __future__ import annotations

import os
import re
from email.utils import parseaddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_ENV_PATH = ROOT / ".env"

try:
    from dotenv import load_dotenv as _python_dotenv_load
except ImportError:  # Keep the existing venv usable until requirements are installed.
    _python_dotenv_load = None


def _load_env_file_fallback(path: Path) -> None:
    """Minimal .env reader used only if python-dotenv is not installed."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(name, value)


def load_backend_environment(path: Path | str = BACKEND_ENV_PATH) -> str:
    """Load the backend-local .env without overriding explicit process settings."""
    env_path = Path(path)
    if _python_dotenv_load is not None:
        _python_dotenv_load(dotenv_path=env_path, override=False, encoding="utf-8")
        return "python-dotenv"
    _load_env_file_fallback(env_path)
    return "stdlib-fallback"


ENV_LOADER = load_backend_environment()


def _valid_email(value: str) -> bool:
    parsed_name, parsed_address = parseaddr(value)
    return bool(parsed_address == value and re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value))


def get_sendgrid_config_status() -> dict:
    """Return configuration presence only; never return the API key or sender."""
    api_key = os.getenv("DELTAMAX_SENDGRID_API_KEY", "").strip()
    sender = os.getenv("DELTAMAX_ALERT_SENDER", "").strip()
    sender_valid = _valid_email(sender) if sender else False
    api_key_valid = bool(api_key) and not any(character.isspace() for character in api_key)
    missing = []
    if not api_key:
        missing.append("DELTAMAX_SENDGRID_API_KEY")
    if not sender:
        missing.append("DELTAMAX_ALERT_SENDER")
    errors = []
    if api_key and not api_key_valid:
        errors.append("DELTAMAX_SENDGRID_API_KEY must contain only the API key, without whitespace or extra lines.")
    if sender and not sender_valid:
        errors.append("DELTAMAX_ALERT_SENDER must be a valid email address.")
    configured = not missing and not errors
    message = "SendGrid configuration loaded." if configured else (
        "Missing required environment variable(s): " + ", ".join(missing) + ". Configure them in V5_Retail/.env."
        if missing else "; ".join(errors) + " Configure them in V5_Retail/.env.")
    return {
        "ready": True,
        "provider": "SendGrid",
        "configured": configured,
        "api_key_configured": bool(api_key),
        "api_key_format_valid": api_key_valid if api_key else False,
        "sender_configured": bool(sender),
        "sender_format_valid": sender_valid,
        "backend_env_file_found": BACKEND_ENV_PATH.is_file(),
        "env_loader": ENV_LOADER,
        "missing": missing,
        "message": message,
    }


def require_sendgrid_config(require_receiver: bool = False) -> tuple[str, str, str | None]:
    """Read and validate credentials without including secret values in errors."""
    status = get_sendgrid_config_status()
    if status["missing"]:
        raise RuntimeError(status["message"])
    if not status["api_key_format_valid"]:
        raise RuntimeError("DELTAMAX_SENDGRID_API_KEY must contain only the API key, without whitespace or extra lines.")
    if not status["sender_format_valid"]:
        raise RuntimeError("DELTAMAX_ALERT_SENDER must be a valid, verified SendGrid sender email address.")
    receiver = os.getenv("DELTAMAX_ALERT_RECEIVER", "").strip() or None
    if require_receiver and not receiver:
        raise RuntimeError("Missing required environment variable: DELTAMAX_ALERT_RECEIVER. Configure it in V5_Retail/.env.")
    return os.getenv("DELTAMAX_SENDGRID_API_KEY", "").strip(), os.getenv("DELTAMAX_ALERT_SENDER", "").strip(), receiver


def sendgrid_error_message(status_code: int | None) -> str:
    if status_code == 401:
        return "SendGrid rejected the API key (401 Unauthorized). Replace it with an active key that has Mail Send permission."
    if status_code == 403:
        return "SendGrid denied the request (403 Forbidden). Check the key's Mail Send permission and verify the sender identity in SendGrid."
    if status_code == 400:
        return "SendGrid rejected the email request (400 Bad Request). Check the sender, recipient, and message fields."
    if status_code == 429:
        return "SendGrid rate limit reached (429). Wait briefly, then retry."
    if status_code is not None and status_code >= 500:
        return f"SendGrid is temporarily unavailable (HTTP {status_code}). Retry later."
    if status_code is not None:
        return f"SendGrid could not accept the email (HTTP {status_code}). Check the sender and account settings."
    return "Could not connect to SendGrid. Check the network connection and retry."


class SendGridDeliveryError(RuntimeError):
    def __init__(self, status_code: int | None):
        self.status_code = status_code
        super().__init__(sendgrid_error_message(status_code))


def sendgrid_send(message) -> int:
    """Send a SendGrid SDK message and return only its successful HTTP status."""
    api_key, _, _ = require_sendgrid_config()
    try:
        from sendgrid import SendGridAPIClient
    except ImportError:
        raise RuntimeError("The SendGrid SDK is unavailable. Run: pip install -r requirements.txt") from None
    try:
        response = SendGridAPIClient(api_key).send(message)
    except Exception as exc:
        # SDK HTTP errors expose status_code; never serialize or log the raw
        # exception because request headers can contain the bearer credential.
        status_code = getattr(exc, "status_code", None)
        raise SendGridDeliveryError(status_code) from None
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code not in {200, 201, 202}:
        raise SendGridDeliveryError(status_code)
    return status_code
