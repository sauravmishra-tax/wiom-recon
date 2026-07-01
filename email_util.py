"""
Email sending via SMTP (stdlib smtplib). Pure helper — caller passes config.
Works with Gmail (app password), Office365, or any SMTP server.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication


def send_email(cfg, to, subject, html_body, attachment=None, attachment_name='report.xlsx', attachments=None):
    """cfg = dict(host, port, user, password, from_addr, use_tls).
    attachments: optional list of (bytes, filename) tuples for multiple files
    (attachment/attachment_name kept for backward compatibility, sent as an
    extra attachment if provided). Returns (ok: bool, message: str). Never raises."""
    host = (cfg.get('host') or '').strip()
    if not host:
        return False, 'SMTP not configured.'
    to_list = [t.strip() for t in (to.split(',') if isinstance(to, str) else to) if t and t.strip()]
    if not to_list:
        return False, 'No recipient.'
    try:
        msg = MIMEMultipart()
        msg['From'] = cfg.get('from_addr') or cfg.get('user')
        msg['To'] = ', '.join(to_list)
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html'))
        if attachment:
            part = MIMEApplication(attachment, Name=attachment_name)
            part['Content-Disposition'] = f'attachment; filename="{attachment_name}"'
            msg.attach(part)
        for data, fname in (attachments or []):
            part = MIMEApplication(data, Name=fname)
            part['Content-Disposition'] = f'attachment; filename="{fname}"'
            msg.attach(part)

        port = int(cfg.get('port') or 587)
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            if str(cfg.get('use_tls', '1')) not in ('0', 'false', 'False', ''):
                server.starttls()
        if cfg.get('user'):
            server.login(cfg['user'], cfg.get('password') or '')
        server.sendmail(msg['From'], to_list, msg.as_string())
        server.quit()
        return True, f'Sent to {", ".join(to_list)}'
    except Exception as e:
        return False, str(e)


def test_smtp(cfg):
    """Validate SMTP by connecting + logging in (no message sent)."""
    host = (cfg.get('host') or '').strip()
    if not host:
        return False, 'Enter SMTP host first.'
    try:
        port = int(cfg.get('port') or 587)
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if str(cfg.get('use_tls', '1')) not in ('0', 'false', 'False', ''):
                server.starttls()
        if cfg.get('user'):
            server.login(cfg['user'], cfg.get('password') or '')
        server.quit()
        return True, 'SMTP connection OK.'
    except Exception as e:
        return False, str(e)
