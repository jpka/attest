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
            'participates_in_wrap_fee_program': _yes(row, C_5I1_WRAP_FEE_PROGRAM),
        },

        'disciplinary': {
            'adv_item': "Form ADV Part 1A Item 11 - disciplinary history of the firm and its advisory affiliates",
            'any_disclosure': _yes(row, C_ITEM_11_ANY) or bool(disciplinary_items),
            'items': disciplinary_items,
        },
    }
