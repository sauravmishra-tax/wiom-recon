"""
Slack notification helpers for WIOM GST Recon.
Sends messages ONLY to the channel configured in Settings (slack_channel) —
never to a hardcoded channel ID.
"""
import requests


def _token():
    from models import get_setting
    return get_setting('slack_bot_token', '')


def _channel():
    from models import get_setting
    return get_setting('slack_channel', '')


def _post(channel: str, text: str) -> bool:
    token = _token()
    if not token or not channel:
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
    """Send to the configured Slack channel (Settings -> slack_channel)."""
    return _post(_channel(), text)


def dm(user_id: str, text: str) -> bool:
    """Send a direct message to a Slack user."""
    return _post(user_id, text)


# ------------------------------------------------------------------
# Notification builders
# ------------------------------------------------------------------

def notify_pending_summary():
    """7 AM daily — pending approvals digest to the configured Slack channel."""
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
                f'>Open the portal to review: https://web-production-bf681c.up.railway.app/detail'
            )
