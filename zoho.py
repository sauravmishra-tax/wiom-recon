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


def test_connection(client_id, client_secret, refresh_token, org_id, region='in'):
    """Validate credentials without writing anything. Returns (ok, message)."""
    try:
        tok = get_access_token(client_id, client_secret, refresh_token, region)
        v = fetch_vendors(tok, org_id, region)
        return True, f'Connected. {len(v)} vendors with GSTIN found.'
    except Exception as e:
        return False, str(e)
