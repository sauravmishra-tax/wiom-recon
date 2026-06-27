"""
WIOM Zoho Agent -- Browser automation script (runs LOCALLY on your PC).

What it does:
  1. Opens Zoho Books in a browser (Chromium via Playwright)
  2. Logs in with your credentials
  3. Goes to GST Filing -> GSTR-2B Reconciliation
  4. Sets the period (from_month -> to_month)
  5. Exports as Excel and downloads it
  6. Uploads each state-slice to the WIOM GST Recon app

Usage:
  python zoho_agent.py --period 2026-04            # single month
  python zoho_agent.py --from 2026-04 --to 2026-06 # range
  python zoho_agent.py --period 2026-04 --state Delhi

Setup (one-time):
  pip install playwright
  playwright install chromium

Config:  edit CONFIG block below OR set env vars.
"""
import argparse
import json
import os
import sys
import time
import glob as _glob
import tempfile
from pathlib import Path

# Auto-load .env file from same directory
_env_file = Path(__file__).parent / '.env'
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# -----------------------------------------------------------------
# CONFIG  <- edit these or set as environment variables
# -----------------------------------------------------------------
ZOHO_EMAIL     = os.environ.get('ZOHO_EMAIL',    'saurav.mishra@wiom.in')
ZOHO_PASSWORD  = os.environ.get('ZOHO_PASSWORD', '')          # set via env for safety
ZOHO_ORG_ID    = os.environ.get('ZOHO_ORG_ID',  '60036724867')
ZOHO_BOOKS_URL = 'https://books.zoho.in'

# WIOM app -- change to localhost:5000 if running locally
WIOM_APP_URL   = os.environ.get('WIOM_APP_URL',  'https://web-production-bf681c.up.railway.app')
WIOM_EMAIL     = os.environ.get('WIOM_EMAIL',    'saurav.mishra@wiom.in')
WIOM_PASSWORD  = os.environ.get('WIOM_PASSWORD', 'WiomRecon@2026')

# States this org has GST registrations in (must match WIOM app names)
DEFAULT_STATES = ['Delhi', 'Haryana', 'Maharashtra', 'Uttar Pradesh']
# -----------------------------------------------------------------

RECON_URL_TEMPLATE = (
    '{base}/app/{org}#/gstfiling/tax/filings/reconciliation'
    '?from_date={from_date}&to_date={to_date}'
    '&tax_return_type=in_gstr2b_return'
)


def month_label(ym: str) -> str:
    """'2026-04' -> 'April 2026'"""
    import datetime
    y, m = ym.split('-')
    return datetime.date(int(y), int(m), 1).strftime('%B %Y')


def fmt_zoho_date(ym: str) -> str:
    """'2026-04' -> '04-2026' (Zoho URL format)"""
    y, m = ym.split('-')
    return f'{m}-{y}'


