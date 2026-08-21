import copy

import pytest

from ingest.anonymize import FICTIONAL, anonymize

IDENTITY_KEYS = [
    "crd", "name", "legal_name", "sec_number", "city", "state", "country", "website",
]


def _make_firms(count):
    firms = []
    for i in range(count):
        firms.append({
            "crd": f"{9000000000 + i}",  # not-a-crd
            "name": f"REAL FIRM {i}",
            "legal_name": f"REAL LEGAL {i}",
            "sec_number": f"801-{9000000000 + i}",
            "city": "REAL CITY",
            "state": "REAL STATE",
            "country": "REAL COUNTRY",
            "website": "REAL WEBSITE",
            "selection_bucket": "clean",
            "aum": {"total_usd": 500_000_000, "total_accounts": 42},
            "clients": {"by_type": {"individuals_non_hnw": {"count": 3}}},
            "disciplinary": {"any_disclosure": False, "items": []},
            "nested": {"value": 1, "deep": [1, 2, 3]},
        })
    return firms


def test_identity_fields_replaced():
    firms = _make_firms(len(FICTIONAL))
    out = anonymize(firms)
    for i, firm in enumerate(out):
        for key in IDENTITY_KEYS:
            assert firm[key] == FICTIONAL[i][key]
        for key in firm:
            if key not in IDENTITY_KEYS:
                assert firm[key] == firms[i][key]


def test_deterministic():
    firms = _make_firms(len(FICTIONAL))
    assert anonymize(firms) == anonymize(firms)


def test_does_not_mutate_input():
    firms = _make_firms(len(FICTIONAL))
    before = copy.deepcopy(firms)
    anonymize(firms)
    assert firms == before


def test_length_mismatch_exits():
    with pytest.raises(SystemExit):
        anonymize(_make_firms(len(FICTIONAL) - 1))


def test_retained_text_naming_real_firm_exits():
    firms = _make_firms(len(FICTIONAL))
    firms[0]["services"] = {"other_description": "REAL FIRM 0 CUSTODIAL SERVICES"}
    with pytest.raises(SystemExit):
        anonymize(firms)


def test_retained_text_without_identity_leak_ok():
    firms = _make_firms(len(FICTIONAL))
    firms[0]["services"] = {"other_description": "WRAP FEE MANAGEMENT PROGRAMS"}
    firms[0]["compensation"] = {"other_description": ""}
    out = anonymize(firms)
    assert out[0]["services"]["other_description"] == "WRAP FEE MANAGEMENT PROGRAMS"


def test_identity_leak_across_punctuation_variants():
    firms = _make_firms(len(FICTIONAL))
    firms[0]["services"] = {
        "other_description": "REAL FIRM, 0 CUSTODIAL SERVICES",
    }
    with pytest.raises(SystemExit):
        anonymize(firms)


def test_identity_leak_across_accent_variants():
    firms = _make_firms(len(FICTIONAL))
    firms[0]["name"] = "RÉAL FIRM 0"
    firms[0]["services"] = {
        "other_description": "REAL FIRM 0 CUSTODIAL SERVICES",
    }
    with pytest.raises(SystemExit):
        anonymize(firms)