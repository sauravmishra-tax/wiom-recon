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


def render_cfo_summary_image(title, ctx):
    """Render a PNG snapshot of the CFO Summary (State Health + Reconciliation
    Summary) using Pillow — no headless browser needed. Returns PNG bytes."""
    from PIL import Image, ImageDraw, ImageFont

    W = 1000
    PAD = 28
    dot_col = {'red': (214, 31, 58), 'yellow': (217, 167, 15), 'green': (11, 157, 99), 'grey': (195, 195, 211)}

    def font(size, bold=False):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    f_title = font(22, True); f_h3 = font(17, True); f_lbl = font(13, True)
    f_body = font(13); f_big = font(24, True); f_small = font(11)

    states = ctx.get('state_health', [])
    rows_of_states = (len(states) + 1) // 2  # 2 per row
    health_h = 60 + rows_of_states * 96
    recon_h = 60 + 34 + 12 + 118 * 2
    H = 90 + health_h + 20 + recon_h + PAD

    img = Image.new('RGB', (W, H), (240, 242, 247))
    d = ImageDraw.Draw(img)

    # header bar
    d.rectangle([0, 0, W, 70], fill=(26, 16, 35))
    d.text((PAD, 14), 'WIOM GST Recon', font=f_title, fill=(255, 255, 255))
    d.text((PAD, 42), 'Books x GST - Controller Summary', font=f_small, fill=(200, 170, 220))
    d.text((W - PAD, 24), title.split('(')[-1].rstrip(')'), font=f_body, fill=(255, 255, 255), anchor='ra')

    y = 90
    # ---- State Health card ----
    card_h = health_h
    d.rounded_rectangle([PAD, y, W - PAD, y + card_h], radius=14, fill=(255, 255, 255), outline=(228, 231, 240))
    d.text((PAD + 16, y + 16), 'State Health Status', font=f_h3, fill=(21, 23, 31))
    tile_w = (W - PAD * 2 - 16 * 3 - 24) / 2
    tx0 = PAD + 16
    ty0 = y + 50
    for i, s in enumerate(states):
        col = i % 2; row = i // 2
        tx = tx0 + col * (tile_w + 16)
        ty = ty0 + row * 96
        d.rounded_rectangle([tx, ty, tx + tile_w, ty + 84], radius=10, fill=(244, 246, 249), outline=(228, 231, 240))
        d.text((tx + 14, ty + 12), s['state'], font=f_lbl, fill=(21, 23, 31))
        dc = dot_col.get(s['light'], dot_col['grey'])
        d.ellipse([tx + tile_w - 30, ty + 14, tx + tile_w - 16, ty + 28], fill=dc)
        d.text((tx + 14, ty + 34), f"{s['open']} open  ·  {s['remarked']} remarked  ·  {s['approved']} done",
                font=f_small, fill=(74, 74, 92))
        d.text((tx + 14, ty + 52), f"ITC risk: Rs.{s['itc_risk']:,}", font=f_small, fill=(214, 31, 58))
        upl = s['last_upload'] + (f" ({s['last_period']})" if s.get('last_period') else '')
        d.text((tx + 14, ty + 68), f"Last upload: {upl}", font=f_small, fill=(138, 138, 168))
    y += card_h + 20

    # ---- Reconciliation Summary card ----
    rs = ctx.get('recon_summary', {})
    total = ctx.get('total', 0) or 1
    card_h2 = recon_h
    d.rounded_rectangle([PAD, y, W - PAD, y + card_h2], radius=14, fill=(255, 255, 255), outline=(228, 231, 240))
    d.text((PAD + 16, y + 16), 'Reconciliation Summary', font=f_h3, fill=(21, 23, 31))
    bar_y = y + 50
    bar_x = PAD + 16
    bar_w = W - PAD * 2 - 32 - 90
    segs = [
        (ctx.get('books_only', 0), (224, 17, 157), f"Not in Books {ctx.get('books_only', 0)}"),
        (ctx.get('gstn_only', 0), (34, 112, 216), f"Not in GSTR-2B {ctx.get('gstn_only', 0)}"),
        (ctx.get('fully', 0), (11, 157, 99), f"Fully Reconciled {ctx.get('fully', 0)}"),
    ]
    cx = bar_x
    for val, col, lbl in segs:
        w = (val / total) * bar_w
        if w > 1:
            d.rectangle([cx, bar_y, cx + w, bar_y + 34], fill=col)
            if w > 70:
                d.text((cx + w / 2, bar_y + 17), lbl, font=f_small, fill=(255, 255, 255), anchor='mm')
            cx += w
    d.rectangle([cx, bar_y, bar_x + bar_w + 90, bar_y + 34], fill=(238, 240, 246))
    d.text((bar_x + bar_w + 78, bar_y + 17), f"Total {total:,}", font=f_small, fill=(106, 112, 128), anchor='rm')
    bar_w += 90

    tiles = [
        (f"{rs.get('cross', 0)}", 'Expert Cross-Match', f"Rs.{rs.get('cross_amt', 0):,}", (11, 157, 99)),
        (f"{ctx.get('books_only', 0)}", 'Books Only', f"Rs.{rs.get('books_amt', 0):,}", (224, 17, 157)),
        (f"{ctx.get('gstn_only', 0)}", 'GSTR-2B Only', f"Rs.{rs.get('gstn_amt', 0):,}", (34, 112, 216)),
        (f"{ctx.get('fully', 0)}", 'Fully Reconciled', f"Rs.{rs.get('fully_amt', 0):,}", (11, 157, 99)),
        (f"{ctx.get('rejected', 0)}", 'Rejected - Ineligible', f"Rs.{rs.get('rejected_amt', 0):,}", (214, 31, 58)),
        (f"{rs.get('gap', 0)}", 'GSTIN Gap Analysis', f"Rs.{rs.get('gap_amt', 0):,}", (217, 112, 15)),
    ]
    grid_y = bar_y + 34 + 20
    tw = (bar_w - 12 * 2) / 3
    for i, (val, lbl, amt, col) in enumerate(tiles):
        c = i % 3; r = i // 3
        tx = bar_x + c * (tw + 12)
        ty = grid_y + r * (118 + 12)
        d.rounded_rectangle([tx, ty, tx + tw, ty + 108], radius=10, fill=(244, 246, 249), outline=(228, 231, 240))
        d.text((tx + 14, ty + 12), val, font=f_big, fill=col)
        d.text((tx + 14, ty + 44), lbl, font=f_small, fill=(74, 74, 92))
        d.text((tx + 14, ty + 66), amt, font=f_lbl, fill=(21, 23, 31))

    import io
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def upload_image_bot(bot_token, channel, image_bytes, filename, title, initial_comment=''):
    """Upload a PNG image to a Slack channel via the Bot Token (files.getUploadURLExternal
    -> upload -> files.completeUploadExternal flow). Returns (ok, message)."""
    import requests as _rq
    headers = {'Authorization': f'Bearer {bot_token}'}
    try:
        r1 = _rq.post('https://slack.com/api/files.getUploadURLExternal',
                       headers=headers,
                       data={'filename': filename, 'length': len(image_bytes)}, timeout=15)
        j1 = r1.json()
        if not j1.get('ok'):
            return False, f"getUploadURLExternal failed: {j1.get('error')}"
        upload_url, file_id = j1['upload_url'], j1['file_id']

        r2 = _rq.post(upload_url, files={'file': (filename, image_bytes, 'image/png')}, timeout=30)
        if r2.status_code != 200:
            return False, f'file upload failed: HTTP {r2.status_code}'

        r3 = _rq.post('https://slack.com/api/files.completeUploadExternal',
                       headers={**headers, 'Content-Type': 'application/json'},
                       json={'files': [{'id': file_id, 'title': title}],
                             'channel_id': channel, 'initial_comment': initial_comment},
                       timeout=15)
        j3 = r3.json()
        if not j3.get('ok'):
            return False, f"completeUploadExternal failed: {j3.get('error')}"
        return True, 'sent'
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
