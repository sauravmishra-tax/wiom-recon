"""
GSTIN state-code mapping.
First 2 digits of any GSTIN encode the state. Used to auto-split recon
rows state-wise without requiring separate uploads per state.
"""

STATE_CODES = {
    '01': 'Jammu & Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
    '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana',
    '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
    '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh',
    '13': 'Nagaland', '14': 'Manipur', '15': 'Mizoram',
    '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
    '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
    '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
    '25': 'Daman & Diu', '26': 'Dadra & Nagar Haveli', '27': 'Maharashtra',
    '28': 'Andhra Pradesh (Old)', '29': 'Karnataka', '30': 'Goa',
    '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu',
    '34': 'Puducherry', '35': 'Andaman & Nicobar', '36': 'Telangana',
    '37': 'Andhra Pradesh', '38': 'Ladakh',
    '97': 'Other Territory', '99': 'Centre Jurisdiction',
}


# WIOM operates/registers in only these 4 states. Each monthly recon file
# belongs to one of them (chosen at upload). Used for all state dropdowns.
WIOM_STATES = ['Delhi', 'Haryana', 'Maharashtra', 'Uttar Pradesh']
NAME_TO_CODE = {v: k for k, v in STATE_CODES.items()}


def code_for_state(name):
    return NAME_TO_CODE.get(name, '')


def state_from_gstin(gstin):
    """Return (state_code, state_name) from a GSTIN. Safe on junk/empty input."""
    if not gstin:
        return ('', 'Unknown')
    code = str(gstin).strip()[:2]
    if code in STATE_CODES:
        return (code, STATE_CODES[code])
    return (code or '', 'Unknown')
