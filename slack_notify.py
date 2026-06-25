"""
Slack notification helpers for WIOM GST Recon.
Sends messages to #wiom-gst-recon channel.
"""
import requests

SLACK_CHANNEL = 'C03AGEGM9R8'
CFO_USER_ID  = 'U09G6616K8F'   # Akash Jain — CFO daily summary DM


def _token():
    from models import get_setting
    return get_setting('slack_bot_token', '')


def _post(channel: str, text: str) -> bool:
    token = _token()
    if not token:
        return False
    try:
        r = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'channel': channel, 'text': text}, timeout=8)
        return r.json().get('ok', False)
    except Exception:
        return False


def send(text: str, blocks=None) -> bool:
    """Send to #wiom-gst-recon channel."""
    return _post(SLACK_CHANNEL, text)


def dm(user_id: str, text: str) -> bool:
    """Send a direct message to a Slack user."""
    return _post(user_id, text)


# ------------------------------------------------------------------
# Notification builders
# ------------------------------------------------------------------

def notify_upload(state, period, total_rows, run_by):
    send(
        f':inbox_tray: *New Recon Uploaded*\n'
        f'>State: *{state}* | Period: *{period}*\n'
        f'>Total rows: *{total_rows}* | Uploaded by: *{run_by}*'
    )


def notify_action(action: str, row, done_by: str):
    """action: approved | rejected | remarked | resolved | reopened"""
    icons = {
        'approved':  ':white_check_mark:',
        'resolved':  ':heavy_check_mark:',
        'rejected':  ':no_entry_sign:',
        'remarked':  ':speech_balloon:',
        'reopened':  ':arrows_counterclockwise:',
    }
    icon = icons.get(action, ':bell:')
    inv = row.books_inv or row.gstn_inv or '—'
    vendor = row.vendor or row.gstin or '—'
    remark = row.team_remark or row.team_reason or ''
    remark_line = f'\n>Remark: _{remark}_' if remark else ''
    send(
        f'{icon} *Row {action.title()}*\n'
        f'>Invoice: *{inv}* | Vendor: {vendor}{remark_line}\n'
        f'>By: {done_by}'
    )


def notify_pending_summary():
    """7 AM daily — pending approvals digest to #wiom-gst-recon."""
    from app import app
    from models import ReconRow
    with app.app_context():
        pending = ReconRow.query.filter_by(status='remarked').count()
        if pending == 0:
            send(':tada: *No pending approvals* — all rows are up to date!')
        else:
            send(
                f':hourglass_flowing_sand: *Daily Pending Summary*\n'
                f'>*{pending}* row(s) marked by Book-Keeping team are awaiting Admin approval.\n'
                f'>Open the portal to review: http://localhost:5000/detail'
            )


def notify_cfo_summary():
    """7 PM daily — full dashboard summary DM to CFO (Akash Jain)."""
    from app import app
    from models import ReconRow
    from sqlalchemy import func
    with app.app_context():
        def cnt(f): return ReconRow.query.filter(f).count()
        def amt(f, col):
            return ReconRow.query.filter(f).with_entities(
                func.coalesce(func.sum(col), 0)).scalar() or 0

        total    = ReconRow.query.count()
        pending  = cnt(ReconRow.status == 'remarked')
        approved = cnt(ReconRow.status == 'approved')
        resolved = cnt(ReconRow.status == 'resolved')
        rejected = cnt(ReconRow.status == 'rejected')
        open_    = cnt(ReconRow.status == 'open')
        books    = cnt((ReconRow.category == 'books_only') & (ReconRow.status != 'rejected'))
        gstn     = cnt((ReconRow.category == 'gstn_only')  & (ReconRow.status != 'rejected'))
        matched  = cnt(ReconRow.category == 'matched')

        from datetime import date
        today = date.today().strftime('%d %b %Y')

        msg = (
            f':bar_chart: *WIOM GST Recon — Controller Summary* | _{today}_\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'>:memo: Total Rows: *{total}*\n'
            f'>:white_check_mark: Approved: *{approved}*   :heavy_check_mark: Resolved: *{resolved}*\n'
            f'>:speech_balloon: Pending Approval: *{pending}*\n'
            f'>:no_entry_sign: Rejected (ITC Ineligible): *{rejected}*\n'
            f'>:open_file_folder: Open / Unactioned: *{open_}*\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'>:books: Not in Books: *{books}*\n'
            f'>:receipt: Not in GSTR-2B: *{gstn}*\n'
            f'>:handshake: Matched: *{matched}*'
        )
        dm(CFO_USER_ID, msg)
