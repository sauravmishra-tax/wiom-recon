"""
Slack notifications via Incoming Webhook (no dependency, uses urllib).
Set up once: api.slack.com -> Incoming Webhooks -> pick channel -> copy URL.
Paste the URL in Settings. Works for a channel, group, or DM-to-self webhook.
"""
import json
import urllib.request


def post_message(webhook_url, text, blocks=None):
    """Post to a Slack incoming webhook. Returns (ok, message). Never raises."""
    if not webhook_url:
        return False, 'Slack webhook not configured.'
    payload = {'text': text}
    if blocks:
        payload['blocks'] = blocks
    try:
        req = urllib.request.Request(
            webhook_url, data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
        return (body.strip() == 'ok'), (body or 'sent')
    except Exception as e:
        return False, str(e)


def build_report_blocks(title, kpis, by_state, by_reason, top_vendors):
    """Build a tidy Slack Block-Kit message for the daily recon report.
    kpis: list of (label, value). by_state/by_reason: list of (name, count, value).
    top_vendors: list of (gstin, vendor, grade, itc_risk)."""
    def section(md):
        return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': md}}

    blocks = [
        {'type': 'header', 'text': {'type': 'plain_text', 'text': f'📊 {title}'}},
        section('*Key numbers*\n' + '\n'.join(f'• {l}: *{v}*' for l, v in kpis)),
    ]
    if by_state:
        blocks.append(section('*State-wise gap*\n' + '\n'.join(
            f'• {n}: {c} rows · ₹{v:,.0f}' for n, c, v in by_state)))
    if by_reason:
        blocks.append(section('*By reason (team-tagged)*\n' + '\n'.join(
            f'• {n}: {c} · ₹{v:,.0f}' for n, c, v in by_reason)))
    if top_vendors:
        blocks.append(section('*Top defaulter vendors*\n' + '\n'.join(
            f'• {g} {("· " + ven) if ven else ""} — grade *{gr}*, ITC risk ₹{risk:,.0f}'
            for g, ven, gr, risk in top_vendors)))
    blocks.append({'type': 'context', 'elements': [
        {'type': 'mrkdwn', 'text': 'WIOM Recon · Books × GST · auto-generated'}]})
    return blocks
