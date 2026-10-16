"""Outgoing email. With SMTP_HOST set it sends through SMTP (STARTTLS on 587, implicit SSL on 465);
otherwise it prints the message to the server console so verification/reset links work in local development."""
import smtplib
import ssl
from email.message import EmailMessage

import config

OUTBOX = []  # most recent messages, for dev inspection and tests


def send(to: str, subject: str, body: str):
    OUTBOX.append({"to": to, "subject": subject, "body": body})
    del OUTBOX[:-50]
    if not config.SMTP_HOST:
        print(f"\n[mail -> {to}] {subject}\n{body}\n", flush=True)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if config.SMTP_PORT == 465:
            smtp = smtplib.SMTP_SSL(config.SMTP_HOST, 465, timeout=15, context=ctx)
        else:
            smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
            smtp.starttls(context=ctx)
        with smtp:
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:  # never fail the request because the mail server is down
        print(f"[mail] could not send to {to}: {e}", flush=True)
