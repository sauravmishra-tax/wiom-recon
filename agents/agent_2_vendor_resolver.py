"""
AGENT 2: VENDOR NAME RESOLVER
===============================
Builds master GSTIN-to-Vendor mapping from all available sources:
Zoho Books sheets, GST portal cache, and flags missing names.
Also applies Vendor Credit sign correction (NEGATIVE).
"""
import pandas as pd
import json
import os

AGENT_NAME = "Vendor Name Resolver"
AGENT_ID = 2

GST_CACHE_PATH = r'C:\Users\Saurav Mishra\OneDrive\Desktop\Claude\GSTIN CHECK\gst_cache.json'
NUM_COLS = ['Taxable Amount', 'IGST Amount', 'CGST Amount', 'SGST Amount', 'Cess Amount', 'Total Amount']


def run(dataframes, log_fn=None):
    """
    Build vendor master + fix vendor credit signs.
    Input: dict of DataFrames from Agent 1
    Output: vendor_map, sign-corrected DataFrames, stats
    """
    results = {
        'agent': AGENT_NAME,
        'status': 'running',
        'checks': [],
        'vendor_map': {},
        'data': {},
        'stats': {}
    }

    def log(msg):
        if log_fn:
            log_fn(AGENT_ID, msg)

    log("Building vendor name master...")

    vendor_map = {}

    # Source 1: Reconciled sheet (most reliable - already matched)
    for key in ['Reconciled', 'Not found in GSTN']:
        df = dataframes.get(key, pd.DataFrame())
        if 'Vendor Name' in df.columns:
            for _, r in df.iterrows():
                g = r.get('GST Registration Number')
                v = r.get('Vendor Name', '')
                if pd.notna(g) and pd.notna(v) and str(v).strip():
                    vendor_map[str(g).strip()] = str(v).strip()

    src1_count = len(vendor_map)
    results['checks'].append({'name': 'Zoho Books Names', 'status': 'PASS', 'detail': f'{src1_count} vendors from Books'})
    log(f"Source 1 (Zoho Books): {src1_count} vendor names")

    # Source 2: Partially Matched
    df_pm = dataframes.get('Partially Matched', pd.DataFrame())
    if 'Vendor Name' in df_pm.columns:
        for _, r in df_pm.iterrows():
            g = r.get('GST Registration Number')
            v = r.get('Vendor Name', '')
            if pd.notna(g) and pd.notna(v) and str(v).strip() and str(g).strip() not in vendor_map:
                vendor_map[str(g).strip()] = str(v).strip()

    src2_count = len(vendor_map) - src1_count
    log(f"Source 2 (Partial Match): {src2_count} additional names")

    # Source 3: GST Portal Cache
    gst_cache = {}
    cache_count = 0
    try:
        if os.path.exists(GST_CACHE_PATH):
            with open(GST_CACHE_PATH, 'r') as f:
                gst_cache = json.load(f)
            for gstin, info in gst_cache.items():
                if gstin not in vendor_map:
                    data = info.get('data', {})
                    name = data.get('tradeNam', '') or data.get('lgnm', '')
                    if name:
                        vendor_map[gstin] = name
                        cache_count += 1
            results['checks'].append({'name': 'GST Portal Cache', 'status': 'PASS', 'detail': f'{cache_count} from cache, {len(gst_cache)} total cached'})
            log(f"Source 3 (GST Cache): {cache_count} additional names from {len(gst_cache)} cached GSTINs")
    except Exception as e:
        results['checks'].append({'name': 'GST Portal Cache', 'status': 'WARN', 'detail': f'Cache not available: {e}'})
        log(f"GST Cache not available: {e}")

    # Identify all unique GSTINs across all sheets
    all_gstins = set()
    for key, df in dataframes.items():
        if 'GST Registration Number' in df.columns:
            all_gstins.update(df['GST Registration Number'].dropna().astype(str).str.strip().unique())

    missing_count = len(all_gstins) - len(set(all_gstins) & set(vendor_map.keys()))
    results['checks'].append({'name': 'Vendor Coverage', 'status': 'PASS' if missing_count < 50 else 'WARN',
                               'detail': f'{len(vendor_map)}/{len(all_gstins)} GSTINs have names ({missing_count} missing)'})

    # ---- FIX VENDOR CREDIT SIGNS ----
    log("Fixing vendor credit signs (making NEGATIVE)...")
    corrected_dfs = {}
    total_corrections = 0

    for key, df in dataframes.items():
        df = df.copy()
        if 'Transaction Type' in df.columns:
            for idx in df.index:
                tt = str(df.loc[idx, 'Transaction Type']).lower()
                if 'credit' in tt or 'debit note' in tt:
                    for col in NUM_COLS:
                        if col in df.columns:
                            val = df.loc[idx, col]
                            if pd.notna(val) and val > 0:
                                df.loc[idx, col] = -abs(val)
                                total_corrections += 1
        corrected_dfs[key] = df

    results['checks'].append({'name': 'Vendor Credit Fix', 'status': 'PASS',
                               'detail': f'{total_corrections} cells corrected to NEGATIVE'})
    log(f"Vendor credit fix: {total_corrections} cells corrected to negative")

    results['vendor_map'] = vendor_map
    results['gst_cache'] = gst_cache
    results['all_gstins'] = sorted(all_gstins)
    results['data'] = corrected_dfs
    results['stats'] = {
        'total_gstins': len(all_gstins),
        'names_found': len(vendor_map),
        'names_missing': missing_count,
        'from_books': src1_count,
        'from_cache': cache_count,
        'credit_corrections': total_corrections,
    }
    results['status'] = 'passed'
    log(f"Vendor resolution complete: {len(vendor_map)} names, {missing_count} missing")
    return results
