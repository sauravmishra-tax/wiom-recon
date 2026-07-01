"""
Zoho Books — live vendor master fetch.

One-time setup (by the account owner, in the app's Settings page):
  Client ID, Client Secret, Refresh Token, Organization ID, Region (.in/.com/.eu/...).
After that the app auto-mints short-lived access tokens from the long-lived
refresh token — no recurring human involvement.

Zero external dependencies (uses urllib) so it runs anywhere.
"""
import json
import urllib.parse
import urllib.request

# Region -> (accounts domain, api domain). Default India.
REGIONS = {
    'in': ('https://accounts.zoho.in', 'https://www.zohoapis.in'),
    'com': ('https://accounts.zoho.com', 'https://www.zohoapis.com'),
    'eu': ('https://accounts.zoho.eu', 'https://www.zohoapis.eu'),
    'au': ('https://accounts.zoho.com.au', 'https://www.zohoapis.com.au'),
    'jp': ('https://accounts.zoho.jp', 'https://www.zohoapis.jp'),
}


def _domains(region):
    return REGIONS.get((region or 'in').lower().lstrip('.'), REGIONS['in'])


def _http(url, data=None, headers=None, method='GET', timeout=30):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def exchange_code_for_refresh_token(client_id, client_secret, code, redirect_uri, region='in'):
    """One-time: trade an OAuth authorization code (from the consent redirect)
    for a long-lived refresh token. The code is single-use and expires in
    ~10 minutes, so call this immediately after the user authorizes."""
    accounts, _ = _domains(region)
    url = f'{accounts}/oauth/v2/token'
    out = _http(url, data={
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'code': code,
    }, method='POST')
    tok = out.get('refresh_token')
    if not tok:
        raise RuntimeError(f"Zoho code exchange error: {out.get('error', out)}")
    return tok


def get_access_token(client_id, client_secret, refresh_token, region='in'):
    """Mint a short-lived access token from the long-lived refresh token."""
    accounts, _ = _domains(region)
    url = f'{accounts}/oauth/v2/token'
    out = _http(url, data={
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token',
    }, method='POST')
    tok = out.get('access_token')
    if not tok:
        raise RuntimeError(f"Zoho token error: {out.get('error', out)}")
    return tok


def fetch_vendors(access_token, org_id, region='in'):
    """Page through Zoho Books contacts and return [{gstin, name, state}]."""
    _, api = _domains(region)
    headers = {'Authorization': f'Zoho-oauthtoken {access_token}'}
    vendors, page = [], 1
    while True:
        qs = urllib.parse.urlencode({
            'organization_id': org_id, 'page': page, 'per_page': 200,
        })
        out = _http(f'{api}/books/v3/contacts?{qs}', headers=headers)
        for c in out.get('contacts', []):
            gstin = (c.get('gst_no') or '').strip()
            if not gstin:
                continue
            name = (c.get('company_name') or c.get('contact_name') or '').strip()
            vendors.append({'gstin': gstin, 'name': name,
                            'email': (c.get('email') or '').strip(),
                            'state': (c.get('place_of_contact') or '').strip()})
        pc = out.get('page_context', {})
        if not pc.get('has_more_page'):
            break
        page += 1
        if page > 100:  # safety cap (~20k contacts)
            break
    return vendors


def _http_json(url, payload=None, headers=None, method='PUT', timeout=20):
    """Send JSON body (PUT/POST) to Zoho API."""
    import json as _json
    body = _json.dumps(payload).encode() if payload else None
    h = dict(headers or {})
    if body:
        h['Content-Type'] = 'application/json; charset=UTF-8'
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors='replace')
        raise RuntimeError(f"Zoho HTTP {e.code}: {body_txt[:400]}")


