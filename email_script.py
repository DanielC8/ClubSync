"""SMTP report email, dry-run capable: send_email(), load_email_config()."""

import smtplib
from email.message import EmailMessage
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_email_config():
    """The `email:` block from config.yaml, or {} if the file's missing."""
    try:
        with open(CONFIG_PATH) as handle:
            config = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return {}
    return config.get("email") or {}


def send_email(subject, sender_email, smtp_host, smtp_port, smtp_username,
               smtp_password, recipient_email, content="", file_name=None,
               cc=None, use_tls=True, dry_run=False):
    """Build the report email and send it. On dry_run (or with no password) it
    returns a preview dict instead of sending, which is what the web page shows."""
    cc = list(cc or [])

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender_email
    message["To"] = recipient_email
    if cc:
        message["Cc"] = ", ".join(cc)
    message.set_content(content)

    attachment = None
    if file_name:
        path = Path(file_name)
        if path.exists():
            message.add_attachment(
                path.read_bytes(), maintype="text", subtype="csv", filename=path.name
            )
            attachment = path.name

    preview = {
        "subject": subject,
        "to": recipient_email,
        "cc": cc,
        "content": content,
        "attachment": attachment,
        "sent": False,
    }

    if dry_run or not smtp_password:
        return preview

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as err:
        # lots of networks block outbound SMTP — don't 500 over it
        preview["error"] = str(err)
        return preview

    preview["sent"] = True
    return preview
