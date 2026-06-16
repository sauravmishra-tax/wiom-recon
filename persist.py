"""
Persist engine output (Agent 3 cross-match results) into the database
as workflow rows, and sync the vendor master.

Called after the 9-agent engine finishes a run.
"""
import pandas as pd
from collections import Counter
from datetime import datetime
from models import db, ReconRow, ReconRun, RowComment, AuditLog, VendorMaster, now_ist
from state_codes import state_from_gstin


def _carry_key(row):
    """Identity of an invoice row for carry-over across re-uploads."""
    return (row.gstin or '', (row.books_inv or '').strip(), (row.gstn_inv or '').strip())


def derive_period(inv_matched, books_unmatched, gstn_unmatched):
    """Auto-detect the reconciliation period (YYYY-MM) from invoice dates.
    Uses the most common month across all rows; falls back to current month
    so the user never has to pick it manually."""
    months = []

    def collect(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return
        ts = pd.to_datetime(val, errors='coerce', dayfirst=True)
        if pd.notna(ts):
            months.append(ts.strftime('%Y-%m'))

    for m in (inv_matched or []):
        collect(m.get('Books_Date'))
        collect(m.get('GSTN_Date'))
    for m in (books_unmatched or []):
        collect(m.get('Date'))
    for m in (gstn_unmatched or []):
        collect(m.get('Date'))

    if months:
        return Counter(months).most_common(1)[0][0]
    return now_ist().strftime('%Y-%m')  # fallback: month the recon was run


def _f(v):
    """Coerce to float safely (NaN/None/'' -> 0)."""
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _s(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    return str(v).strip()


def _date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    try:
        return pd.Timestamp(v).strftime('%d-%m-%Y')
    except Exception:
        return str(v)


def sync_vendor_master(vendor_map, gst_cache=None):
    """Upsert GSTIN -> name into VendorMaster. Source priority: books > cache."""
    count = 0
    for gstin, name in (vendor_map or {}).items():
        g = _s(gstin)
        if not g:
            continue
        code, state = state_from_gstin(g)
        vm = db.session.get(VendorMaster, g)
        if vm is None:
            vm = VendorMaster(gstin=g)
            db.session.add(vm)
        vm.name = _s(name) or vm.name
        vm.state_code = code
        vm.state_name = state
        vm.source = 'books'
        count += 1
    db.session.commit()
    return count


def _make_row(run, m, category, run_state=None, run_state_code=None):
    gstin = _s(m.get('GSTIN'))
    if run_state:
        code, state = (run_state_code or ''), run_state
    else:
        code, state = state_from_gstin(gstin)

    if category == 'matched':
        # matched dicts carry both sides under Books_* / GSTN_* keys
        vals = dict(
            b_inv=_s(m.get('Books_Inv')), g_inv=_s(m.get('GSTN_Inv')),
            b_date=_date(m.get('Books_Date')), g_date=_date(m.get('GSTN_Date')),
            b_taxable=_f(m.get('Books_Taxable')), g_taxable=_f(m.get('GSTN_Taxable')),
            b_igst=_f(m.get('Books_IGST')), g_igst=_f(m.get('GSTN_IGST')),
            b_cgst=_f(m.get('Books_CGST')), g_cgst=_f(m.get('GSTN_CGST')),
            b_sgst=_f(m.get('Books_SGST')), g_sgst=_f(m.get('GSTN_SGST')),
            b_total=_f(m.get('Books_Total')), g_total=_f(m.get('GSTN_Total')),
        )
    else:
        # books_unmatched / gstn_unmatched are single-sided: Inv/Date/Taxable/IGST/CGST/SGST/Total
        inv, dt = _s(m.get('Inv')), _date(m.get('Date'))
        taxable, igst = _f(m.get('Taxable')), _f(m.get('IGST'))
        cgst, sgst, total = _f(m.get('CGST')), _f(m.get('SGST')), _f(m.get('Total'))
        z = dict(b_inv='', g_inv='', b_date='', g_date='', b_taxable=0, g_taxable=0,
                 b_igst=0, g_igst=0, b_cgst=0, g_cgst=0, b_sgst=0, g_sgst=0, b_total=0, g_total=0)
        if category == 'books_only':
            z.update(b_inv=inv, b_date=dt, b_taxable=taxable, b_igst=igst,
                     b_cgst=cgst, b_sgst=sgst, b_total=total)
        else:  # gstn_only
            z.update(g_inv=inv, g_date=dt, g_taxable=taxable, g_igst=igst,
                     g_cgst=cgst, g_sgst=sgst, g_total=total)
        vals = z

    return ReconRow(
        run_id=run.id, period=run.period, category=category,
        gstin=gstin, vendor=_s(m.get('Vendor')),
        state_code=code, state_name=state,
        txn_type=_s(m.get('Type')) or 'Bill',
        books_inv=vals['b_inv'], gstn_inv=vals['g_inv'],
        books_date=vals['b_date'], gstn_date=vals['g_date'],
        books_taxable=vals['b_taxable'], gstn_taxable=vals['g_taxable'],
        books_igst=vals['b_igst'], gstn_igst=vals['g_igst'],
        books_cgst=vals['b_cgst'], gstn_cgst=vals['g_cgst'],
        books_sgst=vals['b_sgst'], gstn_sgst=vals['g_sgst'],
        books_total=vals['b_total'], gstn_total=vals['g_total'],
        total_diff=vals['b_total'] - vals['g_total'],
        recon_status=_s(m.get('Recon_Status')) or _s(m.get('Remark')),
        system_remark=_s(m.get('Remark')),
        status='open',
    )


def persist_reconciled_df(run, df, vendor_map, run_state=None, run_state_code=None):
    """Persist Zoho's already-reconciled invoices (input 'Reconciled'/'Matched'
    sheets) as fully-reconciled matched rows. Books = GSTR-2B (total_diff 0)."""
    if df is None or len(df) == 0 or 'GST Registration Number' not in df.columns:
        return 0
    n = 0
    for _, r in df.iterrows():
        gstin = _s(r.get('GST Registration Number'))
        if not gstin:
            continue
        if run_state:
            code, state = (run_state_code or ''), run_state
        else:
            code, state = state_from_gstin(gstin)
        vendor = _s(r.get('Vendor Name')) or (vendor_map or {}).get(gstin, gstin)
        inv = _s(r.get('Transaction Number'))
        dt = _date(r.get('Transaction Date'))
        taxable = _f(r.get('Taxable Amount'))
        igst, cgst, sgst = _f(r.get('IGST Amount')), _f(r.get('CGST Amount')), _f(r.get('SGST Amount'))
        total = taxable + igst + cgst + sgst
        db.session.add(ReconRow(
            run_id=run.id, period=run.period, category='matched',
            gstin=gstin, vendor=vendor, state_code=code, state_name=state,
            txn_type=_s(r.get('Transaction Type')) or 'Bill',
            books_inv=inv, gstn_inv=inv, books_date=dt, gstn_date=dt,
            books_taxable=taxable, gstn_taxable=taxable,
            books_igst=igst, gstn_igst=igst, books_cgst=cgst, gstn_cgst=cgst,
            books_sgst=sgst, gstn_sgst=sgst, books_total=total, gstn_total=total,
            total_diff=0.0, recon_status='Fully Reconciled (Zoho Auto)',
            system_remark='Matched by Zoho auto-recon — ITC safe', status='open'))
        n += 1
    return n


def _build_carry_map(run, run_state):
    """From prior run(s) for the SAME period+state, map invoice-key -> the row's
    workflow state (remarks/reason/status/approval/follow-up/assignment). Returns
    (carry_map, prior_run_ids)."""
    prior = ReconRun.query.filter(ReconRun.period == run.period,
                                  ReconRun.state == (run_state or ''),
                                  ReconRun.id != run.id).all()
    prior_ids = [p.id for p in prior]
    carry = {}
    if prior_ids:
        for old in ReconRow.query.filter(ReconRow.run_id.in_(prior_ids)).all():
            touched = (old.team_remark or old.team_reason or old.status != 'open'
                       or old.assigned_to_id or (old.followup_count or 0) > 0)
            if touched:
                carry[_carry_key(old)] = old
    return carry, prior_ids


def persist_run(run, inv_matched, books_unmatched, gstn_unmatched,
                vendor_map=None, gst_cache=None, run_state=None,
                reconciled_dfs=None):
    """Write all engine rows into the DB for this run. Returns (row_count, carried).
    Smart re-upload: if a prior run exists for the same period+state, its remarks/
    reasons/status/approval/follow-up/assignment/comments are carried over to matching
    invoices, then the old snapshot is replaced (no duplicates)."""
    if vendor_map:
        sync_vendor_master(vendor_map, gst_cache)

    from state_codes import code_for_state
    rs_code = code_for_state(run_state) if run_state else None

    carry, prior_ids = _build_carry_map(run, run_state)

    n = 0
    for m in (inv_matched or []):
        db.session.add(_make_row(run, m, 'matched', run_state, rs_code)); n += 1
    for m in (books_unmatched or []):
        db.session.add(_make_row(run, m, 'books_only', run_state, rs_code)); n += 1
    for m in (gstn_unmatched or []):
        db.session.add(_make_row(run, m, 'gstn_only', run_state, rs_code)); n += 1
    for df in (reconciled_dfs or []):
        n += persist_reconciled_df(run, df, vendor_map, run_state, rs_code)

    db.session.flush()  # assign ids to the new rows

    # ---- carry over workflow state from the prior snapshot to matching invoices ----
    carried = 0
    if carry:
        for row in ReconRow.query.filter_by(run_id=run.id).all():
            old = carry.get(_carry_key(row))
            if not old:
                continue
            row.team_remark, row.team_reason = old.team_remark, old.team_reason
            row.status = old.status
            row.remarked_by_id, row.remarked_at = old.remarked_by_id, old.remarked_at
            row.approved_by_id, row.approved_at = old.approved_by_id, old.approved_at
            row.resolution_note = old.resolution_note
            row.followup_at, row.followup_by_id = old.followup_at, old.followup_by_id
            row.followup_count, row.followup_note = old.followup_count, old.followup_note
            row.assigned_to_id = old.assigned_to_id
            for c in RowComment.query.filter_by(row_id=old.id).all():
                db.session.add(RowComment(row_id=row.id, user_id=c.user_id,
                    user_name=c.user_name, user_role=c.user_role,
                    text=c.text, created_at=c.created_at))
            carried += 1

    # ---- replace the old snapshot: delete prior runs + their rows/comments/audit ----
    if prior_ids:
        old_row_ids = [r.id for r in ReconRow.query
                       .filter(ReconRow.run_id.in_(prior_ids)).all()]
        if old_row_ids:
            AuditLog.query.filter(AuditLog.row_id.in_(old_row_ids)).delete(synchronize_session=False)
            RowComment.query.filter(RowComment.row_id.in_(old_row_ids)).delete(synchronize_session=False)
            ReconRow.query.filter(ReconRow.run_id.in_(prior_ids)).delete(synchronize_session=False)
        ReconRun.query.filter(ReconRun.id.in_(prior_ids)).delete(synchronize_session=False)

    run.total_rows = n
    db.session.commit()
    return n, carried
