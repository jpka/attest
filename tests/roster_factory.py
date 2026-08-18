"""Build synthetic Form ADV bulk-roster rows for tests."""

import csv

from ingest import adv_schema as adv


def row(crd="1000001", name="TEST FIRM", aum="500000000", status_date="01/01/2010",
        form_version="10/2021", website_count="1", disciplinary=False, **fields):
    cells = [""] * len(adv.EXPECTED_HEADERS)
    cells[adv.C_CRD] = crd
    cells[adv.C_PRIMARY_NAME] = name
    cells[adv.C_5F_TOTAL_AUM] = aum
    cells[adv.C_SEC_STATUS_DATE] = status_date
    cells[adv.C_FORM_VERSION] = form_version
    cells[adv.C_WEBSITE_COUNT] = website_count
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