def run_agent(from_period: str, to_period: str, states: list, headless: bool = False):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("ERROR: playwright not installed.\n  Run:  pip install playwright && playwright install chromium")
        sys.exit(1)

    download_dir = Path(tempfile.mkdtemp(prefix='wiom_zoho_'))
    print(f"\n{'='*60}")
    print(f"  WIOM Zoho Agent")
    print(f"  Period  : {month_label(from_period)} to {month_label(to_period)}")
    print(f"  States  : {', '.join(states)}")
    print(f"  Download: {download_dir}")
    print(f"{'='*60}\n")

    if not ZOHO_PASSWORD:
        print("ERROR: ZOHO_PASSWORD not set. Set it via env var:\n  set ZOHO_PASSWORD=yourpassword")
        sys.exit(1)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            downloads_path=str(download_dir),
        )
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        # -- STEP 1: Login to Zoho ------------------------------
        print("[ 1/5 ] Logging into Zoho Books...")
        page.goto(f'{ZOHO_BOOKS_URL}/app/{ZOHO_ORG_ID}', timeout=60000)
        time.sleep(2)

        # Handle Zoho login page (may redirect to accounts.zoho.in)
        if 'accounts.zoho' in page.url or 'login' in page.url.lower():
            try:
                page.fill('#login_id', ZOHO_EMAIL, timeout=10000)
                page.click('#nextbtn', timeout=5000)
                time.sleep(1)
                page.fill('#password', ZOHO_PASSWORD, timeout=8000)
                page.click('#nextbtn', timeout=5000)
                print("       Waiting for login...")
                # Wait for either the app page OR an announcement/redirect page
                page.wait_for_url(f'**{ZOHO_ORG_ID}**', timeout=60000)
            except PWTimeout:
                # Try alternative selectors
                try:
                    page.fill('input[name="LOGIN_ID"]', ZOHO_EMAIL, timeout=5000)
                    page.press('input[name="LOGIN_ID"]', 'Enter')
                    time.sleep(1)
                    page.fill('input[name="PASSWORD"]', ZOHO_PASSWORD, timeout=5000)
                    page.press('input[name="PASSWORD"]', 'Enter')
                    page.wait_for_url(f'**{ZOHO_ORG_ID}**', timeout=60000)
                except Exception as e:
                    print(f"       Login issue -- browser is open, please log in manually.")
                    input("       Press Enter once you're logged into Zoho Books...")

        # Handle Zoho announcement/notice pages (timezone-update, etc.)
        if 'announcement' in page.url or 'accounts.zoho' in page.url:
            print("       Skipping Zoho announcement page...")
            recon_url_direct = RECON_URL_TEMPLATE.format(
                base=ZOHO_BOOKS_URL,
                org=ZOHO_ORG_ID,
                from_date=fmt_zoho_date(from_period),
                to_date=fmt_zoho_date(to_period),
            )
            page.goto(recon_url_direct, timeout=60000)
            time.sleep(3)

        print("       OK Logged in")

        # -- STEP 2: Navigate to GSTR-2B Reconciliation ---------
        print("[ 2/5 ] Opening GSTR-2B Reconciliation page...")
        recon_url = RECON_URL_TEMPLATE.format(
            base=ZOHO_BOOKS_URL,
            org=ZOHO_ORG_ID,
            from_date=fmt_zoho_date(from_period),
            to_date=fmt_zoho_date(to_period),
        )
        page.goto(recon_url, timeout=60000)
        time.sleep(3)

        # Wait for the reconciliation table to load
        try:
            page.wait_for_selector('.recon-table, table, [class*="reconcil"]', timeout=20000)
        except PWTimeout:
            print("       Page loaded (table selector not found -- continuing)")

        print("       OK Reconciliation page loaded")

        # -- STEP 3: Export as Excel ----------------------------
        print("[ 3/5 ] Exporting as Excel...")
        downloaded_files = []

        # Look for Export button
        export_selectors = [
            'button:has-text("Export")',
            '[aria-label*="Export"]',
            '.export-btn',
            'button:has-text("Download")',
            '[title*="Export"]',
        ]
        export_clicked = False
        for sel in export_selectors:
            try:
                page.click(sel, timeout=3000)
                export_clicked = True
                print(f"       Clicked: {sel}")
                break
            except PWTimeout:
                continue

        if not export_clicked:
            print("       Export button not auto-found.")
            print("       Please manually click 'Export as Excel' in the browser.")
            input("       Press Enter after the download starts...")

        # Wait for download
        try:
            with page.expect_download(timeout=30000) as dl_info:
                if not export_clicked:
                    # Try one more time with Excel option
                    try:
                        page.click('text=Excel', timeout=5000)
                    except PWTimeout:
                        pass
            dl = dl_info.value
            save_path = download_dir / dl.suggested_filename
            dl.save_as(str(save_path))
            downloaded_files.append(save_path)
            print(f"       OK Downloaded: {save_path.name}")
        except PWTimeout:
            # Check if file already downloaded
            existing = list(download_dir.glob('*.xlsx')) + list(download_dir.glob('*.xls'))
            if existing:
                downloaded_files = existing
                print(f"       OK Found downloaded file: {existing[0].name}")
            else:
                print("       Waiting for manual download...")
                input("       Download the Excel manually, then press Enter...")
                existing = list(download_dir.glob('*.xlsx')) + list(download_dir.glob('*.xls'))
                if existing:
                    downloaded_files = existing
                else:
                    # Ask for path
                    path_str = input("       Enter full path to the downloaded file: ").strip().strip('"')
                    if path_str:
                        downloaded_files = [Path(path_str)]

        browser.close()

    if not downloaded_files:
        print("ERROR: No Excel file found. Exiting.")
        sys.exit(1)

    # -- STEP 4: Login to WIOM app ------------------------------
    print("\n[ 4/5 ] Connecting to WIOM GST Recon app...")
    import requests as req

    session = req.Session()
    # Get CSRF token from login page
    r = session.get(f'{WIOM_APP_URL}/login')
    csrf = ''
    if 'csrf_token' in r.text:
        import re
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
        if m:
            csrf = m.group(1)

    login_data = {'email': WIOM_EMAIL, 'password': WIOM_PASSWORD}
    if csrf:
        login_data['csrf_token'] = csrf

    r = session.post(f'{WIOM_APP_URL}/login', data=login_data, allow_redirects=True)
    if 'logout' not in r.text.lower() and '/login' in r.url:
        print(f"ERROR: WIOM login failed. Check WIOM_EMAIL / WIOM_PASSWORD.")
        sys.exit(1)
    print("       OK Logged into WIOM app")

    # -- STEP 5: Upload each file -------------------------------
    print(f"\n[ 5/5 ] Uploading {len(downloaded_files)} file(s) to WIOM...")
    period_str = from_period  # use from_period as the run period

    results = []
    for fp in downloaded_files:
        for state in states:
            print(f"       Uploading -> State: {state}, Period: {period_str}, File: {fp.name}")
            with open(fp, 'rb') as fh:
                upload_r = session.post(
                    f'{WIOM_APP_URL}/upload',
                    data={'state': state, 'period': period_str, 'label': f'Zoho agent {period_str}'},
                    files={'file': (fp.name, fh, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
                    timeout=120,
                )
            try:
                resp = upload_r.json()
                if resp.get('error'):
                    print(f"       X {state}: {resp['error']}")
                else:
                    run_id = resp.get('run_id', '-')
                    print(f"       OK {state}: Run #{run_id} created")
                    results.append({'state': state, 'run_id': run_id})
            except Exception:
                print(f"       X {state}: Unexpected response -- {upload_r.text[:200]}")

    # -- Done ---------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  Done!  {len(results)} run(s) created.")
    for r2 in results:
        print(f"    {r2['state']} -> {WIOM_APP_URL}/detail-run_id={r2['run_id']}")
    print(f"{'='*60}\n")

    # Cleanup temp dir
    try:
        import shutil
        shutil.rmtree(download_dir, ignore_errors=True)
    except Exception:
        pass


# -- CLI ----------------------------------------------------------
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='WIOM Zoho Agent -- download GSTR-2B recon from Zoho Books and upload to WIOM app',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument('--period', help='Single month YYYY-MM (e.g. 2026-04)')
    parser.add_argument('--from',   dest='from_p', help='Start period YYYY-MM for range')
    parser.add_argument('--to',     dest='to_p',   help='End period YYYY-MM for range')
    parser.add_argument('--state',  nargs='+',      help='State(s) to upload for (default: all)')
    parser.add_argument('--headless', action='store_true', help='Run browser headless (no window)')
    args = parser.parse_args()

    if args.period:
        from_p = to_p = args.period
    elif args.from_p and args.to_p:
        from_p, to_p = args.from_p, args.to_p
    else:
        parser.print_help()
        print("\nExample:\n  python zoho_agent.py --period 2026-04\n  python zoho_agent.py --from 2026-04 --to 2026-06")
        sys.exit(1)

    states = args.state or DEFAULT_STATES
    run_agent(from_p, to_p, states, headless=args.headless)