def gstr2b_itc_action(access_token, org_id, region, return_period,
                      vendor_gstin, document_number, action):
    """Mark a GSTR-2B entry as ITC-eligible (accept) or ITC-ineligible (reject).

    action: 'accept' or 'reject'
    return_period: 'YYYY-MM'

    Zoho Books API flow:
      1. GET /books/v3/gstreturn/gstr2b  →  find the entry by vendor_gstin + document_number
      2. PUT /books/v3/gstreturn/gstr2b/{entry_id}/action  →  update status
    Returns (ok: bool, message: str, zoho_id: str|None)
    """
    _, api = _domains(region)
    hdrs = {'Authorization': f'Zoho-oauthtoken {access_token}'}

    # Convert YYYY-MM → MMYYYY for Zoho (e.g. 2026-05 → 052026)
    try:
        yr, mo = return_period.split('-')
        zoho_period = f'{mo}{yr}'
    except Exception:
        zoho_period = return_period

    # Step 1: search for the entry
    qs = urllib.parse.urlencode({'organization_id': org_id, 'return_period': zoho_period,
                                 'vendor_gstin': vendor_gstin})
    data = _http(f'{api}/books/v3/gstreturn/gstr2b?{qs}', headers=hdrs)
    entries = data.get('gstr2b', []) or data.get('transactions', []) or []
    entry_id = None
    doc_norm = (document_number or '').strip().upper()
    for e in entries:
        doc = (e.get('document_number') or e.get('invoice_number') or '').strip().upper()
        if doc == doc_norm:
            entry_id = e.get('gstr2b_id') or e.get('transaction_id') or e.get('id')
            break

    if not entry_id:
        return False, f'Entry not found in Zoho GSTR-2B for period {zoho_period}, doc {document_number}', None

    # Step 2: update action
    zoho_action = 'mark_as_itc_ineligible' if action == 'reject' else 'reconcile'
    url = f'{api}/books/v3/gstreturn/gstr2b/{entry_id}/action?organization_id={org_id}'
    result = _http_json(url, {'action': zoho_action}, headers=hdrs, method='PUT')
    if result.get('code') == 0:
        return True, result.get('message', 'Updated in Zoho.'), entry_id
    return False, result.get('message', str(result)), entry_id


