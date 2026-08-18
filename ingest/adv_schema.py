"""Column map for the SEC Form ADV Part 1A bulk roster CSV.

Column indices are positional into the FOIA roster export; the labels are transcribed
from Form ADV Part 1A itself (Items 5.A-5.I and Item 11), so a scorer reading a record
built by `build_record` sees the regulator's own wording rather than a paraphrase.

Verified against the Form ADV paper version, 2026-08-11.
"""

# --- identity -------------------------------------------------------------
C_CRD = 1
C_SEC_NUMBER = 4
C_PRIMARY_NAME = 10
C_LEGAL_NAME = 11
C_CITY = 14
C_STATE = 15
C_COUNTRY = 16
C_POSTAL = 17
C_SEC_STATUS = 29
C_SEC_STATUS_DATE = 30
C_LATEST_FILING = 32
C_WEBSITE = 35
C_WEBSITE_COUNT = 36

# --- Item 5.A / 5.B: employees --------------------------------------------
C_5A_EMPLOYEES = 73
EMPLOYEE_FIELDS = {
    74: ('performing_advisory_functions', "employees who perform investment advisory functions (including research)"),
    75: ('registered_reps_of_broker_dealer', "employees who are registered representatives of a broker-dealer"),
    76: ('investment_adviser_reps', "employees registered with state securities authorities as investment adviser representatives"),
    77: ('adviser_reps_for_another_adviser', "employees registered as investment adviser representatives for another adviser"),
    78: ('licensed_insurance_agents', "employees who are licensed agents of an insurance company or agency"),
    79: ('solicitors', "firms or other persons who solicit advisory clients on the firm's behalf"),
}

# --- Item 5.C: clients without regulatory AUM -----------------------------
C_5C1_CLIENTS_NO_RAUM = 80
C_5C2_PCT_NON_US = 82

# --- Item 5.D: client types (triplets: count, fewer-than-5 flag, RAUM) ----
# Categories (d), (e) and (f) have no "fewer than 5" checkbox, so their triplets
# are two columns wide rather than three.
CLIENT_TYPES = [
    ('individuals_non_hnw', "Individuals (other than high net worth individuals)", 111, 112, 113),
    ('high_net_worth_individuals', "High net worth individuals", 114, 115, 116),
    ('banking_or_thrift', "Banking or thrift institutions", 117, 118, 119),
    ('investment_companies', "Investment companies", 120, None, 121),
    ('business_development_companies', "Business development companies", 122, None, 123),
    ('pooled_investment_vehicles', "Pooled investment vehicles", 124, None, 125),
    ('pension_and_profit_sharing', "Pension and profit sharing plans", 126, 127, 128),
    ('charitable_organizations', "Charitable organizations", 129, 130, 131),
    ('state_or_municipal_entities', "State or municipal government entities", 132, 133, 134),
    ('other_investment_advisers', "Other investment advisers", 135, 136, 137),
    ('insurance_companies', "Insurance companies", 138, 139, 140),
    ('sovereign_wealth_funds', "Sovereign wealth funds and foreign official institutions", 141, 142, 143),
    ('corporations_or_other_businesses', "Corporations or other businesses not listed above", 144, 145, 146),
    ('other', "Other", 147, 148, 149),
]

# --- Item 5.E: compensation arrangements ----------------------------------
# "You are compensated for your investment advisory services by (check all that apply)"
FEE_FIELDS = {
    151: ('percentage_of_aum', "A percentage of assets under your management"),
    152: ('hourly_charges', "Hourly charges"),
    153: ('subscription_fees', "Subscription fees (for a newsletter or periodical)"),
    154: ('fixed_fees', "Fixed fees (other than subscription fees)"),
    155: ('commissions', "Commissions"),
    156: ('performance_based_fees', "Performance-based fees"),
    157: ('other', "Other"),
}
C_5E_OTHER_TEXT = 158

