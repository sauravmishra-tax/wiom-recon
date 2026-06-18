"""
WIOM Zoho Books vs GST Recon — Multi-user workflow platform
=============================================================
- 9 AI sub-agents produce the reconciliation (unchanged engine).
- Results are stored monthly in a database, auto-split state-wise.
- Accounts team logs in and writes remarks/reasons on their state's rows.
- Manager (admin) approves/resolves; every change is audit-logged (who + when).
- Local-first (SQLite) and cloud-portable (set DATABASE_URL to switch).
"""
import os
import sys
import json
import time
import threading
from datetime import datetime

from flask import (Flask, render_template, request, jsonify, send_file,
                   redirect, url_for, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import func

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import Config
from models import (db, User, ReconRun, ReconRow, VendorMaster, AuditLog,
                    Setting, RowComment, LoginEvent, log_audit, now_ist,
                    get_setting, set_setting)
from auth import login_manager, auth_bp, admin_required, superadmin_required
from persist import persist_run, derive_period, derive_period_from_file
from state_codes import STATE_CODES, WIOM_STATES
import zoho

from agents import agent_1_validator
from agents import agent_2_vendor_resolver
from agents import agent_3_cross_matcher
from agents import agent_4_itc_analyzer
from agents import agent_5_anomaly_detector
from agents import agent_6_report_builder
from agents import agent_7_schema_guard
from agents import agent_8_qa_reviewer
from agents import agent_9_scrutiny


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object(Config)
    app.config['TEMPLATES_AUTO_RELOAD'] = True  # reflect template edits without restart

    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(auth_bp)

    with app.app_context():
        db.create_all()
        _seed_admin(app)

    _start_scheduler(app)
    return app


def _start_scheduler(app):
    """Monthly CFO-summary email (feature 7). Best-effort: only fires while the
    app is running. Sends on the configured day-of-month at 09:00 IST."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("  [scheduler] apscheduler not installed — monthly CFO email disabled (manual send still works).")
        return

    def monthly_cfo():
        with app.app_context():
            try:
                day = int(get_setting('cfo_send_day', '1') or '1')
            except ValueError:
                day = 1
            if now_ist().day == day and _smtp_configured() and get_setting('cfo_email'):
                ok, msg = _send_cfo_email()
                print(f"  [scheduler] Monthly CFO email: {ok} — {msg}")

    def daily_slack():
        with app.app_context():
            day = get_setting('slack_send_day', '*') or '*'   # '*' = every day, or a day-of-month
            if not get_setting('slack_webhook'):
                return
            if day == '*' or str(now_ist().day) == str(day):
                ok, msg = _send_slack_report()
                print(f"  [scheduler] Daily Slack report: {ok} — {msg}")

    sched = BackgroundScheduler(daemon=True)
    sched.add_job(monthly_cfo, 'cron', hour=9, minute=0, id='monthly_cfo')
    sched.add_job(daily_slack, 'cron', hour=9, minute=30, id='daily_slack')
    sched.start()
    return app


def _seed_admin(app):
    """Create the first manager account if no users exist."""
    if User.query.count() == 0:
        admin = User(
            name=app.config['ADMIN_NAME'],
            email=app.config['ADMIN_EMAIL'].lower(),
            role='superadmin', title='Platform Owner',
        )
        admin.set_password(app.config['ADMIN_PASSWORD'])
        db.session.add(admin)
        db.session.commit()
        print(f"  [seed] Admin created: {admin.email} / {app.config['ADMIN_PASSWORD']}")


app = create_app()

# ----------------------------------------------------------------------
# LIVE PROCESSING STATE (one upload at a time)
# ----------------------------------------------------------------------
processing_state = {
    'active': False, 'progress': 0, 'current_agent': '',
    'logs': [], 'agent_results': {}, 'run_id': None,
    'output_file': None, 'error': None, 'start_time': None,
}


def reset_state():
    processing_state.update({
        'active': False, 'progress': 0, 'current_agent': '',
        'logs': [], 'agent_results': {}, 'run_id': None,
        'output_file': None, 'error': None, 'start_time': None,
    })


def add_log(agent_id, msg):
    names = {0: 'SYSTEM', 1: 'Validator', 2: 'Vendor Resolver', 3: 'Cross-Matcher',
             4: 'ITC Analyzer', 5: 'Anomaly Detector', 6: 'Report Builder',
             7: 'Schema Guard', 8: 'QA Reviewer', 9: 'Scrutiny'}
    processing_state['logs'].append({
        'time': datetime.now().strftime('%H:%M:%S'),
        'agent': names.get(agent_id, f'Agent {agent_id}'),
        'agent_id': agent_id, 'msg': msg,
    })


# ----------------------------------------------------------------------
# ENGINE ORCHESTRATOR (9 agents) — runs in a background thread
# ----------------------------------------------------------------------
def run_reconciliation(file_path, period, label, user_id, run_state=None):
    with app.app_context():
        try:
            processing_state['active'] = True
            processing_state['start_time'] = time.time()

            processing_state['current_agent'] = 'Agent 1: Data Validator'
            processing_state['progress'] = 5
            add_log(0, '═══ AGENT 1: DATA VALIDATOR ═══')
            r1 = agent_1_validator.run(file_path, log_fn=add_log)
            processing_state['agent_results']['validator'] = {
                'status': r1['status'], 'checks': r1['checks'],
                'warnings': r1['warnings'], 'errors': r1['errors'], 'stats': r1['stats']}
            if r1['status'] == 'failed':
                processing_state['error'] = f"Validation failed: {r1['errors']}"
                processing_state['active'] = False
                return
            processing_state['progress'] = 15

            processing_state['current_agent'] = 'Agent 2: Vendor Resolver'
            processing_state['progress'] = 20
            add_log(0, '═══ AGENT 2: VENDOR RESOLVER ═══')
            r2 = agent_2_vendor_resolver.run(r1['data'], log_fn=add_log)
            processing_state['agent_results']['vendor_resolver'] = {
                'status': r2['status'], 'checks': r2['checks'], 'stats': r2['stats']}
            processing_state['progress'] = 35

            processing_state['current_agent'] = 'Agent 3: Cross-Match Engine'
            processing_state['progress'] = 40
            add_log(0, '═══ AGENT 3: CROSS-MATCH ENGINE ═══')
            r3 = agent_3_cross_matcher.run(r2['data'], r2['vendor_map'], log_fn=add_log)
            processing_state['agent_results']['cross_matcher'] = {
                'status': r3['status'], 'checks': r3['checks'], 'stats': r3['stats']}
            processing_state['progress'] = 55

            # Period comes from the user's month pick at upload. Only auto-detect
            # (from invoice dates) as a fallback if none was provided.
            if not period:
                period = derive_period(r3['inv_matched'], r3['books_unmatched'],
                                       r3['gstn_unmatched'])
                add_log(0, f'Auto-detected period from invoice dates: {period}')
            else:
                add_log(0, f'Reconciliation period (selected): {period}')

            processing_state['current_agent'] = 'Agent 4: ITC Risk Analyzer'
            processing_state['progress'] = 60
            add_log(0, '═══ AGENT 4: ITC RISK ANALYZER ═══')
            r4 = agent_4_itc_analyzer.run(r2['data'], r3['inv_matched'],
                                          r3['books_unmatched'], r3['gstn_unmatched'], log_fn=add_log)
            processing_state['agent_results']['itc_analyzer'] = {
                'status': r4['status'], 'checks': r4['checks'], 'stats': r4['stats'],
                'itc_summary': r4['itc_summary']}
            processing_state['progress'] = 70

            processing_state['current_agent'] = 'Agent 5: Anomaly Detector'
            processing_state['progress'] = 75
            add_log(0, '═══ AGENT 5: ANOMALY DETECTOR ═══')
            r5 = agent_5_anomaly_detector.run(r2['data'], r3['inv_matched'],
                                              r3['books_unmatched'], r3['gstn_unmatched'], log_fn=add_log)
            processing_state['agent_results']['anomaly_detector'] = {
                'status': r5['status'], 'checks': r5['checks'], 'stats': r5['stats'],
                'anomalies': [{'type': a['type'], 'severity': a['severity'], 'detail': a['detail']}
                              for a in r5['anomalies']]}
            processing_state['progress'] = 85

            processing_state['current_agent'] = 'Agent 6: Report Builder'
            processing_state['progress'] = 88
            add_log(0, '═══ AGENT 6: REPORT BUILDER ═══')
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(app.config['OUTPUT_FOLDER'],
                                       f'WIOM_GST_Recon_{period}_{ts}.xlsx')
            r6 = agent_6_report_builder.run(
                input_file=file_path, output_file=output_file,
                dataframes=r2['data'], vendor_map=r2['vendor_map'],
                gst_cache=r2.get('gst_cache', {}), all_gstins=r2.get('all_gstins', []),
                inv_matched=r3['inv_matched'], books_unmatched=r3['books_unmatched'],
                gstn_unmatched=r3['gstn_unmatched'],
                books_gstins=r3.get('books_gstins', set()),
                gstn_gstins=r3.get('gstn_gstins', set()),
                anomalies=r5.get('anomalies', []), patterns=r5.get('patterns', []),
                itc_summary=r4.get('itc_summary', {}), log_fn=add_log)
            processing_state['agent_results']['report_builder'] = {
                'status': r6['status'], 'checks': r6['checks'], 'stats': r6['stats']}
            processing_state['progress'] = 90

            processing_state['current_agent'] = 'Agent 7: Schema Guard'
            add_log(0, '═══ AGENT 7: SCHEMA GUARD ═══')
            r7 = agent_7_schema_guard.run(output_file, log_fn=add_log)
            processing_state['agent_results']['schema_guard'] = {
                'status': r7['status'], 'checks': r7['checks'], 'stats': r7['stats']}
            processing_state['progress'] = 93

            processing_state['current_agent'] = 'Agent 8: QA Reviewer'
            add_log(0, '═══ AGENT 8: QA REVIEWER ═══')
            r8 = agent_8_qa_reviewer.run(
                agent_results=processing_state['agent_results'], dataframes=r2['data'],
                inv_matched=r3['inv_matched'], books_unmatched=r3['books_unmatched'],
                gstn_unmatched=r3['gstn_unmatched'], vendor_map=r2['vendor_map'],
                itc_summary=r4.get('itc_summary', {}), log_fn=add_log)
            processing_state['agent_results']['qa_reviewer'] = {
                'status': r8['status'], 'checks': r8['checks'], 'stats': r8['stats']}
            processing_state['progress'] = 96

            processing_state['current_agent'] = 'Agent 9: Scrutiny'
            add_log(0, '═══ AGENT 9: SCRUTINY & INTER-RECON ═══')
            r9 = agent_9_scrutiny.run(
                output_file=output_file, dataframes=r2['data'],
                inv_matched=r3['inv_matched'], books_unmatched=r3['books_unmatched'],
                gstn_unmatched=r3['gstn_unmatched'], log_fn=add_log)
            processing_state['agent_results']['scrutiny'] = {
                'status': r9['status'], 'checks': r9['checks'], 'stats': r9['stats']}

            # ---- Persist into DB ----
            add_log(0, 'Storing results into database for team review...')
            run = ReconRun(
                period=period, state=run_state or '', label=label,
                source_filename=os.path.basename(file_path),
                output_file=output_file, uploaded_by_id=user_id,
                summary_json=json.dumps({
                    'matched': len(r3['inv_matched']),
                    'books_only': len(r3['books_unmatched']),
                    'gstn_only': len(r3['gstn_unmatched']),
                    'itc_summary': r4.get('itc_summary', {}),
                    'qa_verdict': r8['stats'].get('verdict', 'N/A'),
                }))
            db.session.add(run)
            db.session.commit()
            reconciled_dfs = [r2['data'].get('Reconciled'), r2['data'].get('Matched')]
            reconciled_dfs = [d for d in reconciled_dfs if d is not None]
            n, carried = persist_run(run, r3['inv_matched'], r3['books_unmatched'],
                            r3['gstn_unmatched'], vendor_map=r2['vendor_map'],
                            gst_cache=r2.get('gst_cache', {}), run_state=run_state,
                            reconciled_dfs=reconciled_dfs)
            if carried:
                add_log(0, f'Smart re-upload: replaced previous {run_state} {period} snapshot, '
                           f'carried over remarks/status on {carried} matching invoices')

            # Auto-sync vendor master from Zoho if configured (best-effort)
            if _zoho_configured():
                try:
                    res = sync_zoho_master()
                    if res.get('ok'):
                        add_log(0, f"Zoho auto-sync: {res['count']} vendors, {res['updated_rows']} names updated")
                except Exception as e:
                    add_log(0, f"Zoho auto-sync skipped: {e}")

            processing_state['run_id'] = run.id
            processing_state['output_file'] = output_file
            processing_state['progress'] = 100
            elapsed = time.time() - processing_state['start_time']
            add_log(0, f'═══ COMPLETE in {elapsed:.1f}s | {n} rows stored | '
                       f"QA: {r8['stats'].get('verdict','N/A')} ═══")

        except Exception as e:
            processing_state['error'] = str(e)
            add_log(0, f'ERROR: {e}')
            import traceback
            add_log(0, traceback.format_exc())
        finally:
            processing_state['active'] = False


# ======================================================================
# PAGE ROUTES
# ======================================================================
@app.route('/')
@login_required
def dashboard():
    periods = [p[0] for p in db.session.query(ReconRow.period)
               .distinct().order_by(ReconRow.period.desc()).all()]
    runs = ReconRun.query.order_by(ReconRun.created_at.desc()).limit(12).all()
    stats = _summary_stats(current_user)
    return render_template('dashboard.html', periods=periods, runs=runs,
                           stats=stats, states=WIOM_STATES)


def _distinct_periods():
    return [p[0] for p in db.session.query(ReconRow.period)
            .distinct().order_by(ReconRow.period.desc()).all()]


def _fy_list(periods):
    """Distinct Indian financial years (start year) from 'YYYY-MM' periods."""
    fys = set()
    for p in periods:
        try:
            y, m = int(p[:4]), int(p[5:7])
            fys.add(y if m >= 4 else y - 1)
        except (ValueError, IndexError):
            continue
    return sorted(fys, reverse=True)


@app.route('/detail')
@login_required
def detail():
    periods = _distinct_periods()
    fys = _fy_list(periods)
    now = now_ist()
    current_fy = now.year if now.month >= 4 else now.year - 1   # India FY (Apr–Mar)
    if current_fy not in fys:
        fys = sorted(set(fys) | {current_fy}, reverse=True)
    return render_template('detail.html', periods=periods, fys=fys,
                           default_fy=current_fy, states=WIOM_STATES,
                           is_admin=current_user.is_admin)


@app.route('/upload-page')
@admin_required
def upload_page():
    return render_template('upload.html')


# ======================================================================
# SETTINGS + ZOHO LIVE INTEGRATION
# ======================================================================
def _zoho_creds():
    return {
        'client_id': get_setting('zoho_client_id'),
        'client_secret': get_setting('zoho_client_secret'),
        'refresh_token': get_setting('zoho_refresh_token'),
        'org_id': get_setting('zoho_org_id'),
        'region': get_setting('zoho_region', 'in'),
    }


def _zoho_configured():
    c = _zoho_creds()
    return all([c['client_id'], c['client_secret'], c['refresh_token'], c['org_id']])


def sync_zoho_master():
    """Fetch vendors from Zoho Books -> upsert VendorMaster + backfill row names.
    Returns dict {ok, count, updated_rows, error}."""
    c = _zoho_creds()
    try:
        tok = zoho.get_access_token(c['client_id'], c['client_secret'],
                                    c['refresh_token'], c['region'])
        vendors = zoho.fetch_vendors(tok, c['org_id'], c['region'])
    except Exception as e:
        set_setting('zoho_last_status', f'error: {e}')
        db.session.commit()
        return {'ok': False, 'error': str(e)}

    from state_codes import state_from_gstin
    updated_rows = 0
    for v in vendors:
        g = v['gstin'].strip()
        if not g:
            continue
        code, state = state_from_gstin(g)
        vm = db.session.get(VendorMaster, g)
        if vm is None:
            vm = VendorMaster(gstin=g)
            db.session.add(vm)
        if v['name']:
            vm.name = v['name']
        if v.get('email'):
            vm.email = v['email']
        vm.state_code, vm.state_name, vm.source = code, state, 'zoho_api'
        # backfill rows whose vendor is blank or equals the GSTIN (unknown)
        if v['name']:
            rows = ReconRow.query.filter(ReconRow.gstin == g,
                                         (ReconRow.vendor == None) | (ReconRow.vendor == '') |
                                         (ReconRow.vendor == g)).all()
            for r in rows:
                r.vendor = v['name']; updated_rows += 1
    set_setting('zoho_last_sync', now_ist().strftime('%d-%b-%Y %H:%M'))
    set_setting('zoho_last_status', f'ok: {len(vendors)} vendors')
    db.session.commit()
    return {'ok': True, 'count': len(vendors), 'updated_rows': updated_rows}


@app.route('/settings')
@superadmin_required
def settings_page():
    c = _zoho_creds()
    return render_template('settings.html',
        zoho_client_id=c['client_id'], zoho_org_id=c['org_id'],
        zoho_region=c['region'] or 'in',
        has_secret=bool(c['client_secret']), has_refresh=bool(c['refresh_token']),
        configured=_zoho_configured(),
        last_sync=get_setting('zoho_last_sync', '—'),
        last_status=get_setting('zoho_last_status', '—'),
        vendor_count=VendorMaster.query.count(),
        zoho_count=VendorMaster.query.filter_by(source='zoho_api').count(),
        # SMTP / email
        smtp_host=get_setting('smtp_host'), smtp_port=get_setting('smtp_port', '587'),
        smtp_user=get_setting('smtp_user'), smtp_from=get_setting('smtp_from'),
        smtp_tls=get_setting('smtp_tls', '1') != '0', smtp_configured=_smtp_configured(),
        has_smtp_pw=bool(get_setting('smtp_password')),
        cfo_email=get_setting('cfo_email'), cfo_send_day=get_setting('cfo_send_day', '1'),
        # Slack
        slack_configured=bool(get_setting('slack_webhook')),
        slack_send_day=get_setting('slack_send_day', '*'),
        # GSP / GSTR-2B source
        gsp_provider=get_setting('gsp_provider'), gsp_configured=bool(get_setting('gsp_provider')))


@app.route('/settings/zoho', methods=['POST'])
@superadmin_required
def save_zoho():
    set_setting('zoho_client_id', request.form.get('client_id', '').strip())
    set_setting('zoho_org_id', request.form.get('org_id', '').strip())
    set_setting('zoho_region', request.form.get('region', 'in').strip().lstrip('.'))
    # secrets: only overwrite when a new value is supplied (blank = keep existing)
    sec = request.form.get('client_secret', '').strip()
    ref = request.form.get('refresh_token', '').strip()
    if sec:
        set_setting('zoho_client_secret', sec)
    if ref:
        set_setting('zoho_refresh_token', ref)
    db.session.commit()
    from flask import flash
    flash('Zoho settings saved.', 'success')
    return redirect(url_for('settings_page'))


@app.route('/settings/zoho/test', methods=['POST'])
@superadmin_required
def test_zoho():
    if not _zoho_configured():
        return jsonify({'ok': False, 'error': 'Enter all credentials first.'})
    c = _zoho_creds()
    ok, msg = zoho.test_connection(c['client_id'], c['client_secret'],
                                   c['refresh_token'], c['org_id'], c['region'])
    return jsonify({'ok': ok, 'message': msg})


@app.route('/settings/zoho/sync', methods=['POST'])
@superadmin_required
def sync_zoho():
    if not _zoho_configured():
        return jsonify({'ok': False, 'error': 'Enter all credentials first.'})
    return jsonify(sync_zoho_master())


# ======================================================================
# EMAIL (SMTP) + scheduled CFO summary  (features 2 & 7)
# ======================================================================
def _smtp_cfg():
    return {
        'host': get_setting('smtp_host'), 'port': get_setting('smtp_port', '587'),
        'user': get_setting('smtp_user'), 'password': get_setting('smtp_password'),
        'from_addr': get_setting('smtp_from') or get_setting('smtp_user'),
        'use_tls': get_setting('smtp_tls', '1'),
    }


def _smtp_configured():
    return bool(get_setting('smtp_host') and get_setting('smtp_user'))


@app.route('/settings/smtp', methods=['POST'])
@superadmin_required
def save_smtp():
    set_setting('smtp_host', request.form.get('host', '').strip())
    set_setting('smtp_port', request.form.get('port', '587').strip())
    set_setting('smtp_user', request.form.get('user', '').strip())
    set_setting('smtp_from', request.form.get('from_addr', '').strip())
    set_setting('smtp_tls', '1' if request.form.get('use_tls') == 'on' else '0')
    pw = request.form.get('password', '').strip()
    if pw:
        set_setting('smtp_password', pw)
    set_setting('cfo_email', request.form.get('cfo_email', '').strip())
    set_setting('cfo_send_day', request.form.get('cfo_send_day', '1').strip())
    db.session.commit()
    from flask import flash
    flash('Email settings saved.', 'success')
    return redirect(url_for('settings_page'))


@app.route('/settings/smtp/test', methods=['POST'])
@superadmin_required
def test_smtp_route():
    import email_util
    ok, msg = email_util.test_smtp(_smtp_cfg())
    return jsonify({'ok': ok, 'message': msg})


def _send_cfo_email(recipient=None):
    """Email the CFO summary (HTML body + cumulative Excel attached). Returns (ok,msg)."""
    import email_util, export_excel
    to = recipient or get_setting('cfo_email')
    if not to:
        return False, 'No CFO recipient configured.'
    if not _smtp_configured():
        return False, 'SMTP not configured.'
    rows = ReconRow.query.order_by(ReconRow.state_name, ReconRow.period).all()
    ctx = _cfo_context(rows, 'all')
    gap = gap_from_rows(rows)
    xls = export_excel.build_cumulative_excel(rows, gap, 'All states · cumulative')
    body = render_template('cfo_email.html', **ctx)
    return email_util.send_email(_smtp_cfg(), to,
        f"WIOM Recon — CFO Summary ({ctx['generated']})", body,
        attachment=xls.read(), attachment_name=f"WIOM_Cumulative_{now_ist().strftime('%Y%m%d')}.xlsx")


@app.route('/cfo-email/send', methods=['POST'])
@admin_required
def send_cfo_email():
    ok, msg = _send_cfo_email(request.form.get('to') or None)
    return jsonify({'ok': ok, 'message': msg})


# ======================================================================
# SLACK daily status report
# ======================================================================
@app.route('/settings/slack', methods=['POST'])
@superadmin_required
def save_slack():
    set_setting('slack_send_day', request.form.get('slack_send_day', '*').strip() or '*')
    url = request.form.get('webhook', '').strip()
    if url:
        set_setting('slack_webhook', url)
    if request.form.get('clear_webhook') == 'on':
        set_setting('slack_webhook', '')
    db.session.commit()
    from flask import flash
    flash('Slack settings saved.', 'success')
    return redirect(url_for('settings_page'))


def _send_slack_report():
    """Build + post the daily recon status report to Slack. Returns (ok, msg)."""
    import slack_util
    url = get_setting('slack_webhook')
    if not url:
        return False, 'Slack webhook not configured.'
    rows = ReconRow.query.all()
    stats = _summary_stats_all()
    bd = _breakdown_data(rows)
    gap = gap_from_rows(rows)
    # state-wise gap
    st = {}
    for r in rows:
        s = st.setdefault(r.state_name, {'c': 0, 'v': 0.0})
        s['c'] += 1; s['v'] += r.total_diff or 0
    by_state = [(k, v['c'], v['v']) for k, v in sorted(st.items())]
    by_reason = [(k, v['count'], v['value']) for k, v in list(bd['by_reason'].items())[:6]]
    worst = sorted([g for g in gap if g['b_cnt'] and not g['g_cnt'] or g['risk'] in ('CRITICAL', 'HIGH')],
                   key=lambda g: -g['total_gap'])[:5]
    top_vendors = [(g['gstin'], (g['vendor'] or '')[:28], g['risk'],
                    (g['b_tax'] if g['b_cnt'] and not g['g_cnt'] else abs(g['total_gap'])))
                   for g in worst]
    kpis = [
        ('ITC at risk', f"₹{stats['itc_risk']:,}"),
        ('Total rows', f"{stats['total']:,}"),
        ('Open / Remarked / Done', f"{stats['open']} / {stats['remarked']} / {stats['done']}"),
    ]
    blocks = slack_util.build_report_blocks(
        f"WIOM Recon — Daily Status ({now_ist().strftime('%d-%b-%Y')})",
        kpis, by_state, by_reason, top_vendors)
    text = f"WIOM Recon daily: ITC at risk ₹{stats['itc_risk']:,}, {stats['open']} open, {stats['done']} resolved"
    return slack_util.post_message(url, text, blocks)


@app.route('/slack/send', methods=['POST'])
@admin_required
def slack_send():
    ok, msg = _send_slack_report()
    return jsonify({'ok': ok, 'message': msg})


def _summary_stats_all():
    """Org-wide stats (no user state restriction) for scheduled reports."""
    q = ReconRow.query
    itc = sum((r.books_igst or 0) + (r.books_cgst or 0) + (r.books_sgst or 0)
              for r in q.filter(ReconRow.category == 'books_only').all())
    return {
        'total': q.count(),
        'open': q.filter(ReconRow.status == 'open').count(),
        'remarked': q.filter(ReconRow.status == 'remarked').count(),
        'done': q.filter(ReconRow.status.in_(['approved', 'resolved'])).count(),
        'itc_risk': round(itc),
    }


# ======================================================================
# AUDIT LOG
# ======================================================================
@app.route('/audit')
@admin_required
def audit_page():
    periods = [p[0] for p in db.session.query(ReconRow.period)
               .distinct().order_by(ReconRow.period.desc()).all()]
    return render_template('audit.html', periods=periods, states=WIOM_STATES)


@app.route('/api/audit')
@admin_required
def api_audit():
    action = request.args.get('action')
    state = request.args.get('state')
    search = request.args.get('q', '').strip()
    # join audit -> row for GSTIN/state context
    q = db.session.query(AuditLog, ReconRow).join(
        ReconRow, AuditLog.row_id == ReconRow.id)
    if action and action != 'all':
        q = q.filter(AuditLog.action == action)
    if state and state != 'all':
        q = q.filter(ReconRow.state_name == state)
    if search:
        like = f'%{search}%'
        q = q.filter(ReconRow.gstin.like(like) | ReconRow.vendor.like(like) |
                     AuditLog.user_name.like(like))
    rows = q.order_by(AuditLog.created_at.desc()).limit(1000).all()
    return jsonify([{
        'at': l.created_at.strftime('%d-%b-%Y %H:%M'),
        'user': l.user_name, 'action': l.action,
        'gstin': r.gstin, 'vendor': r.vendor, 'state': r.state_name,
        'period': r.period, 'new': l.new_value, 'old': l.old_value,
    } for l, r in rows])


# ======================================================================
# UPLOAD + ENGINE
# ======================================================================
@app.route('/upload', methods=['POST'])
@admin_required
def upload():
    if processing_state['active']:
        return jsonify({'error': 'A reconciliation is already running.'}), 400

    label = request.form.get('label', '').strip()
    run_state = request.form.get('state', '').strip()
    period = request.form.get('period', '').strip()  # 'YYYY-MM' from the month picker
    if run_state not in WIOM_STATES:
        return jsonify({'error': 'Select a valid state (Delhi / Haryana / Maharashtra / Uttar Pradesh).'}), 400
    import re as _re
    if not _re.match(r'^\d{4}-\d{2}$', period):
        return jsonify({'error': 'Select the return month (period).'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Only .xlsx/.xls files accepted'}), 400

    filename = secure_filename(file.filename)
    ts = now_ist().strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'{ts}_{filename}')
    file.save(filepath)

    reset_state()
    add_log(0, f'File uploaded for {run_state} ({period}): {filename}')
    threading.Thread(target=run_reconciliation,
                     args=(filepath, period, label, current_user.id, run_state),
                     daemon=True).start()
    return jsonify({'status': 'started', 'filename': filename})


@app.route('/status')
@login_required
def status():
    return jsonify({
        'active': processing_state['active'], 'progress': processing_state['progress'],
        'current_agent': processing_state['current_agent'],
        'logs': processing_state['logs'][-60:],
        'agent_results': processing_state['agent_results'],
        'run_id': processing_state['run_id'],
        'output_file': processing_state['output_file'],
        'error': processing_state['error'],
        'elapsed': round(time.time() - processing_state['start_time'], 1)
                   if processing_state['start_time'] else 0,
    })


@app.route('/download')
@login_required
def download():
    run_id = request.args.get('run_id', type=int)
    if run_id:
        run = db.session.get(ReconRun, run_id)
        path = run.output_file if run else None
    else:
        path = processing_state['output_file']
    if path and os.path.exists(path):
        return send_file(path, as_attachment=True)
    return jsonify({'error': 'No output file available'}), 404


# ======================================================================
# WORKFLOW API
# ======================================================================
@app.route('/api/rows')
@login_required
def api_rows():
    q = ReconRow.query
    period = request.args.get('period')
    state = request.args.get('state')
    category = request.args.get('category')
    wf_status = request.args.get('status')
    search = request.args.get('q', '').strip()
    only_mismatch = request.args.get('mismatch') == '1'

    if period and period != 'all':
        q = q.filter(ReconRow.period == period)
    if state and state != 'all':
        q = q.filter(ReconRow.state_name == state)
    if category and category != 'all':
        q = q.filter(ReconRow.category == category)
    if request.args.get('recon') == 'fully':
        q = q.filter(ReconRow.category == 'matched',
                     ReconRow.recon_status.like('%Fully Reconciled%'))
    if request.args.get('recon') == 'cross':
        # engine cross-matches only — exclude Zoho-auto / exact fully-reconciled
        q = q.filter(ReconRow.category == 'matched',
                     ~ReconRow.recon_status.like('%Fully Reconciled%'))
    if wf_status and wf_status != 'all':
        q = q.filter(ReconRow.status == wf_status)
    q = _apply_fy(q, request.args.get('fy'))
    if only_mismatch:
        # everything except cleanly matched + fully reconciled rows
        q = q.filter(~((ReconRow.category == 'matched') &
                       (ReconRow.recon_status.like('%Fully Reconciled%'))))
    # My Queue: Book-Keeping → rows assigned to me (or, if none assigned, my-state pending);
    #           Admin+ → rows pending approval (remarked).
    if request.args.get('my') == '1':
        if current_user.is_admin:
            q = q.filter(ReconRow.status == 'remarked')
        else:
            assigned_n = ReconRow.query.filter_by(assigned_to_id=current_user.id).count()
            if assigned_n:
                q = q.filter(ReconRow.assigned_to_id == current_user.id)
            else:
                q = q.filter(ReconRow.status.in_(['open', 'remarked']),
                             ReconRow.category != 'matched')
    if search:
        like = f'%{search}%'
        q = q.filter(ReconRow.gstin.like(like) | ReconRow.vendor.like(like) |
                     ReconRow.books_inv.like(like) | ReconRow.gstn_inv.like(like))

    # Book-Keeping users only see their assigned states (blank = all)
    if not current_user.is_admin and current_user.state_list():
        q = q.filter(ReconRow.state_name.in_(current_user.state_list()))

    rows = q.order_by(ReconRow.state_name, ReconRow.gstin).limit(2000).all()
    return jsonify({'rows': [r.to_dict() for r in rows], 'count': len(rows)})


def _apply_fy(q, fy):
    """Filter to an Indian financial year (Apr YYYY – Mar YYYY+1). fy = 'YYYY'."""
    if not fy or fy == 'all':
        return q
    try:
        y = int(fy)
    except (TypeError, ValueError):
        return q
    return q.filter(ReconRow.period >= f'{y}-04', ReconRow.period <= f'{y + 1}-03')


@app.route('/api/row/<int:row_id>/remark', methods=['POST'])
@login_required
def api_remark(row_id):
    row = db.session.get(ReconRow, row_id)
    if not row:
        abort(404)
    if not current_user.can_see_state(row.state_name):
        abort(403)
    data = request.get_json(force=True)
    old = row.team_remark
    row.team_remark = data.get('remark', '').strip()
    row.team_reason = data.get('reason', '').strip()
    row.remarked_by_id = current_user.id
    row.remarked_at = now_ist()
    if row.status == 'open':
        row.status = 'remarked'
    log_audit(row.id, current_user, 'remark', 'team_remark', old, row.team_remark)
    db.session.commit()
    return jsonify({'ok': True, 'row': row.to_dict()})


@app.route('/api/row/<int:row_id>/approve', methods=['POST'])
@admin_required
def api_approve(row_id):
    row = db.session.get(ReconRow, row_id)
    if not row:
        abort(404)
    data = request.get_json(force=True)
    action = data.get('action', 'approve')  # 'approve' | 'resolve' | 'reopen'
    if action == 'reopen':
        row.status = 'remarked' if row.team_remark else 'open'
        row.approved_by_id = None
        row.approved_at = None
    else:
        row.status = 'resolved' if action == 'resolve' else 'approved'
        row.approved_by_id = current_user.id
        row.approved_at = now_ist()
        row.resolution_note = data.get('note', '').strip()
    log_audit(row.id, current_user, action, 'status', '', row.status)
    db.session.commit()
    return jsonify({'ok': True, 'row': row.to_dict()})


@app.route('/api/counts')
@login_required
def api_counts():
    """Row counts per tab (respecting period/state + user state restriction)."""
    def base():
        q = ReconRow.query
        period = request.args.get('period')
        state = request.args.get('state')
        if period and period != 'all':
            q = q.filter(ReconRow.period == period)
        if state and state != 'all':
            q = q.filter(ReconRow.state_name == state)
        q = _apply_fy(q, request.args.get('fy'))
        if not current_user.is_admin and current_user.state_list():
            q = q.filter(ReconRow.state_name.in_(current_user.state_list()))
        return q
    def amt(q, col):
        return round(q.with_entities(func.coalesce(func.sum(col), 0)).scalar() or 0)
    fully_q = base().filter(ReconRow.category == 'matched',
                            ReconRow.recon_status.like('%Fully Reconciled%'))
    cross_q = base().filter(ReconRow.category == 'matched',
                            ~ReconRow.recon_status.like('%Fully Reconciled%'))
    books_q = base().filter(ReconRow.category == 'books_only')
    gstn_q = base().filter(ReconRow.category == 'gstn_only')
    return jsonify({
        'cross': cross_q.count(),         'cross_amt': amt(cross_q, ReconRow.books_total),
        'books': books_q.count(),         'books_amt': amt(books_q, ReconRow.books_total),
        'gstn': gstn_q.count(),           'gstn_amt': amt(gstn_q, ReconRow.gstn_total),
        'fully': fully_q.count(),         'fully_amt': amt(fully_q, ReconRow.books_total),
        'gap': base().with_entities(ReconRow.gstin).distinct().count(),
        'gap_amt': amt(base(), ReconRow.total_diff),
    })


@app.route('/api/rows/bulk', methods=['POST'])
@login_required
def api_bulk():
    """Apply an action to many rows at once.
    remark -> any logged-in user (own states); approve/resolve/reopen -> admin only."""
    data = request.get_json(force=True)
    ids = data.get('ids', [])
    action = data.get('action', '')
    if not ids:
        return jsonify({'ok': False, 'error': 'No rows selected.'}), 400

    rows = ReconRow.query.filter(ReconRow.id.in_(ids)).all()
    changed = 0

    if action == 'remark':
        remark = data.get('remark', '').strip()
        reason = data.get('reason', '').strip()
        for row in rows:
            if not current_user.can_see_state(row.state_name):
                continue
            old = row.team_remark
            if remark:
                row.team_remark = remark
            if reason:
                row.team_reason = reason
            row.remarked_by_id = current_user.id
            row.remarked_at = now_ist()
            if row.status == 'open':
                row.status = 'remarked'
            log_audit(row.id, current_user, 'remark', 'team_remark', old, row.team_remark)
            changed += 1
    elif action in ('approve', 'resolve', 'reopen'):
        if not current_user.is_admin:
            abort(403)
        note = data.get('note', '').strip()
        for row in rows:
            if action == 'reopen':
                row.status = 'remarked' if row.team_remark else 'open'
                row.approved_by_id = None
                row.approved_at = None
            else:
                row.status = 'resolved' if action == 'resolve' else 'approved'
                row.approved_by_id = current_user.id
                row.approved_at = now_ist()
                if note:
                    row.resolution_note = note
            log_audit(row.id, current_user, action, 'status', '', row.status)
            changed += 1
    elif action == 'assign':
        if not current_user.is_admin:
            abort(403)
        aid = data.get('assignee_id')
        assignee = db.session.get(User, int(aid)) if aid else None
        if aid and not assignee:
            return jsonify({'ok': False, 'error': 'Unknown assignee.'}), 400
        for row in rows:
            row.assigned_to_id = assignee.id if assignee else None
            log_audit(row.id, current_user, 'assign', 'assigned_to', '',
                      assignee.name if assignee else '(unassigned)')
            changed += 1
    else:
        return jsonify({'ok': False, 'error': 'Unknown action.'}), 400

    db.session.commit()
    return jsonify({'ok': True, 'changed': changed})


@app.route('/api/assignees')
@login_required
def api_assignees():
    """Book-Keeping users a row can be assigned to."""
    users = User.query.filter_by(role='user', active=True).order_by(User.name).all()
    return jsonify([{'id': u.id, 'name': u.name} for u in users])


def _breakdown_data(rows):
    """Split rows by workflow status and by team reason (RCM / ineligible / etc.),
    with count + amount at stake. Feeds dashboard, CFO summary and the Slack report."""
    def amt(r):
        # exposure: ITC tax for books-only (at risk), else |total diff|
        if r.category == 'books_only':
            return (r.books_igst or 0) + (r.books_cgst or 0) + (r.books_sgst or 0)
        return abs(r.total_diff or 0)
    by_status, by_reason = {}, {}
    for r in rows:
        st = r.status or 'open'
        s = by_status.setdefault(st, {'count': 0, 'value': 0.0})
        s['count'] += 1; s['value'] += amt(r)
        rsn = (r.team_reason or '').strip()
        if rsn:
            d = by_reason.setdefault(rsn, {'count': 0, 'value': 0.0})
            d['count'] += 1; d['value'] += amt(r)
    rnd = lambda d: {k: {'count': v['count'], 'value': round(v['value'])} for k, v in d.items()}
    return {'by_status': rnd(by_status),
            'by_reason': dict(sorted(rnd(by_reason).items(), key=lambda kv: -kv[1]['value']))}


@app.route('/api/breakdown')
@login_required
def api_breakdown():
    return jsonify(_breakdown_data(_filtered_rows_query().all()))


def _filtered_rows_query():
    """ReconRow query filtered by period/state/fy args + user's state restriction."""
    q = ReconRow.query
    period = request.args.get('period')
    state = request.args.get('state')
    if period and period != 'all':
        q = q.filter(ReconRow.period == period)
    if state and state != 'all':
        q = q.filter(ReconRow.state_name == state)
    q = _apply_fy(q, request.args.get('fy'))
    if not current_user.is_admin and current_user.state_list():
        q = q.filter(ReconRow.state_name.in_(current_user.state_list()))
    return q


def gap_from_rows(reconrows):
    """Aggregate ReconRows per GSTIN into the gap-analysis rows (Books vs 2B)."""
    agg = {}
    for r in reconrows:
        a = agg.get(r.gstin)
        if a is None:
            a = agg[r.gstin] = {
                'gstin': r.gstin, 'vendor': r.vendor, 'state': r.state_name,
                'b_cnt': 0, 'b_taxable': 0.0, 'b_tax': 0.0, 'b_total': 0.0,
                'g_cnt': 0, 'g_taxable': 0.0, 'g_tax': 0.0, 'g_total': 0.0,
            }
        if r.vendor and (not a['vendor'] or a['vendor'] == r.gstin):
            a['vendor'] = r.vendor
        b_tax = (r.books_igst or 0) + (r.books_cgst or 0) + (r.books_sgst or 0)
        g_tax = (r.gstn_igst or 0) + (r.gstn_cgst or 0) + (r.gstn_sgst or 0)
        if r.category in ('matched', 'books_only'):
            a['b_cnt'] += 1; a['b_taxable'] += r.books_taxable or 0
            a['b_tax'] += b_tax; a['b_total'] += r.books_total or 0
        if r.category in ('matched', 'gstn_only'):
            a['g_cnt'] += 1; a['g_taxable'] += r.gstn_taxable or 0
            a['g_tax'] += g_tax; a['g_total'] += r.gstn_total or 0

    rows = []
    for a in agg.values():
        total_gap = a['b_total'] - a['g_total']
        if a['b_cnt'] == 0 and a['g_cnt'] > 0:
            risk, remark = 'HIGH', 'Only in GSTR-2B'
        elif a['b_cnt'] > 0 and a['g_cnt'] == 0:
            risk, remark = 'CRITICAL', 'Only in Books — vendor not filed'
        elif abs(total_gap) < 1:
            risk, remark = 'LOW', 'Amount matched'
        elif abs(total_gap) > 100000:
            risk, remark = 'HIGH', ('Books > 2B' if total_gap > 0 else '2B > Books')
        else:
            risk, remark = 'MEDIUM', ('Books > 2B' if total_gap > 0 else '2B > Books')
        action = {'CRITICAL': 'URGENT: Vendor follow-up', 'HIGH': 'Investigate',
                  'LOW': 'Verify invoice nos'}.get(risk, 'Review')
        denom = max(abs(a['b_total']), abs(a['g_total']))
        rows.append({
            'gstin': a['gstin'], 'vendor': a['vendor'], 'state': a['state'],
            'b_cnt': a['b_cnt'], 'b_taxable': round(a['b_taxable'], 2),
            'b_tax': round(a['b_tax'], 2), 'b_total': round(a['b_total'], 2),
            'g_cnt': a['g_cnt'], 'g_taxable': round(a['g_taxable'], 2),
            'g_tax': round(a['g_tax'], 2), 'g_total': round(a['g_total'], 2),
            'taxable_gap': round(a['b_taxable'] - a['g_taxable'], 2),
            'tax_gap': round(a['b_tax'] - a['g_tax'], 2),
            'total_gap': round(total_gap, 2),
            'gap_pct': round(total_gap / denom, 4) if denom else 0,
            'risk': risk, 'remark': remark, 'action': action,
        })
    rows.sort(key=lambda x: abs(x['total_gap']), reverse=True)
    return rows


@app.route('/api/gap-analysis')
@login_required
def api_gap_analysis():
    """Per-GSTIN gap analysis (Books vs GSTR-2B), mirroring the report sheet."""
    rows = gap_from_rows(_filtered_rows_query().all())
    return jsonify({'rows': rows, 'count': len(rows)})


# ---- Feature 5: Vendor compliance scorecard ----
def _grade(score):
    return 'A' if score >= 90 else 'B' if score >= 75 else 'C' if score >= 50 else 'D'


@app.route('/scorecard')
@login_required
def scorecard_page():
    return render_template('scorecard.html', states=WIOM_STATES)


@app.route('/api/scorecard')
@login_required
def api_scorecard():
    """Per-vendor compliance: how reliably they appear in GSTR-2B vs Books."""
    rows = _filtered_rows_query().all()
    agg = {}
    for r in rows:
        a = agg.get(r.gstin)
        if a is None:
            a = agg[r.gstin] = {'gstin': r.gstin, 'vendor': r.vendor, 'periods': set(),
                                'matched': 0, 'books_only': 0, 'gstn_only': 0,
                                'books_val': 0.0, 'itc_risk': 0.0, 'open': 0}
        if r.vendor and (not a['vendor'] or a['vendor'] == r.gstin):
            a['vendor'] = r.vendor
        a['periods'].add(r.period)
        tax = (r.books_igst or 0) + (r.books_cgst or 0) + (r.books_sgst or 0)
        if r.category == 'matched':
            a['matched'] += 1
        elif r.category == 'books_only':
            a['books_only'] += 1
            a['books_val'] += r.books_total or 0
            a['itc_risk'] += tax
            if r.status not in ('approved', 'resolved'):
                a['open'] += 1
        elif r.category == 'gstn_only':
            a['gstn_only'] += 1
    out = []
    for a in agg.values():
        filed = a['matched'] + a['gstn_only']        # appeared in 2B
        not_filed = a['books_only']                   # in books but vendor didn't file
        denom = filed + not_filed
        score = round(100 * filed / denom) if denom else 100
        out.append({
            'gstin': a['gstin'], 'vendor': a['vendor'], 'months': len(a['periods']),
            'matched': a['matched'], 'books_only': not_filed, 'gstn_only': a['gstn_only'],
            'reliability': score, 'grade': _grade(score),
            'itc_risk': round(a['itc_risk']), 'open': a['open'],
        })
    out.sort(key=lambda x: (x['reliability'], -x['itc_risk']))  # worst first
    return jsonify({'rows': out, 'count': len(out)})


@app.route('/export')
@login_required
def export_cumulative():
    """Download a CUMULATIVE multi-sheet Excel (all months to date) from the DB,
    optionally scoped to a state. Mirrors the report tabs + workflow columns."""
    import export_excel
    rows = _filtered_rows_query().order_by(
        ReconRow.state_name, ReconRow.period, ReconRow.gstin).all()
    gap = gap_from_rows(rows)
    state = request.args.get('state')
    scope = (state if state and state != 'all' else 'All states') + ' · cumulative to date'
    bio = export_excel.build_cumulative_excel(rows, gap, scope)
    fname = f"WIOM_Cumulative_{(state or 'AllStates').replace(' ', '')}_{now_ist().strftime('%Y%m%d')}.xlsx"
    return send_file(bio, as_attachment=True, download_name=fname,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/api/row/<int:row_id>/history')
@login_required
def api_history(row_id):
    logs = AuditLog.query.filter_by(row_id=row_id).order_by(AuditLog.created_at).all()
    return jsonify([{
        'user': l.user_name, 'action': l.action, 'field': l.field,
        'old': l.old_value, 'new': l.new_value,
        'at': l.created_at.strftime('%d-%b-%Y %H:%M'),
    } for l in logs])


# ---- Feature 8: comment thread per row ----
@app.route('/api/row/<int:row_id>/comments', methods=['GET', 'POST'])
@login_required
def api_comments(row_id):
    row = db.session.get(ReconRow, row_id)
    if not row:
        abort(404)
    if not current_user.can_see_state(row.state_name):
        abort(403)
    if request.method == 'POST':
        text = (request.get_json(force=True).get('text') or '').strip()
        if text:
            db.session.add(RowComment(row_id=row_id, user_id=current_user.id,
                user_name=current_user.name, user_role=current_user.role_display, text=text))
            db.session.commit()
    cs = RowComment.query.filter_by(row_id=row_id).order_by(RowComment.created_at).all()
    return jsonify([{'user': c.user_name, 'role': c.user_role, 'text': c.text,
                     'at': c.created_at.strftime('%d-%b-%Y %H:%M')} for c in cs])


# ---- Feature 10: vendor follow-up (Book-Keeping shoots mail; everyone sees tracking) ----
@app.route('/api/row/<int:row_id>/followup', methods=['POST'])
@login_required
def api_followup(row_id):
    row = db.session.get(ReconRow, row_id)
    if not row:
        abort(404)
    if not current_user.can_see_state(row.state_name):
        abort(403)
    note = (request.get_json(force=True).get('note') or '').strip()
    row.followup_at = now_ist()
    row.followup_by_id = current_user.id
    row.followup_count = (row.followup_count or 0) + 1
    if note:
        row.followup_note = note

    # If SMTP configured + vendor email known, actually send the reminder.
    emailed = None
    vm = db.session.get(VendorMaster, row.gstin)
    vendor_email = vm.email if vm else ''
    if _smtp_configured() and vendor_email:
        import email_util
        inv = row.books_inv or row.gstn_inv or ''
        amt = row.books_total or row.gstn_total or 0
        body = render_template('vendor_followup_email.html', vendor=row.vendor or 'Vendor',
                               gstin=row.gstin, inv=inv, amount=f'{amt:,.0f}', note=note)
        ok, msg = email_util.send_email(_smtp_cfg(), vendor_email,
            f'GST follow-up: {row.gstin} {inv}', body)
        emailed = {'ok': ok, 'to': vendor_email, 'msg': msg}

    log_audit(row.id, current_user, 'followup', 'followup', '',
              f'#{row.followup_count} by {current_user.name}'
              + (f' · emailed {vendor_email}' if emailed and emailed['ok'] else ''))
    db.session.commit()
    return jsonify({'ok': True, 'row': row.to_dict(), 'emailed': emailed,
                    'mailto': not (emailed and emailed['ok'])})


# ---- Feature 5: vendor drill-down (all rows for a GSTIN, all states/months) ----
@app.route('/api/vendor-rows/<gstin>')
@login_required
def api_vendor_rows(gstin):
    q = ReconRow.query.filter(ReconRow.gstin == gstin.strip())
    if not current_user.is_admin and current_user.state_list():
        q = q.filter(ReconRow.state_name.in_(current_user.state_list()))
    rows = q.order_by(ReconRow.period, ReconRow.category).all()
    vm = db.session.get(VendorMaster, gstin.strip())
    return jsonify({'gstin': gstin, 'vendor': (vm.name if vm else (rows[0].vendor if rows else '')),
                    'rows': [r.to_dict() for r in rows], 'count': len(rows)})


# ---- Feature 4: ITC-at-risk trend (month series) ----
@app.route('/api/trend')
@login_required
def api_trend():
    q = ReconRow.query
    if not current_user.is_admin and current_user.state_list():
        q = q.filter(ReconRow.state_name.in_(current_user.state_list()))
    series = {}
    for r in q.all():
        s = series.setdefault(r.period, {'itc_risk': 0.0, 'gap': 0.0})
        if r.category == 'books_only':  # in books, not in 2B → ITC at risk
            s['itc_risk'] += (r.books_igst or 0) + (r.books_cgst or 0) + (r.books_sgst or 0)
        s['gap'] += r.total_diff or 0
    out = [{'period': p, 'itc_risk': round(v['itc_risk'], 0), 'gap': round(v['gap'], 0)}
           for p, v in sorted(series.items())]
    return jsonify(out)


@app.route('/api/cumulative')
@login_required
def api_cumulative():
    """State x period pivot of total differences — cumulative view."""
    q = db.session.query(
        ReconRow.state_name, ReconRow.period,
        func.count(ReconRow.id),
        func.sum(ReconRow.total_diff),
        func.sum(db.case((ReconRow.status.in_(['approved', 'resolved']), 1), else_=0)),
    )
    if not current_user.is_admin and current_user.state_list():
        q = q.filter(ReconRow.state_name.in_(current_user.state_list()))
    q = q.group_by(ReconRow.state_name, ReconRow.period)
    out = {}
    for state, period, cnt, diff, done in q.all():
        out.setdefault(state, {})[period] = {
            'count': cnt, 'diff': round(diff or 0, 2), 'done': done,
        }
    return jsonify(out)


@app.route('/api/vendor/<gstin>')
@login_required
def api_vendor(gstin):
    vm = db.session.get(VendorMaster, gstin.strip())
    if vm:
        if not current_user.can_see_state(vm.state_name):
            abort(403)
        return jsonify({'gstin': vm.gstin, 'name': vm.name,
                        'state': vm.state_name, 'source': vm.source})
    return jsonify({'gstin': gstin, 'name': 'Unknown', 'state': '', 'source': ''})


# ---- Feature 7: bulk import remarks from CSV ----
@app.route('/import-remarks', methods=['GET', 'POST'])
@login_required
def import_remarks():
    if request.method == 'POST':
        import csv, io as _io
        f = request.files.get('file')
        if not f or not f.filename.lower().endswith('.csv'):
            return jsonify({'ok': False, 'error': 'Upload a .csv file.'}), 400
        text = f.read().decode('utf-8-sig', errors='replace')
        reader = csv.DictReader(_io.StringIO(text))
        applied, skipped = 0, 0
        for r in reader:
            row = None
            rid = (r.get('row_id') or r.get('id') or '').strip()
            if rid.isdigit():
                row = db.session.get(ReconRow, int(rid))
            if row is None:
                g = (r.get('gstin') or '').strip()
                inv = (r.get('invoice') or r.get('books_inv') or r.get('gstn_inv') or '').strip()
                if g and inv:
                    row = ReconRow.query.filter(ReconRow.gstin == g,
                        (ReconRow.books_inv == inv) | (ReconRow.gstn_inv == inv)).first()
            if row is None or not current_user.can_see_state(row.state_name):
                skipped += 1
                continue
            remark = (r.get('remark') or '').strip()
            reason = (r.get('reason') or '').strip()
            if not (remark or reason):
                skipped += 1
                continue
            old = row.team_remark
            if remark:
                row.team_remark = remark
            if reason:
                row.team_reason = reason
            row.remarked_by_id = current_user.id
            row.remarked_at = now_ist()
            if row.status == 'open':
                row.status = 'remarked'
            log_audit(row.id, current_user, 'remark', 'team_remark(import)', old, row.team_remark)
            applied += 1
        db.session.commit()
        return jsonify({'ok': True, 'applied': applied, 'skipped': skipped})
    return render_template('import_remarks.html')


# ---- Feature 9: CFO one-page executive summary (print → PDF) ----
def _cfo_context(rows, state):
    gap = gap_from_rows(rows)
    def tax(r, side):
        if side == 'b':
            return (r.books_igst or 0) + (r.books_cgst or 0) + (r.books_sgst or 0)
        return (r.gstn_igst or 0) + (r.gstn_cgst or 0) + (r.gstn_sgst or 0)
    itc_risk = sum(tax(r, 'b') for r in rows if r.category == 'books_only')
    excess_2b = sum(tax(r, 'g') for r in rows if r.category == 'gstn_only')
    matched = [r for r in rows if r.category == 'matched']
    fully = sum(1 for r in matched if 'Fully Reconciled' in (r.recon_status or ''))
    by_state = {}
    for r in rows:
        s = by_state.setdefault(r.state_name, {'rows': 0, 'gap': 0.0, 'risk': 0.0})
        s['rows'] += 1; s['gap'] += r.total_diff or 0
        if r.category == 'books_only':
            s['risk'] += tax(r, 'b')
    done = sum(1 for r in rows if r.status in ('approved', 'resolved'))
    return dict(
        scope=(state if state and state != 'all' else 'All States'),
        generated=now_ist().strftime('%d-%b-%Y %H:%M'),
        total=len(rows), matched=len(matched), fully=fully,
        books_only=sum(1 for r in rows if r.category == 'books_only'),
        gstn_only=sum(1 for r in rows if r.category == 'gstn_only'),
        itc_risk=round(itc_risk), excess_2b=round(excess_2b),
        done=done, pending=len(rows) - done,
        by_state={k: {'rows': v['rows'], 'gap': round(v['gap']), 'risk': round(v['risk'])}
                  for k, v in sorted(by_state.items())},
        top_gaps=gap[:10], breakdown=_breakdown_data(rows))


@app.route('/cfo-summary')
@admin_required
def cfo_summary():
    rows = _filtered_rows_query().all()
    return render_template('cfo_summary.html', **_cfo_context(rows, request.args.get('state')))


# ---- Feature 12: login history + DB backup (super admin) ----
@app.route('/login-history')
@superadmin_required
def login_history():
    events = LoginEvent.query.order_by(LoginEvent.created_at.desc()).limit(500).all()
    return render_template('login_history.html', events=events)


@app.route('/settings/backup')
@superadmin_required
def backup_db():
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if uri.startswith('sqlite:///'):
        path = uri.replace('sqlite:///', '', 1)
        if os.path.exists(path):
            return send_file(path, as_attachment=True,
                download_name=f"wiom_recon_backup_{now_ist().strftime('%Y%m%d_%H%M')}.db")
    return jsonify({'error': 'Backup only available for SQLite (local) deployments.'}), 400


@app.route('/settings/clear-recon', methods=['POST'])
@superadmin_required
def clear_recon_data():
    """Wipe all recon rows/runs/comments/audit logs. Users, settings, vendor master kept."""
    from models import AuditLog, RowComment, ReconRow, ReconRun
    AuditLog.query.delete(synchronize_session=False)
    RowComment.query.delete(synchronize_session=False)
    ReconRow.query.delete(synchronize_session=False)
    ReconRun.query.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True, 'msg': 'All reconciliation data cleared. Users and settings intact.'})


# ----------------------------------------------------------------------
def _summary_stats(user):
    q = ReconRow.query
    if not user.is_admin and user.state_list():
        q = q.filter(ReconRow.state_name.in_(user.state_list()))
    total = q.count()
    open_n = q.filter(ReconRow.status == 'open').count()
    remarked = q.filter(ReconRow.status == 'remarked').count()
    done = q.filter(ReconRow.status.in_(['approved', 'resolved'])).count()
    itc = 0.0
    for r in q.filter(ReconRow.category == 'books_only').all():
        itc += (r.books_igst or 0) + (r.books_cgst or 0) + (r.books_sgst or 0)
    return {'total': total, 'open': open_n, 'remarked': remarked, 'done': done,
            'itc_risk': round(itc)}


if __name__ == '__main__':
    # Optional port override from CLI (used by the preview launcher)
    port = Config.PORT
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    print("\n" + "=" * 64)
    print("  WIOM Zoho Books vs GST Recon — Workflow Platform")
    print(f"  http://localhost:{port}")
    print(f"  Login: {Config.ADMIN_EMAIL} / {Config.ADMIN_PASSWORD}")
    print("=" * 64 + "\n")
    app.run(debug=False, port=port, host=Config.HOST)
