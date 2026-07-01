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


def render_cfo_summary_image(title, ctx, fmt='PNG'):
    """Render a full snapshot of the CFO Summary page (State Health,
    Reconciliation Summary, Financial KPIs, breakdown tables, Top-10 gaps)
    using Pillow — no headless browser needed. fmt='PNG' or 'PDF'. Returns bytes."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    W = 1000
    PAD = 28
    dot_col = {'red': (214, 31, 58), 'yellow': (217, 167, 15), 'green': (11, 157, 99), 'grey': (195, 195, 211)}
    risk_col = {'CRITICAL': (214, 31, 58), 'HIGH': (217, 112, 15), 'MEDIUM': (184, 134, 11), 'LOW': (11, 157, 99)}

    def font(size, bold=False):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    f_title = font(22, True); f_h3 = font(17, True); f_lbl = font(13, True)
    f_body = font(13); f_big = font(24, True); f_small = font(11)

    states = ctx.get('state_health', [])
    rows_of_states = (len(states) + 1) // 2
    health_h = 60 + rows_of_states * 96
    recon_h = 60 + 34 + 12 + 118 * 2
    kpi_h = 118
    breakdown = ctx.get('breakdown', {}) or {}
    by_reason = list(breakdown.get('by_reason', {}).items())
    by_status = list(breakdown.get('by_status', {}).items())
    top_gaps = (ctx.get('top_gaps') or [])[:10]

    def table_h(n_rows):
        return 44 + 30 + max(n_rows, 1) * 24 + 16

    reason_h = table_h(len(by_reason)) if by_reason else 0
    status_h = table_h(len(by_status))
    gaps_h = table_h(len(top_gaps))

    H = (90 + health_h + 20 + recon_h + 20 + kpi_h + 20
         + (reason_h + 20 if reason_h else 0) + status_h + 20 + gaps_h + 20 + 40)

    img = Image.new('RGB', (W, H), (240, 242, 247))
    d = ImageDraw.Draw(img)

    # header bar
    d.rectangle([0, 0, W, 70], fill=(26, 16, 35))
    d.text((PAD, 14), 'WIOM GST Recon', font=f_title, fill=(255, 255, 255))
    d.text((PAD, 42), 'Books x GST - Controller Summary', font=f_small, fill=(200, 170, 220))
    d.text((W - PAD, 24), title.split('(')[-1].rstrip(')'), font=f_body, fill=(255, 255, 255), anchor='ra')

    y = 90
    # ---- State Health card ----
    d.rounded_rectangle([PAD, y, W - PAD, y + health_h], radius=14, fill=(255, 255, 255), outline=(228, 231, 240))
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
    y += health_h + 20

    # ---- Reconciliation Summary card ----
    rs = ctx.get('recon_summary', {})
    total = ctx.get('total', 0) or 1
    d.rounded_rectangle([PAD, y, W - PAD, y + recon_h], radius=14, fill=(255, 255, 255), outline=(228, 231, 240))
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
    y += recon_h + 20

    # ---- Financial KPI row ----
    kpi_tiles = [
        (f"Rs.{ctx.get('itc_risk', 0):,}", 'ITC at Risk', 'Books != 2B tax exposure', (214, 31, 58)),
        (f"Rs.{ctx.get('excess_2b', 0):,}", 'Excess in GSTR-2B', 'In 2B, not booked', (34, 112, 216)),
        (f"{ctx.get('total', 0):,}", 'Total Invoice Rows', 'All states combined', (74, 74, 92)),
        (f"{ctx.get('done', 0)} / {ctx.get('pending', 0)}", 'Resolved / Pending', 'Approved + resolved', (11, 157, 99)),
    ]
    kw = (W - PAD * 2 - 14 * 3) / 4
    for i, (val, lbl, sub, col) in enumerate(kpi_tiles):
        tx = PAD + i * (kw + 14)
        d.rounded_rectangle([tx, y, tx + kw, y + kpi_h], radius=12, fill=(255, 255, 255), outline=(228, 231, 240))
        d.text((tx + 14, y + 16), val, font=f_big, fill=col)
        d.text((tx + 14, y + 52), lbl, font=f_small, fill=(106, 112, 128))
        d.text((tx + 14, y + 72), sub, font=f_small, fill=(170, 170, 170))
    y += kpi_h + 20

    def draw_table(y, title, headers, rows, col_ratios, row_colors=None):
        h = table_h(len(rows))
        d.rounded_rectangle([PAD, y, W - PAD, y + h], radius=12, fill=(255, 255, 255), outline=(228, 231, 240))
        d.text((PAD + 16, y + 14), title, font=f_lbl, fill=(21, 23, 31))
        tw_total = W - PAD * 2 - 32
        col_x = [PAD + 16]
        for r in col_ratios:
            col_x.append(col_x[-1] + tw_total * r)
        hy = y + 44
        for i, htext in enumerate(headers):
            d.text((col_x[i], hy), htext, font=f_small, fill=(106, 112, 128))
        d.line([PAD + 16, hy + 18, W - PAD - 16, hy + 18], fill=(228, 231, 240))
        ry = hy + 24
        if not rows:
            d.text((col_x[0], ry), 'No data', font=f_small, fill=(170, 170, 170))
        for row in rows:
            for i, cell in enumerate(row):
                text, color = cell if isinstance(cell, tuple) else (cell, (42, 42, 58))
                d.text((col_x[i], ry), str(text), font=f_small, fill=color)
            ry += 24
        return y + h + 20

    if by_reason:
        rows = [((rsn, (21, 23, 31)), (f"{v['count']:,}", (42, 42, 58)), (f"Rs.{v['value']:,}", (214, 31, 58)))
                for rsn, v in by_reason]
        y = draw_table(y, 'By Reason (team-tagged)', ['Reason', 'Invoices', 'Amount'], rows, [0.55, 0.2, 0.25])

    rows = [((st.title(), (21, 23, 31)), (f"{v['count']:,}", (42, 42, 58)), (f"Rs.{v['value']:,}", (42, 42, 58)))
            for st, v in by_status]
    y = draw_table(y, 'By Workflow Status', ['Status', 'Invoices', 'Amount'], rows, [0.55, 0.2, 0.25])

    rows = []
    for g in top_gaps:
        rows.append((
            (g.get('gstin', ''), (21, 23, 31)),
            ((g.get('vendor') or '')[:22], (74, 74, 92)),
            (g.get('state', ''), (74, 74, 92)),
            (f"Rs.{int(g.get('total_gap', 0)):,}", (214, 31, 58) if (g.get('total_gap') or 0) < 0 else (11, 157, 99)),
            (g.get('risk', ''), risk_col.get(g.get('risk'), (74, 74, 92))),
        ))
    y = draw_table(y, 'Top 10 GSTINs by Gap', ['GSTIN', 'Vendor', 'State', 'Total Gap', 'Risk'],
                    rows, [0.22, 0.28, 0.15, 0.2, 0.15])

    d.text((W / 2, y + 6), 'WIOM GST Recon - Books vs GST Reconciliation Intelligence - Confidential',
            font=f_small, fill=(170, 170, 170), anchor='ma')

    buf = io.BytesIO()
    if fmt == 'PDF':
        img.save(buf, format='PDF', resolution=150.0)
    else:
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
