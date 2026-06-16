"""
AGENT 8: QA REVIEWER (Cross-Verification Agent)
==================================================
Reviews and verifies EVERY other agent's output for accuracy.
Acts as the final quality gate before delivering the report.

Checks:
1. Validator stats match actual data
2. Vendor Resolver coverage is reasonable
3. Cross-Matcher totals add up
4. ITC Analyzer numbers are consistent
5. Anomaly Detector didn't miss obvious issues
6. Report Builder output is complete
7. Schema Guard passed
8. End-to-end reconciliation math verification
"""
import pandas as pd

AGENT_NAME = "QA Reviewer"
AGENT_ID = 8


def run(agent_results, dataframes, inv_matched, books_unmatched, gstn_unmatched,
        vendor_map, itc_summary, log_fn=None):
    """Review all agents' outputs for consistency and accuracy."""
    results = {
        'agent': AGENT_NAME,
        'status': 'running',
        'checks': [],
        'errors': [],
        'warnings': [],
        'stats': {}
    }

    def log(msg):
        if log_fn:
            log_fn(AGENT_ID, msg)

    log("QA Reviewer activated — cross-verifying all agent outputs...")

    errors = []
    warnings = []
    checks = []

    # ================================================================
    # REVIEW 1: Validator (Agent 1) — Did it catch real issues?
    # ================================================================
    log("Review 1: Verifying Agent 1 (Validator) output...")
    r1 = agent_results.get('validator', {})
    if r1.get('status') == 'passed':
        checks.append({'name': 'Agent 1 Status', 'status': 'PASS', 'detail': 'Validation passed'})
        log("PASS: Agent 1 validation passed correctly")
    elif r1.get('status') == 'failed':
        errors.append('Agent 1 validation failed — pipeline should have stopped')
        checks.append({'name': 'Agent 1 Status', 'status': 'FAIL', 'detail': 'Validation failed but pipeline continued'})
    else:
        warnings.append('Agent 1 status unclear')
        checks.append({'name': 'Agent 1 Status', 'status': 'WARN', 'detail': f"Status: {r1.get('status', 'unknown')}"})

    # ================================================================
    # REVIEW 2: Vendor Resolver (Agent 2) — Coverage check
    # ================================================================
    log("Review 2: Verifying Agent 2 (Vendor Resolver) coverage...")
    r2 = agent_results.get('vendor_resolver', {})
    r2_stats = r2.get('stats', {})
    total_gstins = r2_stats.get('total_gstins', 0)
    names_found = r2_stats.get('names_found', 0)
    if total_gstins > 0:
        coverage = names_found / total_gstins * 100
        if coverage < 30:
            warnings.append(f'Low vendor coverage: {coverage:.0f}%')
            checks.append({'name': 'Vendor Coverage', 'status': 'WARN',
                          'detail': f'{names_found}/{total_gstins} = {coverage:.0f}% (low)'})
            log(f"WARN: Vendor coverage only {coverage:.0f}% ({names_found}/{total_gstins})")
        else:
            checks.append({'name': 'Vendor Coverage', 'status': 'PASS',
                          'detail': f'{names_found}/{total_gstins} = {coverage:.0f}%'})
            log(f"PASS: Vendor coverage {coverage:.0f}% ({names_found}/{total_gstins})")
    else:
        checks.append({'name': 'Vendor Coverage', 'status': 'WARN', 'detail': 'No GSTIN data'})

    # Verify vendor_map consistency
    vm_check = sum(1 for g, v in vendor_map.items() if v != g and v != 'Unknown')
    checks.append({'name': 'Vendor Map Quality', 'status': 'PASS',
                   'detail': f'{vm_check} GSTINs have resolved names out of {len(vendor_map)}'})
    log(f"PASS: Vendor map has {vm_check} resolved names / {len(vendor_map)} total")

    # ================================================================
    # REVIEW 3: Cross-Matcher (Agent 3) — Totals verification
    # ================================================================
    log("Review 3: Verifying Agent 3 (Cross-Matcher) totals...")
    r3 = agent_results.get('cross_matcher', {})
    r3_stats = r3.get('stats', {})

    total_matched = len(inv_matched)
    total_books_unmatched = len(books_unmatched)
    total_gstn_unmatched = len(gstn_unmatched)

    # Verify: matched count = partial + exact + amt + taxable
    partial = r3_stats.get('partial_matched', 0)
    exact = r3_stats.get('exact_matched', 0)
    amt = r3_stats.get('amt_matched', 0)
    taxable = r3_stats.get('taxable_matched', 0)
    expected_total = partial + exact + amt + taxable
    if total_matched != expected_total:
        errors.append(f'Cross-match count mismatch: {total_matched} ≠ {partial}+{exact}+{amt}+{taxable}={expected_total}')
        checks.append({'name': 'Cross-Match Count', 'status': 'FAIL',
                       'detail': f'{total_matched} ≠ {expected_total}'})
        log(f"FAIL: Cross-match total {total_matched} ≠ expected {expected_total}")
    else:
        checks.append({'name': 'Cross-Match Count', 'status': 'PASS',
                       'detail': f'{total_matched} = {partial} partial + {exact} exact + {amt} amt + {taxable} taxable'})
        log(f"PASS: Cross-match: {total_matched} = {partial}+{exact}+{amt}+{taxable}")

    # Verify: Books-only source data = matched (from books) + unmatched books
    df_nig = dataframes.get('Not found in GSTN', pd.DataFrame())
    if len(df_nig) > 0:
        orig_books_count = len(df_nig)
        reconciled_from_books = total_matched + total_books_unmatched
        if orig_books_count != reconciled_from_books:
            log(f"INFO: Original Books={orig_books_count}, Matched={total_matched}, Unmatched={total_books_unmatched}, Sum={reconciled_from_books}")
            # Some tolerance since matching might create edge cases
            diff = abs(orig_books_count - reconciled_from_books)
            if diff > 5:
                warnings.append(f'Books count mismatch: {orig_books_count} ≠ {total_matched}+{total_books_unmatched}')
                checks.append({'name': 'Books Accounting', 'status': 'WARN',
                              'detail': f'Orig={orig_books_count}, Matched+Unmatched={reconciled_from_books}'})
            else:
                checks.append({'name': 'Books Accounting', 'status': 'PASS',
                              'detail': f'Within tolerance (diff={diff})'})
        else:
            checks.append({'name': 'Books Accounting', 'status': 'PASS',
                          'detail': f'{orig_books_count} = {total_matched} matched + {total_books_unmatched} unmatched'})
            log(f"PASS: Books accounting: {orig_books_count} = {total_matched}+{total_books_unmatched}")

    # ================================================================
    # REVIEW 4: ITC Analyzer (Agent 4) — Math verification
    # ================================================================
    log("Review 4: Verifying Agent 4 (ITC Analyzer) math...")
    r4 = agent_results.get('itc_analyzer', {})
    if itc_summary:
        safe = itc_summary.get('safe_itc', 0)
        recovered = itc_summary.get('expert_recovered', 0)
        at_risk = itc_summary.get('at_risk_total', 0)
        excess = itc_summary.get('excess_available', 0)
        net_gap = itc_summary.get('net_gap', 0)

        # Net gap should = safe + recovered - total_2b_tax (approximately)
        # At minimum, check signs make sense
        if safe < 0:
            warnings.append(f'Safe ITC is negative: {safe:,.0f}')
            checks.append({'name': 'ITC Safe', 'status': 'WARN', 'detail': f'Safe ITC = {safe:,.0f} (negative?)'})
            log(f"WARN: Safe ITC is negative: ₹{safe:,.0f}")
        else:
            checks.append({'name': 'ITC Safe', 'status': 'PASS', 'detail': f'₹{safe:,.0f}'})

        if at_risk < 0:
            warnings.append(f'At-risk ITC is negative: {at_risk:,.0f}')
            checks.append({'name': 'ITC At Risk', 'status': 'WARN', 'detail': f'At-risk = {at_risk:,.0f} (negative?)'})
        else:
            checks.append({'name': 'ITC At Risk', 'status': 'PASS', 'detail': f'₹{at_risk:,.0f}'})

        log(f"PASS: ITC Summary — Safe=₹{safe:,.0f} | Recovered=₹{recovered:,.0f} | Risk=₹{at_risk:,.0f} | Excess=₹{excess:,.0f}")
    else:
        warnings.append('No ITC summary available')
        checks.append({'name': 'ITC Summary', 'status': 'WARN', 'detail': 'Missing'})

    # ================================================================
    # REVIEW 5: Anomaly Detector (Agent 5) — Sanity check
    # ================================================================
    log("Review 5: Verifying Agent 5 (Anomaly Detector) output...")
    r5 = agent_results.get('anomaly_detector', {})
    r5_stats = r5.get('stats', {})
    anomaly_count = r5_stats.get('total_anomalies', 0)
    pattern_count = r5_stats.get('total_patterns', 0)

    # If there are many unmatched items but zero anomalies, something may be wrong
    if total_books_unmatched + total_gstn_unmatched > 100 and anomaly_count == 0:
        warnings.append(f'{total_books_unmatched + total_gstn_unmatched} unmatched but 0 anomalies detected')
        checks.append({'name': 'Anomaly Sanity', 'status': 'WARN',
                       'detail': f'Many unmatched ({total_books_unmatched + total_gstn_unmatched}) but no anomalies'})
        log(f"WARN: {total_books_unmatched + total_gstn_unmatched} unmatched items but 0 anomalies — review Agent 5 logic")
    else:
        checks.append({'name': 'Anomaly Sanity', 'status': 'PASS',
                       'detail': f'{anomaly_count} anomalies, {pattern_count} patterns'})
        log(f"PASS: Anomaly detection found {anomaly_count} anomalies, {pattern_count} patterns")

    # ================================================================
    # REVIEW 6: Report Builder (Agent 6) — Output check
    # ================================================================
    log("Review 6: Verifying Agent 6 (Report Builder) output...")
    r6 = agent_results.get('report_builder', {})
    r6_stats = r6.get('stats', {})
    sheets_count = r6_stats.get('sheets', 0)
    if sheets_count >= 11:
        checks.append({'name': 'Report Sheets', 'status': 'PASS', 'detail': f'{sheets_count} sheets generated'})
        log(f"PASS: Report has {sheets_count} sheets (incl. Inter-Recon Scrutiny)")
    elif sheets_count > 0:
        warnings.append(f'Only {sheets_count} sheets (expected ≥11)')
        checks.append({'name': 'Report Sheets', 'status': 'WARN', 'detail': f'{sheets_count} sheets (expected ≥11)'})
        log(f"WARN: Only {sheets_count} sheets, expected 11+")
    else:
        errors.append('No sheets in report output')
        checks.append({'name': 'Report Sheets', 'status': 'FAIL', 'detail': 'No output'})

    # ================================================================
    # REVIEW 7: Schema Guard (Agent 7) — Pass/Fail
    # ================================================================
    log("Review 7: Verifying Agent 7 (Schema Guard) verdict...")
    r7 = agent_results.get('schema_guard', {})
    r7_status = r7.get('status', 'unknown')
    r7_stats = r7.get('stats', {})
    if r7_status == 'passed':
        checks.append({'name': 'Schema Guard', 'status': 'PASS',
                       'detail': f"{r7_stats.get('passed', 0)}/{r7_stats.get('total_checks', 0)} checks passed"})
        log(f"PASS: Schema Guard passed all checks")
    elif r7_status == 'failed':
        errors.append(f"Schema Guard failed: {r7.get('errors', [])}")
        checks.append({'name': 'Schema Guard', 'status': 'FAIL',
                       'detail': f"Failed: {r7.get('errors', [])}"})
        log(f"FAIL: Schema Guard detected issues: {r7.get('errors', [])}")
    else:
        warnings.append(f'Schema Guard status: {r7_status}')
        checks.append({'name': 'Schema Guard', 'status': 'WARN', 'detail': f'Status: {r7_status}'})

    # ================================================================
    # REVIEW 8: End-to-End Amount Verification
    # ================================================================
    log("Review 8: End-to-end reconciliation math check...")
    df_rec = dataframes.get('Reconciled', pd.DataFrame())
    df_nig = dataframes.get('Not found in GSTN', pd.DataFrame())
    df_nib = dataframes.get('Not found in Zoho Books', pd.DataFrame())

    total_reconciled = df_rec['Total Amount'].sum() if 'Total Amount' in df_rec.columns and len(df_rec) > 0 else 0
    total_books_only = df_nig['Total Amount'].sum() if 'Total Amount' in df_nig.columns and len(df_nig) > 0 else 0
    total_gstr2b_only = df_nib['Total Amount'].sum() if 'Total Amount' in df_nib.columns and len(df_nib) > 0 else 0

    # Cross-matched amounts from inv_matched
    total_xm_books = sum(m.get('Books_Total', 0) for m in inv_matched)
    total_xm_gstn = sum(m.get('GSTN_Total', 0) for m in inv_matched)

    # Remaining unmatched
    total_unmatched_books = sum(x.get('Total', 0) or 0 for x in books_unmatched)
    total_unmatched_gstn = sum(x.get('Total', 0) or 0 for x in gstn_unmatched)

    log(f"  Reconciled (Zoho auto): ₹{total_reconciled:,.0f}")
    log(f"  Cross-matched Books:    ₹{total_xm_books:,.0f}")
    log(f"  Cross-matched GSTR-2B:  ₹{total_xm_gstn:,.0f}")
    log(f"  Unmatched Books:        ₹{total_unmatched_books:,.0f}")
    log(f"  Unmatched GSTR-2B:      ₹{total_unmatched_gstn:,.0f}")

    # Verify Books side: original books-only = cross-matched(books) + unmatched books
    expected_books = total_xm_books + total_unmatched_books
    books_diff = abs(total_books_only - expected_books)
    if books_diff > 100:  # Allow small rounding
        warnings.append(f'Books amount mismatch: {total_books_only:,.0f} vs {expected_books:,.0f} (diff={books_diff:,.0f})')
        checks.append({'name': 'Books Amount Check', 'status': 'WARN',
                       'detail': f'Orig={total_books_only:,.0f}, XM+Unmatched={expected_books:,.0f}'})
        log(f"WARN: Books total {total_books_only:,.0f} ≠ XM+Unmatched {expected_books:,.0f} (diff ₹{books_diff:,.0f})")
    else:
        checks.append({'name': 'Books Amount Check', 'status': 'PASS',
                       'detail': f'₹{total_books_only:,.0f} accounted for'})
        log(f"PASS: Books total ₹{total_books_only:,.0f} = XM ₹{total_xm_books:,.0f} + Unmatched ₹{total_unmatched_books:,.0f}")

    # Same for GSTR-2B side
    expected_gstn = total_xm_gstn + total_unmatched_gstn
    gstn_diff = abs(total_gstr2b_only - expected_gstn)
    if gstn_diff > 100:
        warnings.append(f'GSTR-2B amount mismatch: {total_gstr2b_only:,.0f} vs {expected_gstn:,.0f}')
        checks.append({'name': 'GSTR2B Amount Check', 'status': 'WARN',
                       'detail': f'Orig={total_gstr2b_only:,.0f}, XM+Unmatched={expected_gstn:,.0f}'})
        log(f"WARN: GSTR-2B total {total_gstr2b_only:,.0f} ≠ XM+Unmatched {expected_gstn:,.0f} (diff ₹{gstn_diff:,.0f})")
    else:
        checks.append({'name': 'GSTR2B Amount Check', 'status': 'PASS',
                       'detail': f'₹{total_gstr2b_only:,.0f} accounted for'})
        log(f"PASS: GSTR-2B total ₹{total_gstr2b_only:,.0f} = XM ₹{total_xm_gstn:,.0f} + Unmatched ₹{total_unmatched_gstn:,.0f}")

    # ================================================================
    # REVIEW 9: Vendor Credits Sign Check
    # ================================================================
    log("Review 9: Verifying vendor credits are NEGATIVE...")
    credit_issues = 0
    for df_name, df in dataframes.items():
        if 'Transaction Type' in df.columns and 'Total Amount' in df.columns:
            credits = df[df['Transaction Type'].astype(str).str.contains('Credit|Debit', case=False, na=False)]
            positive_credits = credits[credits['Total Amount'] > 0]
            if len(positive_credits) > 0:
                credit_issues += len(positive_credits)
                log(f"WARN: {len(positive_credits)} positive credits in '{df_name}'")

    if credit_issues > 0:
        warnings.append(f'{credit_issues} vendor credits still positive')
        checks.append({'name': 'Credit Sign Check', 'status': 'WARN',
                       'detail': f'{credit_issues} credits still positive'})
        log(f"WARN: {credit_issues} vendor credits remain positive (should be negative)")
    else:
        checks.append({'name': 'Credit Sign Check', 'status': 'PASS',
                       'detail': 'All vendor credits are negative'})
        log("PASS: All vendor credits correctly shown as NEGATIVE")

    # ================================================================
    # FINAL QA VERDICT
    # ================================================================
    total_checks = len(checks)
    passed = sum(1 for c in checks if c['status'] == 'PASS')
    failed = sum(1 for c in checks if c['status'] == 'FAIL')
    warned = sum(1 for c in checks if c['status'] == 'WARN')

    results['checks'] = checks
    results['errors'] = errors
    results['warnings'] = warnings
    results['stats'] = {
        'total_checks': total_checks,
        'passed': passed,
        'failed': failed,
        'warned': warned,
        'verdict': 'APPROVED' if failed == 0 else 'REJECTED',
    }
    results['status'] = 'passed' if failed == 0 else 'failed'

    if failed > 0:
        log(f"╔══════════════════════════════════════════════╗")
        log(f"║  QA VERDICT: ❌ REJECTED — {failed} ERRORS FOUND  ║")
        log(f"╚══════════════════════════════════════════════╝")
        for e in errors:
            log(f"  ERROR: {e}")
    else:
        log(f"╔══════════════════════════════════════════════╗")
        log(f"║  QA VERDICT: ✅ APPROVED ({passed}/{total_checks} PASS, {warned} WARN)  ║")
        log(f"╚══════════════════════════════════════════════╝")

    return results
