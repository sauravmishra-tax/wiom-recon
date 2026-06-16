"""
AGENT 3: CROSS-MATCH ENGINE
==============================
The core intelligence - matches invoices across Books & GSTR-2B.
4-pass matching:
  Pass 1: Exact (Inv+Amt) from Not-in-GSTN vs Not-in-Books
  Pass 2: Amount match (Inv different)
  Pass 3: Taxable match (Total different)
  Pass 4: Partially Matched pairs from Zoho (ALWAYS included)
"""
import re
import pandas as pd
import numpy as np

AGENT_NAME = "Cross-Match Engine"
AGENT_ID = 3


def norm_inv(s):
    """Normalize an invoice number for fuzzy matching:
    drop spaces/slashes/dashes/dots, uppercase, strip leading zeros.
    'AIIPL/103/26-27' and 'aiipl-103-2627' -> 'AIIPL10326 27' style equality."""
    if s is None:
        return ''
    return re.sub(r'[^A-Za-z0-9]', '', str(s)).upper().lstrip('0')


def run(dataframes, vendor_map, log_fn=None):
    """
    Cross-match invoices between Not-in-GSTN (Books) and Not-in-Books (GSTR-2B).
    Also processes Partially Matched pairs from Zoho.
    4-pass matching: Exact → Amount → Taxable → Partial-Matched
    """
    results = {
        'agent': AGENT_NAME,
        'status': 'running',
        'checks': [],
        'inv_matched': [],
        'books_unmatched': [],
        'gstn_unmatched': [],
        'stats': {}
    }

    def log(msg):
        if log_fn:
            log_fn(AGENT_ID, msg)

    def get_vendor(gstin):
        if pd.isna(gstin):
            return ''
        return vendor_map.get(str(gstin).strip(), str(gstin).strip())

    df_nig = dataframes.get('Not found in GSTN', pd.DataFrame())
    df_nib = dataframes.get('Not found in Zoho Books', pd.DataFrame())
    df_pm = dataframes.get('Partially Matched', pd.DataFrame())

    books_gstins = set(df_nig['GST Registration Number'].dropna().astype(str).str.strip().unique()) if 'GST Registration Number' in df_nig.columns else set()
    gstn_gstins = set(df_nib['GST Registration Number'].dropna().astype(str).str.strip().unique()) if 'GST Registration Number' in df_nib.columns else set()
    common = books_gstins & gstn_gstins

    log(f"GSTINs: Books={len(books_gstins)}, GSTR-2B={len(gstn_gstins)}, Common={len(common)}")
    results['checks'].append({'name': 'GSTIN Overlap', 'status': 'PASS',
                               'detail': f'{len(common)} common GSTINs to cross-match'})

    inv_matched = []
    books_unmatched = []
    gstn_unmatched = []
    exact_count = 0
    fuzzy_count = 0
    amt_count = 0
    tax_count = 0
    partial_count = 0
    tax_head_mismatch_count = 0

    def detect_tax_head_mismatch(b_igst, b_cgst, b_sgst, g_igst, g_cgst, g_sgst):
        """Detect when Books has IGST but 2B has CGST+SGST or vice versa."""
        books_is_igst = abs(b_igst) > 0.01 and abs(b_cgst) < 0.01 and abs(b_sgst) < 0.01
        books_is_cgst_sgst = (abs(b_cgst) > 0.01 or abs(b_sgst) > 0.01) and abs(b_igst) < 0.01
        gstn_is_igst = abs(g_igst) > 0.01 and abs(g_cgst) < 0.01 and abs(g_sgst) < 0.01
        gstn_is_cgst_sgst = (abs(g_cgst) > 0.01 or abs(g_sgst) > 0.01) and abs(g_igst) < 0.01

        if books_is_igst and gstn_is_cgst_sgst:
            return ' | TAX HEAD MISMATCH: Books=IGST, 2B=CGST+SGST'
        elif books_is_cgst_sgst and gstn_is_igst:
            return ' | TAX HEAD MISMATCH: Books=CGST+SGST, 2B=IGST'
        return ''

    # ================================================================
    # PASS 4 FIRST: Process Zoho's Partially Matched pairs
    # These are invoices Zoho already paired but with differences
    # (e.g., WMMNSR2024-1292 - same inv, different Place of Supply)
    # ================================================================
    log("Pass 0: Processing Zoho Partially Matched pairs...")
    pm_gstins_processed = set()  # Track GSTINs+Invs already handled by Partial Match

    if len(df_pm) > 0 and 'Source' in df_pm.columns and 'GST Registration Number' in df_pm.columns:
        books_pm = df_pm[df_pm['Source'] == 'Books'].copy()
        gstn_pm = df_pm[df_pm['Source'] == 'GSTN'].copy()

        log(f"  Partially Matched: {len(books_pm)} Books rows + {len(gstn_pm)} GSTN rows")

        # Match pairs by GSTIN + Transaction Number
        for bi in books_pm.index:
            gstin = str(books_pm.loc[bi, 'GST Registration Number']).strip()
            b_inv = str(books_pm.loc[bi, 'Transaction Number']).strip()
            vendor = get_vendor(gstin)

            # Find corresponding GSTN row
            for gi in gstn_pm.index:
                g_gstin = str(gstn_pm.loc[gi, 'GST Registration Number']).strip()
                g_inv = str(gstn_pm.loc[gi, 'Transaction Number']).strip()

                if g_gstin == gstin and g_inv == b_inv:
                    # Determine match type
                    b_total = float(books_pm.loc[bi, 'Total Amount'] or 0)
                    g_total = float(gstn_pm.loc[gi, 'Total Amount'] or 0)
                    b_taxable = float(books_pm.loc[bi, 'Taxable Amount'] or 0)
                    g_taxable = float(gstn_pm.loc[gi, 'Taxable Amount'] or 0)

                    b_igst = float(books_pm.loc[bi, 'IGST Amount'] or 0)
                    g_igst = float(gstn_pm.loc[gi, 'IGST Amount'] or 0)
                    b_cgst = float(books_pm.loc[bi, 'CGST Amount'] or 0)
                    g_cgst = float(gstn_pm.loc[gi, 'CGST Amount'] or 0)
                    b_sgst = float(books_pm.loc[bi, 'SGST Amount'] or 0)
                    g_sgst = float(gstn_pm.loc[gi, 'SGST Amount'] or 0)

                    # Detect tax head mismatch (IGST vs CGST+SGST)
                    thm = detect_tax_head_mismatch(b_igst, b_cgst, b_sgst, g_igst, g_cgst, g_sgst)
                    if thm:
                        tax_head_mismatch_count += 1

                    if abs(b_total - g_total) < 1.0:
                        status = 'Reconciled'
                        remark = 'Zoho Partial Match - Inv+Amt Match (non-amount diff)'
                    elif abs(b_taxable - g_taxable) < 1.0:
                        status = 'Partially Reconciled'
                        remark = f'Zoho Partial Match - Taxable Match, Tax Diff Rs {abs(b_total-g_total):,.0f}'
                    else:
                        status = 'Partially Reconciled'
                        remark = f'Zoho Partial Match - Amount Diff Rs {abs(b_total-g_total):,.0f}'

                    remark += thm  # Append tax head mismatch if detected

                    inv_matched.append({
                        'GSTIN': gstin, 'Vendor': vendor,
                        'Books_Inv': b_inv,
                        'GSTN_Inv': g_inv,
                        'Books_Date': books_pm.loc[bi, 'Transaction Date'],
                        'GSTN_Date': gstn_pm.loc[gi, 'Transaction Date'],
                        'Books_Taxable': b_taxable,
                        'GSTN_Taxable': g_taxable,
                        'Books_IGST': b_igst,
                        'GSTN_IGST': g_igst,
                        'Books_CGST': b_cgst,
                        'GSTN_CGST': g_cgst,
                        'Books_SGST': b_sgst,
                        'GSTN_SGST': g_sgst,
                        'Books_Total': b_total,
                        'GSTN_Total': g_total,
                        'Type': books_pm.loc[bi, 'Transaction Type'] if 'Transaction Type' in books_pm.columns else 'Bill',
                        'Remark': remark,
                        'Recon_Status': status,
                    })
                    partial_count += 1
                    pm_gstins_processed.add((gstin, b_inv))
                    break

            # If no matching GSTN row found by inv, try matching by amount
            if (gstin, b_inv) not in pm_gstins_processed:
                for gi in gstn_pm.index:
                    g_gstin = str(gstn_pm.loc[gi, 'GST Registration Number']).strip()
                    if g_gstin == gstin:
                        b_total = float(books_pm.loc[bi, 'Total Amount'] or 0)
                        g_total = float(gstn_pm.loc[gi, 'Total Amount'] or 0)
                        if abs(b_total - g_total) < 1.0:
                            g_inv = str(gstn_pm.loc[gi, 'Transaction Number']).strip()
                            inv_matched.append({
                                'GSTIN': gstin, 'Vendor': vendor,
                                'Books_Inv': b_inv,
                                'GSTN_Inv': g_inv,
                                'Books_Date': books_pm.loc[bi, 'Transaction Date'],
                                'GSTN_Date': gstn_pm.loc[gi, 'Transaction Date'],
                                'Books_Taxable': float(books_pm.loc[bi, 'Taxable Amount'] or 0),
                                'GSTN_Taxable': float(gstn_pm.loc[gi, 'Taxable Amount'] or 0),
                                'Books_IGST': float(books_pm.loc[bi, 'IGST Amount'] or 0),
                                'GSTN_IGST': float(gstn_pm.loc[gi, 'IGST Amount'] or 0),
                                'Books_CGST': float(books_pm.loc[bi, 'CGST Amount'] or 0),
                                'GSTN_CGST': float(gstn_pm.loc[gi, 'CGST Amount'] or 0),
                                'Books_SGST': float(books_pm.loc[bi, 'SGST Amount'] or 0),
                                'GSTN_SGST': float(gstn_pm.loc[gi, 'SGST Amount'] or 0),
                                'Books_Total': b_total,
                                'GSTN_Total': g_total,
                                'Type': books_pm.loc[bi, 'Transaction Type'] if 'Transaction Type' in books_pm.columns else 'Bill',
                                'Remark': 'Zoho Partial Match - Amt Match, Inv Different',
                                'Recon_Status': 'Partially Reconciled',
                            })
                            partial_count += 1
                            pm_gstins_processed.add((gstin, b_inv))
                            break

        log(f"  → {partial_count} Zoho Partially Matched pairs processed")
    else:
        log("  No Partially Matched data or missing Source column")

    results['checks'].append({'name': 'Zoho Partial Pairs', 'status': 'PASS',
                               'detail': f'{partial_count} pairs from Zoho Partially Matched'})

    # ================================================================
    # PASSES 1-3: Process Not-in-GSTN vs Not-in-Books
    # ================================================================
    log("Pass 1-3: Cross-matching Not-in-GSTN vs Not-in-Books...")

    for gstin in sorted(common):
        b_df = df_nig[df_nig['GST Registration Number'].astype(str).str.strip() == gstin].copy()
        g_df = df_nib[df_nib['GST Registration Number'].astype(str).str.strip() == gstin].copy()
        vendor = get_vendor(gstin)
        b_used = set()
        g_used = set()

        def make_match(bi, gi, remark, status):
            nonlocal tax_head_mismatch_count
            b_igst = float(b_df.loc[bi, 'IGST Amount'] or 0)
            g_igst = float(g_df.loc[gi, 'IGST Amount'] or 0)
            b_cgst = float(b_df.loc[bi, 'CGST Amount'] or 0)
            g_cgst = float(g_df.loc[gi, 'CGST Amount'] or 0)
            b_sgst = float(b_df.loc[bi, 'SGST Amount'] or 0)
            g_sgst = float(g_df.loc[gi, 'SGST Amount'] or 0)
            thm = detect_tax_head_mismatch(b_igst, b_cgst, b_sgst, g_igst, g_cgst, g_sgst)
            if thm:
                tax_head_mismatch_count += 1
                remark += thm
            return {
                'GSTIN': gstin, 'Vendor': vendor,
                'Books_Inv': str(b_df.loc[bi, 'Transaction Number']).strip(),
                'GSTN_Inv': str(g_df.loc[gi, 'Transaction Number']).strip(),
                'Books_Date': b_df.loc[bi, 'Transaction Date'],
                'GSTN_Date': g_df.loc[gi, 'Transaction Date'],
                'Books_Taxable': b_df.loc[bi, 'Taxable Amount'],
                'GSTN_Taxable': g_df.loc[gi, 'Taxable Amount'],
                'Books_IGST': b_igst,
                'GSTN_IGST': g_igst,
                'Books_CGST': b_cgst,
                'GSTN_CGST': g_cgst,
                'Books_SGST': b_sgst,
                'GSTN_SGST': g_sgst,
                'Books_Total': b_df.loc[bi, 'Total Amount'],
                'GSTN_Total': g_df.loc[gi, 'Total Amount'],
                'Type': b_df.loc[bi, 'Transaction Type'],
                'Remark': remark,
                'Recon_Status': status,
            }

        # PASS 1: Exact invoice + amount match
        for bi in b_df.index:
            for gi in g_df.index:
                if gi not in g_used and bi not in b_used:
                    b_inv = str(b_df.loc[bi, 'Transaction Number']).strip()
                    g_inv = str(g_df.loc[gi, 'Transaction Number']).strip()
                    if b_inv == g_inv and abs(b_df.loc[bi, 'Total Amount'] - g_df.loc[gi, 'Total Amount']) < 1.0:
                        b_used.add(bi)
                        g_used.add(gi)
                        inv_matched.append(make_match(bi, gi, 'Fully Reconciled - Exact Match', 'Reconciled'))
                        exact_count += 1

        # PASS 1.5: Fuzzy invoice match — same invoice, different formatting
        # (e.g. AIIPL/103/26-27 vs AIIPL-103-2627). Strong signal: same bill.
        for bi in b_df.index:
            if bi in b_used:
                continue
            nb = norm_inv(b_df.loc[bi, 'Transaction Number'])
            if not nb:
                continue
            for gi in g_df.index:
                if gi in g_used:
                    continue
                if nb == norm_inv(g_df.loc[gi, 'Transaction Number']):
                    b_used.add(bi)
                    g_used.add(gi)
                    if abs(b_df.loc[bi, 'Total Amount'] - g_df.loc[gi, 'Total Amount']) < 1.0:
                        inv_matched.append(make_match(bi, gi, 'Fully Reconciled - Inv Match (format diff)', 'Reconciled'))
                    else:
                        inv_matched.append(make_match(bi, gi, 'Partially Reconciled - Inv Match, Amt Diff', 'Partially Reconciled'))
                    fuzzy_count += 1
                    break

        # PASS 2: Amount match, invoice different
        for bi in b_df.index:
            if bi in b_used:
                continue
            for gi in g_df.index:
                if gi in g_used:
                    continue
                if abs(b_df.loc[bi, 'Total Amount'] - g_df.loc[gi, 'Total Amount']) < 1.0:
                    b_used.add(bi)
                    g_used.add(gi)
                    inv_matched.append(make_match(bi, gi, 'Partially Reconciled - Amt Match, Inv Mismatch', 'Partially Reconciled'))
                    amt_count += 1
                    break  # one Books invoice matches at most one 2B row

        # PASS 3: Taxable match, total differs
        for bi in b_df.index:
            if bi in b_used:
                continue
            for gi in g_df.index:
                if gi in g_used:
                    continue
                if abs(b_df.loc[bi, 'Taxable Amount'] - g_df.loc[gi, 'Taxable Amount']) < 1.0 and abs(b_df.loc[bi, 'Total Amount'] - g_df.loc[gi, 'Total Amount']) >= 1.0:
                    b_used.add(bi)
                    g_used.add(gi)
                    inv_matched.append(make_match(bi, gi, 'Partially Reconciled - Taxable Match, Tax Diff', 'Partially Reconciled'))
                    tax_count += 1
                    break  # one Books invoice matches at most one 2B row

        # Unmatched from this GSTIN
        for bi in b_df.index:
            if bi not in b_used:
                books_unmatched.append({
                    'GSTIN': gstin, 'Vendor': vendor,
                    'Inv': b_df.loc[bi, 'Transaction Number'], 'Type': b_df.loc[bi, 'Transaction Type'],
                    'Date': b_df.loc[bi, 'Transaction Date'],
                    'Taxable': b_df.loc[bi, 'Taxable Amount'], 'IGST': b_df.loc[bi, 'IGST Amount'],
                    'CGST': b_df.loc[bi, 'CGST Amount'], 'SGST': b_df.loc[bi, 'SGST Amount'],
                    'Total': b_df.loc[bi, 'Total Amount'],
                    'GSTIN_in_2B': 'Yes',
                    'Remark': 'GSTIN in 2B but invoice not found',
                    'Action': 'Check inv no. format with vendor'
                })
        for gi in g_df.index:
            if gi not in g_used:
                gstn_unmatched.append({
                    'GSTIN': gstin, 'Vendor': vendor,
                    'Inv': g_df.loc[gi, 'Transaction Number'], 'Type': g_df.loc[gi, 'Transaction Type'],
                    'Date': g_df.loc[gi, 'Transaction Date'],
                    'Taxable': g_df.loc[gi, 'Taxable Amount'], 'IGST': g_df.loc[gi, 'IGST Amount'],
                    'CGST': g_df.loc[gi, 'CGST Amount'], 'SGST': g_df.loc[gi, 'SGST Amount'],
                    'Total': g_df.loc[gi, 'Total Amount'],
                    'GSTIN_in_Books': 'Yes',
                    'Remark': 'GSTIN in Books but invoice unrecorded',
                    'Action': 'Book in Zoho if valid purchase'
                })

    # GSTINs only in Books
    for gstin in sorted(books_gstins - gstn_gstins):
        for _, r in df_nig[df_nig['GST Registration Number'].astype(str).str.strip() == gstin].iterrows():
            books_unmatched.append({
                'GSTIN': gstin, 'Vendor': get_vendor(gstin),
                'Inv': r['Transaction Number'], 'Type': r['Transaction Type'],
                'Date': r['Transaction Date'],
                'Taxable': r['Taxable Amount'], 'IGST': r['IGST Amount'],
                'CGST': r['CGST Amount'], 'SGST': r['SGST Amount'],
                'Total': r['Total Amount'],
                'GSTIN_in_2B': 'No',
                'Remark': 'GSTIN NOT in GSTR-2B - Vendor not filed',
                'Action': 'URGENT: Follow up for GSTR-1 filing'
            })

    # GSTINs only in GSTR-2B
    for gstin in sorted(gstn_gstins - books_gstins):
        for _, r in df_nib[df_nib['GST Registration Number'].astype(str).str.strip() == gstin].iterrows():
            gstn_unmatched.append({
                'GSTIN': gstin, 'Vendor': get_vendor(gstin),
                'Inv': r['Transaction Number'], 'Type': r['Transaction Type'],
                'Date': r['Transaction Date'],
                'Taxable': r['Taxable Amount'], 'IGST': r['IGST Amount'],
                'CGST': r['CGST Amount'], 'SGST': r['SGST Amount'],
                'Total': r['Total Amount'],
                'GSTIN_in_Books': 'No',
                'Remark': 'GSTIN NOT in Books - Unknown vendor',
                'Action': 'Investigate: Book if valid, REJECT if unknown'
            })

    total_expert = exact_count + amt_count + tax_count
    log(f"Cross-matching done:")
    log(f"  Zoho Partial Pairs: {partial_count}")
    log(f"  Expert: {exact_count} exact + {amt_count} amt-match + {tax_count} taxable-match = {total_expert}")
    log(f"  Tax Head Mismatches (IGST vs CGST+SGST): {tax_head_mismatch_count}")
    log(f"  Grand Total Matched: {len(inv_matched)} | Books Unmatched: {len(books_unmatched)} | GSTR-2B Unmatched: {len(gstn_unmatched)}")

    results['inv_matched'] = inv_matched
    results['books_unmatched'] = books_unmatched
    results['gstn_unmatched'] = gstn_unmatched
    results['books_gstins'] = books_gstins
    results['gstn_gstins'] = gstn_gstins
    results['checks'].append({'name': 'Exact Match (Inv+Amt)', 'status': 'PASS', 'detail': f'{exact_count} invoices'})
    results['checks'].append({'name': 'Amount Match (Inv Diff)', 'status': 'PASS', 'detail': f'{amt_count} invoices'})
    results['checks'].append({'name': 'Taxable Match (Tax Diff)', 'status': 'PASS', 'detail': f'{tax_count} invoices'})
    results['checks'].append({'name': 'Tax Head Mismatch', 'status': 'WARN' if tax_head_mismatch_count > 0 else 'PASS',
                               'detail': f'{tax_head_mismatch_count} IGST↔CGST+SGST mismatches'})
    results['checks'].append({'name': 'Books Unmatched', 'status': 'WARN', 'detail': f'{len(books_unmatched)} invoices'})
    results['checks'].append({'name': 'GSTR-2B Unmatched', 'status': 'WARN', 'detail': f'{len(gstn_unmatched)} invoices'})
    results['stats'] = {
        'exact_matched': exact_count,
        'fuzzy_matched': fuzzy_count,
        'amt_matched': amt_count,
        'taxable_matched': tax_count,
        'partial_matched': partial_count,
        'total_matched': len(inv_matched),
        'books_unmatched': len(books_unmatched),
        'gstn_unmatched': len(gstn_unmatched),
        'books_gstin_missing': len([x for x in books_unmatched if x.get('GSTIN_in_2B') == 'No']),
        'gstn_gstin_unknown': len([x for x in gstn_unmatched if x.get('GSTIN_in_Books') == 'No']),
        'tax_head_mismatch': tax_head_mismatch_count,
    }
    results['status'] = 'passed'
    return results