# --- Item 5.F: regulatory assets under management -------------------------
C_5F1_CONTINUOUS_SUPERVISORY = 159
C_5F_TOTAL_AUM = 162
AUM_FIELDS = {
    160: ('discretionary_usd', "Regulatory AUM managed on a discretionary basis (USD)"),
    161: ('non_discretionary_usd', "Regulatory AUM managed on a non-discretionary basis (USD)"),
    162: ('total_usd', "Total regulatory assets under management (USD)"),
    163: ('discretionary_accounts', "Number of discretionary accounts"),
    164: ('non_discretionary_accounts', "Number of non-discretionary accounts"),
    165: ('total_accounts', "Total number of accounts"),
}
C_5F3_NON_US_AUM = 166

# --- Item 5.G: advisory activities ----------------------------------------
SERVICE_FIELDS = {
    167: ('financial_planning', "Financial planning services"),
    168: ('portfolio_mgmt_individuals_small_biz', "Portfolio management for individuals and/or small businesses"),
    169: ('portfolio_mgmt_investment_companies', "Portfolio management for investment companies"),
    172: ('portfolio_mgmt_pooled_vehicles', "Portfolio management for pooled investment vehicles"),
    173: ('portfolio_mgmt_businesses_institutions', "Portfolio management for businesses or institutional clients"),
    174: ('pension_consulting', "Pension consulting services"),
    175: ('selection_of_other_advisers', "Selection of other advisers (including private fund managers)"),
    176: ('publication_of_periodicals', "Publication of periodicals or newsletters"),
    177: ('security_ratings_or_pricing', "Security ratings or pricing services"),
    178: ('market_timing', "Market timing services"),
    179: ('educational_seminars', "Educational seminars/workshops"),
    180: ('other', "Other"),
}
C_5G_OTHER_TEXT = 181

# --- Item 5.H / 5.I -------------------------------------------------------
C_5H_PLANNING_CLIENTS = 182
C_5H_IF_MORE_THAN_500 = 183
C_5I1_WRAP_FEE_PROGRAM = 184

# --- Item 11: disciplinary ------------------------------------------------
C_ITEM_11_ANY = 394
DISCIPLINARY_FIELDS = {
    395: ('11A(1)', "Convicted of or pled guilty/nolo contendere to a felony (past ten years)"),
    397: ('11A(2)', "Charged with a felony (past ten years)"),
    399: ('11B(1)', "Convicted of or pled guilty/nolo contendere to an investment-related misdemeanor (past ten years)"),
    401: ('11B(2)', "Charged with an investment-related misdemeanor (past ten years)"),
    403: ('11C(1)', "SEC or CFTC found a false statement or omission"),
    405: ('11C(2)', "SEC or CFTC found involvement in a violation of SEC or CFTC regulations or statutes"),
    407: ('11C(3)', "SEC or CFTC found the firm a cause of an investment-related business losing its authorization"),
    409: ('11C(4)', "SEC or CFTC entered an order in connection with investment-related activity"),
    411: ('11C(5)', "SEC or CFTC imposed a civil money penalty or a cease-and-desist order"),
    413: ('11D(1)', "Another regulator found a false statement, omission, or dishonest/unfair/unethical conduct"),
    415: ('11D(2)', "Another regulator found involvement in a violation of investment-related regulations or statutes"),
    417: ('11D(3)', "Another regulator found the firm a cause of an investment-related business losing its authorization"),
    419: ('11D(4)', "Another regulator entered an order in connection with investment-related activity (past ten years)"),
    421: ('11D(5)', "Another regulator denied, suspended, or revoked a registration or license"),
    423: ('11E(1)', "A self-regulatory organization found a false statement or omission"),
    425: ('11E(2)', "A self-regulatory organization found involvement in a violation of its rules"),
    427: ('11E(3)', "An SRO found the firm a cause of an investment-related business losing its authorization"),
    429: ('11E(4)', "An SRO disciplined the firm by expulsion, suspension, bar, or restriction of activities"),
    431: ('11F', "An authorization to act as attorney, accountant, or federal contractor was revoked or suspended"),
    433: ('11G', "Currently the subject of a regulatory proceeding that could result in a yes to Item 11.C, 11.D, or 11.E"),
    435: ('11H(1)(a)', "A court enjoined the firm in connection with investment-related activity (past ten years)"),
    437: ('11H(1)(b)', "A court found involvement in a violation of investment-related statutes or regulations"),
    439: ('11H(1)(c)', "A court dismissed an investment-related civil action pursuant to a settlement agreement"),
    441: ('11H(2)', "Currently named in a pending investment-related civil action"),
}

