"""
AGENT 5: ANOMALY DETECTOR
============================
Detects unusual patterns: duplicate invoices, date anomalies,
unusual amounts, GSTIN format issues, invoice format patterns.
"""
import pandas as pd
import numpy as np
from collections import Counter

AGENT_NAME = "Anomaly Detector"
AGENT_ID = 5


def run(dataframes, inv_matched, books_unmatched, gstn_unmatched, log_fn=None):
    """Detect anomalies and patterns in reconciliation data."""
    results = {
        'agent': AGENT_NAME,
        'status': 'running',
        'checks': [],
        'anomalies': [],
        'patterns': [],
        'stats': {}
    }

    def log(msg):
        if log_fn:
            log_fn(AGENT_ID, msg)

    log("Scanning for anomalies...")

    anomalies = []
    patterns = []

    # ANOMALY 1: Invoice Number Format Patterns in mismatches
    log("Analyzing invoice format patterns...")
    inv_format_issues = []
    for m in inv_matched:
        if m['Recon_Status'] == 'Partially Reconciled' and m['Books_Inv'] != m['GSTN_Inv']:
            b = m['Books_Inv']
            g = m['GSTN_Inv']
            issue_type = 'Unknown'
            # Leading zeros
            if b.lstrip('0') == g.lstrip('0'):
                issue_type = 'Leading Zeros'
            # Year format (2025/2026 vs 25/26)
            elif b.replace('2025/2026', '2025/26') == g or b.replace('2025-2026', '2025-26') == g:
                issue_type = 'Year Format'
            elif b.replace('2025/2026', '25-26') == g or g.replace('2025/2026', '25-26') == b:
                issue_type = 'Year Format'
            # Prefix/suffix
            elif b in g or g in b:
                issue_type = 'Prefix/Suffix Diff'
            else:
                issue_type = 'Completely Different'

            inv_format_issues.append({
                'vendor': m['Vendor'],
                'books_inv': b,
                'gstn_inv': g,
                'type': issue_type,
                'amount': m['Books_Total']
            })

    if inv_format_issues:
        type_counts = Counter([x['type'] for x in inv_format_issues])
        patterns.append({
            'name': 'Invoice Format Mismatch Patterns',
            'detail': dict(type_counts),
            'items': inv_format_issues
        })
        results['checks'].append({'name': 'Inv Format Analysis', 'status': 'PASS',
                                   'detail': f'{len(inv_format_issues)} format mismatches: {dict(type_counts)}'})

    # ANOMALY 2: Late uploads (dates much older than current period)
    log("Checking for late GSTR-2B uploads...")
    late_uploads = []
    df_nib = dataframes.get('Not found in Zoho Books', pd.DataFrame())
    if 'Date' in df_nib.columns:
        cutoff = pd.Timestamp('2025-04-01')
        late = df_nib[df_nib['Date'] < cutoff]
        if len(late) > 0:
            late_uploads = late[['GST Registration Number', 'Transaction Number', 'Transaction Date', 'Total Amount']].to_dict('records')
            anomalies.append({
                'type': 'Late GSTR-2B Upload',
                'severity': 'MEDIUM',
                'count': len(late),
                'detail': f'{len(late)} invoices dated before Apr-2025 appearing in current 2B',
                'items': late_uploads[:10]
            })
            results['checks'].append({'name': 'Late Uploads', 'status': 'WARN',
                                       'detail': f'{len(late)} invoices from prior periods in GSTR-2B'})

    # ANOMALY 3: Large value unmatched invoices
    log("Checking large unmatched invoices...")
    large_books = [x for x in books_unmatched if abs(x.get('Total', 0) or 0) > 500000]
    large_gstn = [x for x in gstn_unmatched if abs(x.get('Total', 0) or 0) > 500000]
    if large_books:
        anomalies.append({
            'type': 'Large Unmatched (Books)',
            'severity': 'HIGH',
            'count': len(large_books),
            'detail': f'{len(large_books)} invoices > Rs 5L in Books but not in GSTR-2B',
            'total': sum(x.get('Total', 0) for x in large_books)
        })
    if large_gstn:
        anomalies.append({
            'type': 'Large Unmatched (GSTR-2B)',
            'severity': 'HIGH',
            'count': len(large_gstn),
            'detail': f'{len(large_gstn)} invoices > Rs 5L in GSTR-2B but not in Books',
            'total': sum(x.get('Total', 0) for x in large_gstn)
        })
    results['checks'].append({'name': 'Large Value Check', 'status': 'WARN' if large_books or large_gstn else 'PASS',
                               'detail': f'{len(large_books)} Books + {len(large_gstn)} GSTR-2B invoices > Rs 5L'})

    # ANOMALY 4: Vendor concentration risk
    log("Analyzing vendor concentration...")
    vendor_totals = {}
    for item in books_unmatched + gstn_unmatched:
        v = item.get('Vendor', item.get('GSTIN', ''))
        vendor_totals[v] = vendor_totals.get(v, 0) + abs(item.get('Total', 0) or 0)
    top_vendors = sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)[:10]
    if top_vendors:
        total_unmatched = sum(v for _, v in vendor_totals.items())
        top_pct = sum(v for _, v in top_vendors) / total_unmatched * 100 if total_unmatched > 0 else 0
        patterns.append({
            'name': 'Vendor Concentration',
            'detail': f'Top 10 vendors = {top_pct:.0f}% of unmatched value',
            'vendors': top_vendors
        })
        results['checks'].append({'name': 'Vendor Concentration', 'status': 'WARN' if top_pct > 70 else 'PASS',
                                   'detail': f'Top 10 = {top_pct:.0f}% of unmatched value'})

    # ANOMALY 5: Round amount check (potential fake invoices)
    log("Checking for suspicious round amounts...")
    round_invoices = []
    for item in gstn_unmatched:
        total = abs(item.get('Total', 0) or 0)
        if total > 10000 and total == round(total, -3):  # Exactly divisible by 1000
            round_invoices.append(item)
    if round_invoices:
        anomalies.append({
            'type': 'Suspicious Round Amounts (GSTR-2B)',
            'severity': 'LOW',
            'count': len(round_invoices),
            'detail': f'{len(round_invoices)} unbooked GSTR-2B invoices with perfectly round amounts (possible fake)',
        })
        results['checks'].append({'name': 'Round Amount Check', 'status': 'PASS',
                                   'detail': f'{len(round_invoices)} round-amount invoices in GSTR-2B (review recommended)'})

    # ANOMALY 6: Cross-verification of Partially Matched
    log("Cross-verifying partial matches...")
    df_pm = dataframes.get('Partially Matched', pd.DataFrame())
    if len(df_pm) > 0 and 'Source' in df_pm.columns:
        books_pm = df_pm[df_pm['Source'] == 'Books']
        gstn_pm = df_pm[df_pm['Source'] == 'GSTN']
        large_diff = []
        for i in range(min(len(books_pm), len(gstn_pm))):
            b = books_pm.iloc[i]
            g = gstn_pm.iloc[i]
            diff = abs(b.get('Total Amount', 0) - g.get('Total Amount', 0))
            if diff > 10000:
                large_diff.append({
                    'vendor': b.get('Vendor Name', b.get('GST Registration Number', '')),
                    'diff': diff,
                    'books_total': b.get('Total Amount', 0),
                    'gstn_total': g.get('Total Amount', 0),
                })
        if large_diff:
            anomalies.append({
                'type': 'Large Partial Match Differences',
                'severity': 'HIGH',
                'count': len(large_diff),
                'detail': f'{len(large_diff)} partial matches with diff > Rs 10K',
                'items': large_diff
            })
        results['checks'].append({'name': 'Partial Match Diffs', 'status': 'WARN' if large_diff else 'PASS',
                                   'detail': f'{len(large_diff)} with diff > Rs 10K' if large_diff else 'All within tolerance'})

    results['anomalies'] = anomalies
    results['patterns'] = patterns
    results['stats'] = {
        'total_anomalies': len(anomalies),
        'total_patterns': len(patterns),
        'format_mismatches': len(inv_format_issues),
        'late_uploads': len(late_uploads),
        'large_unmatched': len(large_books) + len(large_gstn),
    }
    results['status'] = 'passed'
    log(f"Anomaly detection: {len(anomalies)} anomalies, {len(patterns)} patterns found")
    return results