def fetch_gstr2b(access_token, org_id, region, return_period):
    """Fetch GSTR-2B reconciliation data from Zoho Books for a given period.

    return_period: 'YYYY-MM'  →  converts to MMYYYY for Zoho API
    Returns {
      'matched':    [{'GSTIN','Vendor','Books_Inv','GSTN_Inv','Books_Date','GSTN_Date',
                       'Books_Taxable','GSTN_Taxable','Books_IGST','GSTN_IGST',
                       'Books_CGST','GSTN_CGST','Books_SGST','GSTN_SGST',
                       'Books_Total','GSTN_Total','Recon_Status'}],
      'books_only': [{'GSTIN','Vendor','Inv','Date','Taxable','IGST','CGST','SGST','Total'}],
      'gstn_only':  [...same...],
      'reconciled': [...same as matched...],
    }
    """
    _, api = _domains(region)
    hdrs = {'Authorization': f'Zoho-oauthtoken {access_token}'}
    yr, mo = return_period.split('-')
    zoho_period = f'{mo}{yr}'   # e.g. '052026'

    qs = urllib.parse.urlencode({'organization_id': org_id, 'return_period': zoho_period})
    raw = _http(f'{api}/books/v3/gstreturn/gstr2b?{qs}', headers=hdrs)

    if raw.get('code', -1) != 0 and 'gstr2b' not in raw and 'matched_transactions' not in raw:
        raise RuntimeError(f"Zoho GSTR-2B error: {raw.get('message', raw)}")

    # Zoho may nest under 'gstr2b' key or return flat
    g = raw.get('gstr2b', raw)

    def _f(v):
        try: return float(v or 0)
        except Exception: return 0.0

    def _s(v): return str(v or '').strip()

    def parse_single(e):
        """Parse a single-sided entry (books_only or gstn_only)."""
        taxable = _f(e.get('taxable_amount') or e.get('taxable') or e.get('sub_total'))
        igst    = _f(e.get('igst_amount') or e.get('igst'))
        cgst    = _f(e.get('cgst_amount') or e.get('cgst'))
        sgst    = _f(e.get('sgst_amount') or e.get('sgst'))
        total   = _f(e.get('total_amount') or e.get('total')) or (taxable + igst + cgst + sgst)
        return {
            'GSTIN':   _s(e.get('vendor_gstin') or e.get('gstin') or e.get('gst_no')),
            'Vendor':  _s(e.get('vendor_name') or e.get('contact_name') or e.get('company_name')),
            'Inv':     _s(e.get('document_number') or e.get('invoice_number') or e.get('bill_number')),
            'Date':    _s(e.get('transaction_date') or e.get('document_date') or e.get('date')),
            'Taxable': taxable, 'IGST': igst, 'CGST': cgst, 'SGST': sgst, 'Total': total,
            'Type':    _s(e.get('transaction_type') or 'Bill'),
        }

    def parse_matched(e, recon_status='Matched (Zoho)'):
        """Parse a matched/reconciled entry (two-sided: Books + GSTR-2B)."""
        # Zoho may have separate books/gstr2b sides or merged
        b_inv  = _s(e.get('books_invoice_number') or e.get('bill_number') or e.get('document_number'))
        g_inv  = _s(e.get('gstr2b_invoice_number') or e.get('document_number') or b_inv)
        b_date = _s(e.get('books_date') or e.get('transaction_date') or e.get('date'))
        g_date = _s(e.get('gstr2b_date') or b_date)
        taxable = _f(e.get('taxable_amount') or e.get('taxable') or e.get('sub_total'))
        igst    = _f(e.get('igst_amount') or e.get('igst'))
        cgst    = _f(e.get('cgst_amount') or e.get('cgst'))
        sgst    = _f(e.get('sgst_amount') or e.get('sgst'))
        b_total = _f(e.get('books_total') or e.get('bill_total') or e.get('total_amount')) or (taxable + igst + cgst + sgst)
        g_total = _f(e.get('gstr2b_total') or e.get('total_amount')) or b_total
        return {
            'GSTIN': _s(e.get('vendor_gstin') or e.get('gstin') or e.get('gst_no')),
            'Vendor': _s(e.get('vendor_name') or e.get('contact_name') or e.get('company_name')),
            'Books_Inv': b_inv, 'GSTN_Inv': g_inv,
            'Books_Date': b_date, 'GSTN_Date': g_date,
            'Books_Taxable': taxable, 'GSTN_Taxable': taxable,
            'Books_IGST': igst, 'GSTN_IGST': igst,
            'Books_CGST': cgst, 'GSTN_CGST': cgst,
            'Books_SGST': sgst, 'GSTN_SGST': sgst,
            'Books_Total': b_total, 'GSTN_Total': g_total,
            'Remark': recon_status, 'Recon_Status': recon_status,
            'Type': _s(e.get('transaction_type') or 'Bill'),
        }

    # Key aliases Zoho might use
    matched_raw    = (g.get('matched_transactions') or g.get('matched') or [])
    partial_raw    = (g.get('partially_matched_transactions') or g.get('partially_matched') or [])
    books_only_raw = (g.get('missing_in_gstn') or g.get('missing_in_gst') or g.get('books_only') or [])
    gstn_only_raw  = (g.get('missing_in_books') or g.get('missing_in_zoho_books') or g.get('gstn_only') or [])
    reconciled_raw = (g.get('reconciled_transactions') or g.get('reconciled') or [])

    return {
        'matched':    [parse_matched(e, 'Matched (Zoho)') for e in matched_raw]
                    + [parse_matched(e, 'Partially Matched (Zoho)') for e in partial_raw],
        'books_only': [parse_single(e) for e in books_only_raw],
        'gstn_only':  [parse_single(e) for e in gstn_only_raw],
        'reconciled': [parse_matched(e, 'Fully Reconciled (Zoho)') for e in reconciled_raw],
        '_raw_keys':  list(g.keys()),   # for debugging if data is empty
    }


def test_connection(client_id, client_secret, refresh_token, org_id, region='in'):
    """Validate credentials without writing anything. Returns (ok, message)."""
    try:
        tok = get_access_token(client_id, client_secret, refresh_token, region)
        v = fetch_vendors(tok, org_id, region)
        return True, f'Connected. {len(v)} vendors with GSTIN found.'
    except Exception as e:
        return False, str(e)