MIN_COLUMNS = 443

# Pinned header for the FOIA bulk roster, transcribed from the 2026-08-11 release's
# own header row (the SEC export, not this repo's `headers.json`; the two agree).
# `select_firms.py` checks the CSV header against this before reading any row so a
# renamed, reordered or inserted column fails loudly instead of silently mis-mapping.
EXPECTED_HEADERS = (
    'SEC Region', 'Organization CRD#', 'Additional CRD Number', 'Total number of additional CRD numbers',
    'SEC#', 'Firm Type', 'Umbrella Registration', 'Total number of relying advisers',
    'CIK#', 'Total number of CIK numbers', 'Primary Business Name', 'Legal Name',
    'Main Office Street Address 1', 'Main Office Street Address 2', 'Main Office City', 'Main Office State',
    'Main Office Country', 'Main Office Postal Code', 'Main Office Private Residence Flag', 'Main Office Telephone Number',
    'Main Office Facsimile Number', 'Total number of offices, other than your Principal Office and place of business', 'Mail Office Street Address 1', 'Mail Office Street Address 2',
    'Mail Office City', 'Mail Office State', 'Mail Office Country', 'Mail Office Postal Code',
    'Mail Office Private Residence Flag', 'SEC Current Status', 'SEC Status Effective Date', 'Jurisdiction Notice Filed-Effective Date',
    'Latest ADV Filing Date', 'Form Version', '1I', 'Website Address',
    'Total Number of Website Addresses', '1L', 'Location of Books and Records Street Address 1', 'Location of Books and Records Street Address 2',
    'Location of Books and Records City', 'Location of Books and Records State', 'Location of Books and Records Country', 'Location of Books and Records Postal Code',
    'Total Number of Books and Records Locations', '1M', '1N', '1O',
    '1O - If yes, approx. amount of assets', '1P', '2A(1)', '2A(2)',
    '2A(4)', '2A(5)', '2A(6)', '2A(7)',
    '2A(8)', '2A(9)', '2A(10)', '2A(11)',
    '2A(12)', '2A(13)', '3A', '3A-Other',
    '3B', '3C-State', '3C-Country', '4A',
    'Acquired Firm', 'Acquired Firm SEC#', 'Acquired Firm CRD#', 'Total Number of Acquired Firms',
    '4B', '5A', '5B(1)', '5B(2)',
    '5B(3)', '5B(4)', '5B(5)', '5B(6)',
    '5C(1)', '5C(1)-If more than 100, how many', '5C(2)', '5D(1)(a)',
    '5D(1)(b)', '5D(1)(c)', '5D(1)(d)', '5D(1)(e)',
    '5D(1)(f)', '5D(1)(g)', '5D(1)(h)', '5D(1)(i)',
    '5D(1)(j)', '5D(1)(k)', '5D(1)(l)', '5D(1)(m)',
    '5D(1)(m)-Other', '5D(2)(a)', '5D(2)(b)', '5D(2)(c)',
    '5D(2)(d)', '5D(2)(e)', '5D(2)(f)', '5D(2)(g)',
    '5D(2)(h)', '5D(2)(i)', '5D(2)(j)', '5D(2)(k)',
    '5D(2)(l)', '5D(2)(m)', '5D(2)(m)-Other', '5D(a)(1)',
    '5D(a)(2)', '5D(a)(3)', '5D(b)(1)', '5D(b)(2)',
    '5D(b)(3)', '5D(c)(1)', '5D(c)(2)', '5D(c)(3)',
    '5D(d)(1)', '5D(d)(3)', '5D(e)(1)', '5D(e)(3)',
    '5D(f)(1)', '5D(f)(3)', '5D(g)(1)', '5D(g)(2)',
    '5D(g)(3)', '5D(h)(1)', '5D(h)(2)', '5D(h)(3)',
    '5D(i)(1)', '5D(i)(2)', '5D(i)(3)', '5D(j)(1)',
    '5D(j)(2)', '5D(j)(3)', '5D(k)(1)', '5D(k)(2)',
    '5D(k)(3)', '5D(l)(1)', '5D(l)(2)', '5D(l)(3)',
    '5D(m)(1)', '5D(m)(2)', '5D(m)(3)', '5D(n)(1)',
    '5D(n)(2)', '5D(n)(3)', '5D(n)(3) - Other', '5E(1)',
    '5E(2)', '5E(3)', '5E(4)', '5E(5)',
    '5E(6)', '5E(7)', '5E(7)-Other', '5F(1)',
    '5F(2)(a)', '5F(2)(b)', '5F(2)(c)', '5F(2)(d)',
    '5F(2)(e)', '5F(2)(f)', '5F(3)', '5G(1)',
    '5G(2)', '5G(3)', '5.G.(3) - Total number of RICs or BDCs', '5.G.(3) - Total amount of Parallel Assets',
    '5G(4)', '5G(5)', '5G(6)', '5G(7)',
    '5G(8)', '5G(9)', '5G(10)', '5G(11)',
    '5G(12)', '5G(12)-Other', '5H', '5H-If more than 500, how many',
    '5I(1)', '5I(2)(a)', '5I(2)(b)', '5I(2)(c)',
    '5.I.(2) - Total number of wrap fee programs', '5J(1)', '5J(2)', '5K(1)',
    '5.K.(1)(a)(i) midyear percentage', '5.K.(1)(a)(ii) midyear percentage', '5.K.(1)(a)(iii) midyear percentage', '5.K.(1)(a)(iv) midyear percentage',
    '5.K.(1)(a)(v) midyear percentage', '5.K.(1)(a)(vi) midyear percentage', '5.K.(1)(a)(vii) midyear percentage', '5.K.(1)(a)(viii) midyear percentage',
    '5.K.(1)(a)(ix) midyear percentage', '5.K.(1)(a)(x) midyear percentage', '5.K.(1)(a)(xi) midyear percentage', '5.K.(1)(a)(xii) midyear percentage',
    '5.K.(1)(a)(i) end year percentage', '5.K.(1)(a)(ii) end year percentage', '5.K.(1)(a)(iii) end year percentage', '5.K.(1)(a)(iv) end year percentage',
    '5.K.(1)(a)(v) end year percentage', '5.K.(1)(a)(vi) end year percentage', '5.K.(1)(a)(vii) end year percentage', '5.K.(1)(a)(viii) end year percentage',
    '5.K.(1)(a)(ix) end year percentage', '5.K.(1)(a)(x) end year percentage', '5.K.(1)(a)(xi) end year percentage', '5.K.(1)(a)(xii) end year percentage',
    '5.K.(1)(a)(xii)  - Other description', '5.K.(1)(b)(i) end year percentage', '5.K.(1)(b)(ii) end year percentage', '5.K.(1)(b)(iii) end year percentage',
    '5.K.(1)(b)(iv) end year percentage', '5.K.(1)(b)(v) end year percentage', '5.K.(1)(b)(vi) end year percentage', '5.K.(1)(b)(vii) end year percentage',
    '5.K.(1)(b)(viii) end year percentage', '5.K.(1)(b)(ix) end year percentage', '5.K.(1)(b)(x) end year percentage', '5.K.(1)(b)(xi) end year percentage',
    '5.K.(1)(b)(xii) end year percentage', '5.K.(1)(b)(xii)  - Other description', '5K(2)', '5K(3)',
    '5.K.(2)(a)(i)(1) less 10', '5.K.(2)(a)(i)(1) 10-149', '5.K.(2)(a)(i)(1) over 150', '5.K.(2)(a)(i)(2) less 10',
    '5.K.(2)(a)(i)(2) 10-149', '5.K.(2)(a)(i)(2) over 150', '5.K.(2)(a)(i)(3)(a) less 10 percentage ', '5.K.(2)(a)(i)(3)(b) less 10 percentage ',
    '5.K.(2)(a)(i)(3)(c) less 10 percentage ', '5.K.(2)(a)(i)(3)(d) less 10 percentage ', '5.K.(2)(a)(i)(3)(e) less10 percentage ', '5.K.(2)(a)(i)(3)(f) less 10 percentage ',
    '5.K.(2)(a)(i)(3)(a) 10-149 percentage ', '5.K.(2)(a)(i)(3)(b) 10-149 percentage ', '5.K.(2)(a)(i)(3)(c) 10-149 percentage ', '5.K.(2)(a)(i)(3)(d) 10-149 percentage ',
    '5.K.(2)(a)(i)(3)(e) 10-149 percentage ', '5.K.(2)(a)(i)(3)(f) 10-149 percentage ', '5.K.(2)(a)(i)(3)(a) over 150 percentage ', '5.K.(2)(a)(i)(3)(b) over 150 percentage ',
    '5.K.(2)(a)(i)(3)(c) over 150 percentage ', '5.K.(2)(a)(i)(3)(d) over 150 percentage ', '5.K.(2)(a)(i)(3)(e) over 150 percentage ', '5.K.(2)(a)(i)(3)(f) over 150 percentage ',
    '5.K.(2)(a)(ii)(1) less 10', '5.K.(2)(a)(ii)(1) 10-149', '5.K.(2)(a)(ii)(1) over 150', '5.K.(2)(a)(ii)(2) less 10',
    '5.K.(2)(a)(ii)(2) 10-149', '5.K.(2)(a)(ii)(2) over 150', '5.K.(2)(a)(ii)(3)(a) less 10 percentage ', '5.K.(2)(a)(ii)(3)(b) less 10 percentage ',
    '5.K.(2)(a)(ii)(3)(c) less 10 percentage ', '5.K.(2)(a)(ii)(3)(d) less 10 percentage ', '5.K.(2)(a)(ii)(3)(e) less10 percentage ', '5.K.(2)(a)(ii)(3)(f) less 10 percentage ',
    '5.K.(2)(a)(ii)(3)(a) 10-149 percentage ', '5.K.(2)(a)(ii)(3)(b) 10-149 percentage ', '5.K.(2)(a)(ii)(3)(c) 10-149 percentage ', '5.K.(2)(a)(ii)(3)(d) 10-149 percentage ',
    '5.K.(2)(a)(ii)(3)(e) 10-149 percentage ', '5.K.(2)(a)(ii)(3)(f) 10-149 percentage ', '5.K.(2)(a)(ii)(3)(a) over 150 percentage ', '5.K.(2)(a)(ii)(3)(b) over 150 percentage ',
    '5.K.(2)(a)(ii)(3)(c) over 150 percentage ', '5.K.(2)(a)(ii)(3)(d) over 150 percentage ', '5.K.(2)(a)(ii)(3)(e) over 150 percentage ', '5.K.(2)(a)(ii)(3)(f) over 150 percentage ',
    '5.K.(2)(b)(1) less 10', '5.K.(2)(b)(1) 10-149', '5.K.(2)(b)(1) over150', '5.K.(2)(b)(2) less 10',
    '5.K.(2)(b)(2) 10-149', '5.K.(2)(b)(2) over150', '5K(4)', '5L(1)(a)',
    '5L(1)(b)', '5L(1)(c)', '5L(1)(d)', '5L(1)(e)',
    '5L(2)', '5L(3)', '5L(4)', '6A(1)',
    '6A(2)', '6A(3)', '6A(4)', '6A(5)',
    '6A(6)', '6A(7)', '6A(8)', '6A(9)',
    '6A(10)', '6A(11)', '6A(12)', '6A(13)',
    '6A(14)', '6A(14)-Other', '6B(1)', '6B(2)',
    '6B(3)', '7A(1)', '7A(2)', '7A(3)',
    '7A(4)', '7A(5)', '7A(6)', '7A(7)',
    '7A(8)', '7A(9)', '7A(10)', '7A(11)',
    '7A(12)', '7A(13)', '7A(14)', '7A(15)',
    '7A(16)', 'Count of IA Affiliates', 'Count of IA/BD Affiliates', 'Count of BD Affiliates',
    'Control/Controlled by Related Person', 'Under Common Control', 'Share Supervised Persons', 'Share Location',
    '7B', 'Count of Private Funds - 7B(1)', 'Any PFs a Master', 'Any Hedge Funds',
    'Total number of Hedge funds', 'Any Liquidity Funds', 'Total number of Liquidity funds', 'Any PE Funds',
    'Total number of PE funds', 'Any Real Estate Funds', 'Total number of Real Estate funds', 'Any Securitized Funds',
    'Total number of Securitized funds', 'Any VC Funds', 'Total number of VC funds', 'Any Other Funds',
    'Total number of Other funds', 'Total Gross Assets of Private Funds', 'Count of Private Funds - 7B(2)', '8A(1)',
    '8A(2)', '8A(3)', '8B(1)', '8B(2)',
    '8B(3)', '8C(1)', '8C(2)', '8C(3)',
    '8C(4)', '8D', '8E', '8F',
    '8G(1)', '8G(2)', '8H', '8H(1)',
    '8H(2)', '8I', '9A(1)(a)', '9A(1)(b)',
    '9A(2)(a)', '9A(2)(b)', '9B(1)(a)', '9B(1)(b)',
    '9B(2)(a)', '9B(2)(b)', 'Total Custody Amount', '9C(1)',
    '9C(2)', '9C(3)', '9C(4)', '9C Unqual Opinion',
    '9D(1)', '9D(2)', '9E', '9F',
    '10A', 'Count of Control person Public Reporting Company', '11', '11A(1)',
    'Count of 11A(1) disclosures', '11A(2)', 'Count of 11A(2) disclosures', '11B(1)',
    'Count of 11B(1) disclosures', '11B(2)', 'Count of 11B(2) disclosures', '11C(1)',
    'Count of 11C(1) disclosures', '11C(2)', 'Count of 11C(2) disclosures', '11C(3)',
    'Count of 11C(3) disclosures', '11C(4)', 'Count of 11C(4) disclosures', '11C(5)',
    'Count of 11C(5) disclosures', '11D(1)', 'Count of 11D(1) disclosures', '11D(2)',
    'Count of 11D(2) disclosures', '11D(3)', 'Count of 11D(3) disclosures', '11D(4)',
    'Count of 11D(4) disclosures', '11D(5)', 'Count of 11D(5) disclosures', '11E(1)',
    'Count of 11E(1) disclosures', '11E(2)', 'Count of 11E(2) disclosures', '11E(3)',
    'Count of 11E(3) disclosures', '11E(4)', 'Count of 11E(4) disclosures', '11F',
    'Count of 11F disclosures', '11G', 'Count of 11G disclosures', '11H(1)(a)',
    'Count of 11H(1)(a) disclosures', '11H(1)(b)', 'Count of 11H(1)(b) disclosures', '11H(1)(c)',
    'Count of 11H(1)(c) disclosures', '11H(2)', 'Count of 11H(2) disclosures', '12A',
    '12B(1)', '12B(2)', '12C(1)', '12C(2)',
)


