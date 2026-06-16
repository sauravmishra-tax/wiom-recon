"""
AGENT 1: DATA VALIDATOR
========================
Validates input Excel structure, checks required sheets & columns,
detects data quality issues, and prepares clean DataFrames for processing.
"""
import pandas as pd
import numpy as np
from datetime import datetime

AGENT_NAME = "Data Validator"
AGENT_ID = 1
AGENT_COLOR = "#D9008D"

REQUIRED_SHEETS = {
    'Reconciled': 'Fully matched invoices',
    'Not found in GSTN': 'In Books, not in GSTR-2B',
    'Not found in Zoho Books': 'In GSTR-2B, not in Books',
    'Partially Matched': 'Partial matches with differences',
}

REQUIRED_COLS = [
    'GST Registration Number', 'Transaction Number', 'Transaction Type',
    'Transaction Date', 'Taxable Amount', 'IGST Amount', 'CGST Amount',
    'SGST Amount', 'Total Amount'
]

NUM_COLS = ['Taxable Amount', 'IGST Amount', 'CGST Amount', 'SGST Amount', 'Cess Amount', 'Total Amount']


def run(file_path, log_fn=None):
    """Run data validation agent. Returns dict with clean DataFrames + validation report."""
    results = {
        'agent': AGENT_NAME,
        'status': 'running',
        'checks': [],
        'warnings': [],
        'errors': [],
        'data': {},
        'stats': {}
    }

    def log(msg):
        if log_fn:
            log_fn(AGENT_ID, msg)

    log("Starting data validation...")

    # CHECK 1: File exists and is readable
    try:
        xl = pd.ExcelFile(file_path)
        sheet_names = xl.sheet_names
        results['checks'].append({'name': 'File Readable', 'status': 'PASS', 'detail': f'{len(sheet_names)} sheets found'})
        log(f"File loaded: {len(sheet_names)} sheets")
    except Exception as e:
        results['errors'].append(f'Cannot read file: {e}')
        results['status'] = 'failed'
        return results

    # CHECK 2: Required sheets exist
    found_sheets = {}
    for req_sheet, desc in REQUIRED_SHEETS.items():
        matches = [s for s in sheet_names if req_sheet.lower() in s.lower()]
        if matches:
            found_sheets[req_sheet] = matches[0]
            results['checks'].append({'name': f'Sheet: {req_sheet}', 'status': 'PASS', 'detail': f'Found as "{matches[0]}"'})
        else:
            results['errors'].append(f'Missing sheet: {req_sheet} ({desc})')
            results['checks'].append({'name': f'Sheet: {req_sheet}', 'status': 'FAIL', 'detail': 'Not found'})

    if len(results['errors']) > 0:
        results['status'] = 'failed'
        return results

    # CHECK 3: Load and validate each sheet
    dataframes = {}
    for key, actual_name in found_sheets.items():
        log(f"Validating sheet: {actual_name}")
        try:
            df = pd.read_excel(file_path, actual_name, header=1)
            # Check for required columns
            missing_cols = [c for c in REQUIRED_COLS if c not in df.columns]
            if missing_cols:
                # Try header row 0
                df = pd.read_excel(file_path, actual_name, header=0)
                missing_cols = [c for c in REQUIRED_COLS if c not in df.columns]

            if missing_cols:
                results['warnings'].append(f'{key}: Missing columns: {missing_cols}')

            # Clean numeric columns
            for col in NUM_COLS:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            # Parse dates
            if 'Transaction Date' in df.columns:
                df['Date'] = pd.to_datetime(df['Transaction Date'], format='%d/%m/%Y', errors='coerce')

            # Remove completely empty rows
            if 'GST Registration Number' in df.columns:
                df = df.dropna(subset=['GST Registration Number'], how='all')

            dataframes[key] = df
            results['checks'].append({
                'name': f'Data: {key}',
                'status': 'PASS',
                'detail': f'{len(df)} rows, {len(df.columns)} columns'
            })
        except Exception as e:
            results['errors'].append(f'Error loading {key}: {e}')

    # CHECK 4: Data quality checks
    log("Running data quality checks...")
    for key, df in dataframes.items():
        if 'GST Registration Number' in df.columns:
            # GSTIN format check (15 chars)
            invalid_gstin = df[df['GST Registration Number'].astype(str).str.len() != 15]
            if len(invalid_gstin) > 0:
                results['warnings'].append(f'{key}: {len(invalid_gstin)} rows with invalid GSTIN length')

            # Negative amounts (before our fix)
            if 'Total Amount' in df.columns:
                neg_count = (df['Total Amount'] < 0).sum()
                if neg_count > 0:
                    results['warnings'].append(f'{key}: {neg_count} rows already have negative Total Amount')

            # Duplicate invoice check
            if 'Transaction Number' in df.columns:
                dupes = df.duplicated(subset=['GST Registration Number', 'Transaction Number'], keep=False)
                if dupes.sum() > 0:
                    results['warnings'].append(f'{key}: {dupes.sum()} potential duplicate invoices (same GSTIN+Inv)')

    # CHECK 5: Vendor Credit sign validation
    log("Checking vendor credit signs...")
    for key, df in dataframes.items():
        if 'Transaction Type' in df.columns and 'Total Amount' in df.columns:
            credits = df[df['Transaction Type'].astype(str).str.lower().str.contains('credit|debit note', na=False)]
            positive_credits = credits[credits['Total Amount'] > 0]
            if len(positive_credits) > 0:
                results['warnings'].append(f'{key}: {len(positive_credits)} Vendor Credits with POSITIVE amounts (will be corrected to NEGATIVE)')

    # Compute stats
    results['stats'] = {
        'total_sheets': len(dataframes),
        'reconciled_count': len(dataframes.get('Reconciled', pd.DataFrame())),
        'books_only_count': len(dataframes.get('Not found in GSTN', pd.DataFrame())),
        'gstr2b_only_count': len(dataframes.get('Not found in Zoho Books', pd.DataFrame())),
        'partial_count': len(dataframes.get('Partially Matched', pd.DataFrame())) // 2,
        'total_invoices': sum(len(df) for df in dataframes.values()),
    }

    results['data'] = dataframes
    results['status'] = 'passed' if len(results['errors']) == 0 else 'failed'
    log(f"Validation complete: {results['status'].upper()} | {len(results['checks'])} checks | {len(results['warnings'])} warnings")
    return results
