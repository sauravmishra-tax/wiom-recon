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


def post_message_bot(bot_token, channel, text, blocks=None):
    """Post via Slack Bot Token (chat.postMessage) — works with a bot already
    added to the channel, no incoming-webhook setup needed. Returns (ok, message)."""
    if not bot_token or not channel:
        return False, 'Slack bot token / channel not configured.'
    payload = {'channel': channel, 'text': text}
    if blocks:
        payload['blocks'] = blocks
    try:
        req = urllib.request.Request(
            'https://slack.com/api/chat.postMessage',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {bot_token}'}, method='POST')
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        if body.get('ok'):
            return True, 'sent'
        return False, body.get('error', 'unknown error')
    except Exception as e:
        return False, str(e)


def build_cfo_summary_blocks(title, ctx):
    """Build Slack blocks mirroring the /cfo-summary page: State Health + Reconciliation Summary."""
    def section(md):
        return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': md}}
    dot = {'red': '🔴', 'yellow': '🟡', 'green': '🟢', 'grey': '⚪'}

    blocks = [{'type': 'header', 'text': {'type': 'plain_text', 'text': f'📊 {title}'}}]

    if ctx.get('state_health'):
        blocks.append(section('*🚦 State Health Status*\n' + '\n'.join(
            f"{dot.get(s['light'], '⚪')} *{s['state']}* — ⚠️ {s['open']} open · 💬 {s['remarked']} remarked · "
            f"✅ {s['approved']} done · ITC risk ₹{s['itc_risk']:,} · last upload {s['last_upload']}"
            for s in ctx['state_health'])))

    rs = ctx.get('recon_summary', {})
    blocks.append(section(
        '*📋 Reconciliation Summary*\n'
        f"🔗 Expert Cross-Match: *{rs.get('cross', 0)}* · ₹{rs.get('cross_amt', 0):,}\n"
        f"📕 Books Only: *{ctx.get('books_only', 0)}* · ₹{rs.get('books_amt', 0):,} (ITC at risk)\n"
        f"🧾 GSTR-2B Only: *{ctx.get('gstn_only', 0)}* · ₹{rs.get('gstn_amt', 0):,} (unbooked)\n"
        f"✅ Fully Reconciled: *{ctx.get('fully', 0)}* · ₹{rs.get('fully_amt', 0):,} (ITC safe)\n"
        f"🚫 Rejected — ITC Ineligible: *{ctx.get('rejected', 0)}* · ₹{rs.get('rejected_amt', 0):,}\n"
        f"📊 GSTIN Gap Analysis: *{rs.get('gap', 0)}* · ₹{rs.get('gap_amt', 0):,} (net gap)\n"
        f"🧾 Total Invoice Rows: *{ctx.get('total', 0):,}*"
    ))

    blocks.append({'type': 'context', 'elements': [
        {'type': 'mrkdwn', 'text': 'WIOM GST Recon · Books × GST · auto-generated'}]})
    return blocks


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
        {'type': 'mrkdwn', 'text': 'WIOM GST Recon · Books × GST · auto-generated'}]})
    return blocks