def _text(row, idx):
    return row[idx].strip() if idx is not None and idx < len(row) else ""


def is_yes(row, idx):
    return _text(row, idx).upper() == 'Y'


_yes = is_yes  # internal alias used throughout this module


def _number(row, idx):
    raw = _text(row, idx).replace(',', '')
    if not raw:
        return None
    try:
        return float(raw) if '.' in raw else int(raw)
    except ValueError:
        return None


def _flags(row, spec):
    """{field: {"value": bool, "adv_item": label}} for a checkbox group."""
    return {
        key: {'value': _yes(row, col), 'adv_item': label}
        for col, (key, label) in spec.items()
    }


def build_record(row, selection_bucket=None):
    """Turn one roster row into the ground-truth record the scorer compares against."""
    fees = _flags(row, FEE_FIELDS)
    services = _flags(row, SERVICE_FIELDS)

    disciplinary_items = [
        {'item': code, 'description': label}
        for col, (code, label) in sorted(DISCIPLINARY_FIELDS.items())
        if _yes(row, col)
    ]

    clients_by_type = {}
    for key, label, c_count, c_fewer, c_aum in CLIENT_TYPES:
        count = _number(row, c_count)
        fewer_than_five = _yes(row, c_fewer) if c_fewer is not None else False
        aum = _number(row, c_aum)
        if count or fewer_than_five or aum:
            clients_by_type[key] = {
                'adv_item': label,
                'number_of_clients': count,
                'fewer_than_5_clients': fewer_than_five,
                'regulatory_aum_usd': aum,
            }

    return {
        'crd': _text(row, C_CRD),
        'name': _text(row, C_PRIMARY_NAME),
        'legal_name': _text(row, C_LEGAL_NAME),
        'sec_number': _text(row, C_SEC_NUMBER),
        'city': _text(row, C_CITY),
        'state': _text(row, C_STATE),
        'country': _text(row, C_COUNTRY),
        'website': _text(row, C_WEBSITE),
        'sec_registration_status': _text(row, C_SEC_STATUS),
        'sec_status_effective_date': _text(row, C_SEC_STATUS_DATE),
        'latest_adv_filing_date': _text(row, C_LATEST_FILING),
        'selection_bucket': selection_bucket,

        'aum': {
            'provides_continuous_supervisory_services': _yes(row, C_5F1_CONTINUOUS_SUPERVISORY),
            **{key: _number(row, col) for col, (key, _) in AUM_FIELDS.items()},
            'non_us_client_aum_usd': _number(row, C_5F3_NON_US_AUM),
        },

        'employees': {
            'total_excluding_clerical': _number(row, C_5A_EMPLOYEES),
            **{key: _number(row, col) for col, (key, _) in EMPLOYEE_FIELDS.items()},
        },

        'clients': {
            'clients_without_regulatory_aum': _number(row, C_5C1_CLIENTS_NO_RAUM),
            'percent_non_us_persons': _number(row, C_5C2_PCT_NON_US),
            'by_type': clients_by_type,
        },

        'compensation': {
            'adv_item': "Form ADV Part 1A Item 5.E - how the firm is compensated for advisory services",
            **fees,
            'other_description': _text(row, C_5E_OTHER_TEXT),
            # Derived. "Fee-only" in industry usage means no commission compensation;
            # a Y in 5.E(5) is therefore decisive against a fee-only description.
            'receives_commissions': fees['commissions']['value'],
            'is_fee_only': not fees['commissions']['value'],
        },

        'services': {
            'adv_item': "Form ADV Part 1A Item 5.G - types of advisory services provided",
            **services,
            'other_description': _text(row, C_5G_OTHER_TEXT),
            'financial_planning_client_count_band': _text(row, C_5H_PLANNING_CLIENTS),
            'financial_planning_client_count_if_more_than_500': _number(row, C_5H_IF_MORE_THAN_500),
            'participates_in_wrap_fee_program': _yes(row, C_5I1_WRAP_FEE_PROGRAM),
        },

        'disciplinary': {
            'adv_item': "Form ADV Part 1A Item 11 - disciplinary history of the firm and its advisory affiliates",
            'any_disclosure': _yes(row, C_ITEM_11_ANY) or bool(disciplinary_items),
            'items': disciplinary_items,
        },
    }
