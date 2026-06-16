"""
AGENT 4: ITC RISK ANALYZER
============================
Calculates ITC impact for each reconciliation category,
identifies at-risk ITC, excess ITC, and net gap.
"""
import pandas as pd
import numpy as np

AGENT_NAME = "ITC Risk Analyzer"
AGENT_ID = 4


def run(dataframes, inv_matched, books_unmatched, gstn_unmatched, log_fn=None):
    """Analyze ITC risk across all reconciliation categories."""
    results = {
        'agent': AGENT_NAME,
        'status': 'running',
        'checks': [],
        'itc_summary': {},
        'risk_vendors': [],
        'stats': {}
    }

    def log(msg):
        if log_fn:
            log_fn(AGENT_ID, msg)

    log("Analyzing ITC impact...")

    df_rec = dataframes.get('Reconciled', pd.DataFrame())

    # Category 1: Fully Reconciled (safe ITC)
    safe_tax = df_rec['IGST Amount'].sum() + df_rec['CGST Amount'].sum() + df_rec['SGST Amount'].sum() if len(df_rec) > 0 else 0

    # Category 2: Expert cross-matched
    expert_exact = [x for x in inv_matched if x['Recon_Status'] == 'Reconciled']
    expert_partial = [x for x in inv_matched if x['Recon_Status'] == 'Partially Reconciled']
    expert_tax = sum((x.get('Books_IGST', 0) or 0) + (x.get('Books_CGST', 0) or 0) + (x.get('Books_SGST', 0) or 0) for x in inv_matched)

    # Category 3: At Risk (in Books, not in 2B)
    risk_tax = sum((x.get('IGST', 0) or 0) + (x.get('CGST', 0) or 0) + (x.get('SGST', 0) or 0) for x in books_unmatched)
    risk_total = sum(x.get('Total', 0) or 0 for x in books_unmatched if pd.notna(x.get('Total')))

    # Category 4: Excess (in 2B, not in Books)
    excess_tax = sum((x.get('IGST', 0) or 0) + (x.get('CGST', 0) or 0) + (x.get('SGST', 0) or 0) for x in gstn_unmatched)
    excess_total = sum(x.get('Total', 0) or 0 for x in gstn_unmatched if pd.notna(x.get('Total')))

    # CRITICAL: GSTINs not in 2B at all
    critical_items = [x for x in books_unmatched if x.get('GSTIN_in_2B') == 'No']
    critical_tax = sum((x.get('IGST', 0) or 0) + (x.get('CGST', 0) or 0) + (x.get('SGST', 0) or 0) for x in critical_items)

    itc_summary = {
        'safe_itc': safe_tax,
        'expert_recovered': expert_tax,
        'at_risk_total': risk_tax,
        'at_risk_critical': critical_tax,
        'excess_available': excess_tax,
        'net_gap': risk_tax - excess_tax,
    }

    results['itc_summary'] = itc_summary
    results['checks'].append({'name': 'Safe ITC', 'status': 'PASS', 'detail': f'Rs {safe_tax:,.0f}'})
    results['checks'].append({'name': 'Expert Recovered', 'status': 'PASS', 'detail': f'Rs {expert_tax:,.0f}'})
    results['checks'].append({'name': 'ITC at Risk', 'status': 'WARN' if risk_tax > 0 else 'PASS', 'detail': f'Rs {risk_tax:,.0f}'})
    results['checks'].append({'name': 'Critical (Vendor not filed)', 'status': 'FAIL' if critical_tax > 10000 else 'PASS', 'detail': f'Rs {critical_tax:,.0f} ({len(critical_items)} invoices)'})
    results['checks'].append({'name': 'Excess ITC Available', 'status': 'PASS', 'detail': f'Rs {excess_tax:,.0f}'})
    results['checks'].append({'name': 'Net ITC Gap', 'status': 'WARN' if itc_summary['net_gap'] > 0 else 'PASS', 'detail': f'Rs {itc_summary["net_gap"]:,.0f}'})

    # Top risk vendors
    log("Identifying top risk vendors...")
    vendor_risk = {}
    for item in books_unmatched:
        v = item.get('Vendor', item.get('GSTIN', ''))
        if v not in vendor_risk:
            vendor_risk[v] = {'gstin': item['GSTIN'], 'count': 0, 'total': 0, 'tax': 0, 'critical': item.get('GSTIN_in_2B') == 'No'}
        vendor_risk[v]['count'] += 1
        vendor_risk[v]['total'] += item.get('Total', 0) or 0
        vendor_risk[v]['tax'] += (item.get('IGST', 0) or 0) + (item.get('CGST', 0) or 0) + (item.get('SGST', 0) or 0)

    top_risk = sorted(vendor_risk.items(), key=lambda x: abs(x[1]['tax']), reverse=True)[:20]
    results['risk_vendors'] = top_risk
    results['checks'].append({'name': 'Top Risk Vendors', 'status': 'PASS', 'detail': f'{len(top_risk)} vendors identified'})

    results['stats'] = {
        'safe_itc': safe_tax,
        'expert_recovered': expert_tax,
        'at_risk': risk_tax,
        'critical_risk': critical_tax,
        'excess': excess_tax,
        'net_gap': itc_summary['net_gap'],
        'risk_vendor_count': len(vendor_risk),
    }
    results['status'] = 'passed'
    log(f"ITC Analysis: Safe={safe_tax:,.0f} | Risk={risk_tax:,.0f} | Excess={excess_tax:,.0f} | Gap={itc_summary['net_gap']:,.0f}")
    return results
