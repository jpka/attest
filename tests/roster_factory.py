"""Build synthetic Form ADV bulk-roster rows for tests."""

import csv
from datetime import timedelta

from ingest import adv_schema as adv
from ingest.select_firms import REFERENCE_DATE


def recent_date():
    """A registration date inside the 18-month recent window, relative to the
    pinned clock — so the fixtures stay recent no matter when the tests run."""
    return (REFERENCE_DATE - timedelta(days=120)).strftime("%m/%d/%Y")


def future_date():
    """A registration date strictly after the pinned clock, in any year."""
    return (REFERENCE_DATE + timedelta(days=1)).strftime("%m/%d/%Y")


def row(crd="1000001", name="TEST FIRM", aum="500000000", status_date="01/01/2010",  # not-a-crd
        form_version="10/2021", website_count="1", disciplinary=False,
        status="Approved", **fields):
    cells = [""] * len(adv.EXPECTED_HEADERS)
    cells[adv.C_CRD] = crd
    cells[adv.C_PRIMARY_NAME] = name
    cells[adv.C_5F_TOTAL_AUM] = aum
    cells[adv.C_SEC_STATUS_DATE] = status_date
    cells[adv.C_FORM_VERSION] = form_version
    cells[adv.C_WEBSITE_COUNT] = website_count
    cells[adv.C_SEC_STATUS] = status
    if disciplinary:
        cells[adv.C_ITEM_11_ANY] = "Y"
    for col, value in fields.items():
        cells[col] = value
    return cells


def write_roster(path, rows, header=None):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header or list(adv.EXPECTED_HEADERS))
        for r in rows:
            writer.writerow(r)