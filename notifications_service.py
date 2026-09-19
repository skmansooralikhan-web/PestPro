"""
PCT CRM — outbound notification dispatch.

Wraps the WhatsApp Cloud API (via `requests`) and SMTP email (via
`smtplib`), per the spec's tech stack. Both are safe to call at any
time: if the relevant credentials in Settings are blank, the function
logs a no-op and returns False instead of raising, so the rest of the
app (visit scheduling, job-card submission, invoicing) never breaks
because a notification couldn't be sent.

Wire your own WhatsApp Business (Meta Cloud API) or SMTP credentials
into Settings to make these live — nothing else needs to change.
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import db as db_module

logger = logging.getLogger("pct_crm.notifications")


def _settings():
    return db_module.get_all_settings()


def send_whatsapp(to_phone: str, message: str) -> bool:
    """Send a WhatsApp message via the Meta Cloud API.

    Returns True only on a confirmed successful send. Silently returns
    False (after logging) if credentials are missing, the request
    fails, or the `requests` library / network isn't available —
    callers should treat this as best-effort and never depend on it
    for correctness.
    """
    s = _settings()
    api_key = s.get("whatsappApiKey", "")
    phone_id = s.get("whatsappPhoneId", "")
    if not api_key or not phone_id:
        logger.info("WhatsApp not configured — skipping send to %s", to_phone)
        return False
    if not to_phone:
        return False

    try:
        import requests
    except ImportError:
        logger.warning("`requests` not installed — cannot send WhatsApp message")
        return False

    digits = "".join(ch for ch in to_phone if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits  # default to India country code for bare 10-digit numbers

    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": digits,
        "type": "text",
        "text": {"body": message},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code in (200, 201):
            return True
        logger.warning("WhatsApp send failed (%s): %s", resp.status_code, resp.text[:300])
        return False
    except Exception as exc:  # network errors, timeouts, DNS, etc.
        logger.warning("WhatsApp send raised %s", exc)
        return False


def send_email(to_email: str, subject: str, body: str,
                attachment_bytes: bytes = None, attachment_name: str = None) -> bool:
    """Send an email via SMTP using the credentials configured in Settings.

    Returns True only on confirmed delivery to the SMTP server. Silently
    returns False (after logging) if SMTP isn't configured, the
    recipient is blank, or the connection/auth fails.
    """
    s = _settings()
    server = s.get("smtpServer", "")
    username = s.get("smtpUsername", "")
    password = s.get("smtpPassword", "")
    port = int(s.get("smtpPort", "587") or 587)
    if not server or not username or not password:
        logger.info("SMTP not configured — skipping email to %s", to_email)
        return False
    if not to_email:
        return False

    msg = MIMEMultipart()
    msg["From"] = username
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if attachment_bytes and attachment_name:
        part = MIMEApplication(attachment_bytes, Name=attachment_name)
        part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
        msg.attach(part)

    try:
        with smtplib.SMTP(server, port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.sendmail(username, [to_email], msg.as_string())
        return True
    except Exception as exc:  # auth failure, connection refused, timeout, etc.
        logger.warning("Email send to %s raised %s", to_email, exc)
        return False


def notify_visit_reminder(client_phone: str, client_name: str, visit_date: str) -> bool:
    if db_module.get_setting("notifyVisitReminder", "1") != "1":
        return False
    message = (f"Hi, this is a reminder from {db_module.get_setting('companyName','')} — "
               f"your pest control visit is scheduled for {visit_date}. "
               f"Please ensure access to the premises. Thank you!")
    return send_whatsapp(client_phone, message)


def notify_post_visit_feedback(client_phone: str, feedback_url: str) -> bool:
    message = (f"Thank you for choosing {db_module.get_setting('companyName','')}! "
               f"We'd love your feedback on today's visit: {feedback_url}")
    return send_whatsapp(client_phone, message)


def notify_amc_expiry(client_phone: str, client_email: str, contract_number: str,
                       days_left: int) -> bool:
    if db_module.get_setting("notifyAmcExpiry", "1") != "1":
        return False
    message = (f"Your AMC contract {contract_number} with "
               f"{db_module.get_setting('companyName','')} expires in {days_left} days. "
               f"Contact us to renew and avoid a coverage gap.")
    sent = send_whatsapp(client_phone, message)
    if client_email:
        sent = send_email(
            client_email, f"AMC Renewal Reminder — {contract_number}", message
        ) or sent
    return sent


def email_invoice(client_email: str, inv_number: str, pdf_bytes: bytes) -> bool:
    if not client_email:
        return False
    company_name = db_module.get_setting("companyName", "")
    body = (f"Dear Customer,\n\nPlease find attached invoice {inv_number} from "
            f"{company_name}.\n\n{db_module.get_setting('invoiceFooter','Thank you for your business')}")
    filename = f"{inv_number.replace('/', '-')}.pdf"
    return send_email(client_email, f"Invoice {inv_number} — {company_name}", body,
                       attachment_bytes=pdf_bytes, attachment_name=filename)
