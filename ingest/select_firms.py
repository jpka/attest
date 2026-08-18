"""Select the 5 premise-test firms from the Form ADV bulk roster and emit their
full ground-truth records.

Selection policy (unchanged): 5 SEC-registered advisers in the $250M-$2B regulatory
AUM band, spread across four buckets so the test covers different firm shapes -
2 clean, 1 with an Item 11 disciplinary disclosure, 1 recently registered, 1 with
no website on file.

Firms are identified by CRD, not by name: the roster contains distinct firms sharing
a primary business name, and a name-keyed record silently merges them.

Usage:  python3 ingest/select_firms.py <roster.csv> [--out <path>]

The default output is `agents/attest_orchestrator/ground_truth.json`, the reviewable
source `publish_registry.py` uploads to the Battery Registry. Pass --out to write
elsewhere (a dry run, or the private `selected_firms.json` the premise test reads).

Port 2026-08-18 from `resources/data/firms/csv_data/select_firms.py` in the private
root repo; that file is the same code that produced the premise-test ground truth,
so a regeneration here is directly comparable with it. Line endings of the output
depend on the platform (the committed source was written by `py -3` on Windows and
is CRLF); the Battery Registry content hash runs over the parsed JSON, so the line
ending never affects a roster version.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import adv_schema as adv

AUM_MIN = 250_000_000
AUM_MAX = 2_000_000_000

REFERENCE_DATE = datetime(2026, 8, 11)
RECENT_REGISTRATION_MONTHS = 18

BUCKET_QUOTAS = {'clean': 2, 'disciplinary': 1, 'recent': 1, 'thin_web': 1}

DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / "agents" / "attest_orchestrator" / "ground_truth.json"
)


def parse_aum(value):
    try:
        value = value.strip().replace(',', '')
        return float(value) if value else 0.0
    except (AttributeError, ValueError):
        return 0.0


def months_since(date_str):
    try:
        registered = datetime.strptime(date_str.strip(), '%m/%d/%Y')
    except (AttributeError, ValueError):
        return None
    if registered > REFERENCE_DATE:
        return None
    return ((REFERENCE_DATE.year - registered.year) * 12
            + REFERENCE_DATE.month - registered.month)


def classify(row):
    """Which selection bucket this firm belongs to, or None if it doesn't qualify."""
    has_disciplinary = adv._yes(row, adv.C_ITEM_11_ANY) or any(
        adv._yes(row, col) for col in adv.DISCIPLINARY_FIELDS
    )
    age = months_since(row[adv.C_SEC_STATUS_DATE])
    is_recent = age is not None and 0 <= age <= RECENT_REGISTRATION_MONTHS
    is_thin_web = row[adv.C_WEBSITE_COUNT].strip() in ('0', '', 'N')

    if has_disciplinary:
        return 'disciplinary'
    if is_recent:
        return 'recent'
    if is_thin_web:
        return 'thin_web'
    return 'clean'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roster_csv')
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    args = parser.parse_args()

    selected = {bucket: [] for bucket in BUCKET_QUOTAS}
    seen_crds = set()

    with open(args.roster_csv, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise SystemExit(
                "roster is empty; expected the pinned 2026-08-11 header"
            ) from None
        if header != list(adv.EXPECTED_HEADERS):
            mismatch = next(
                ((i, got, want) for i, (got, want)
                 in enumerate(zip(header, adv.EXPECTED_HEADERS, strict=False))
                 if got != want),
                (min(len(header), len(adv.EXPECTED_HEADERS)), "end of header",
                 "end of pinned header"),
            )
            raise SystemExit(
                f"roster header does not match the pinned 2026-08-11 schema "
                f"(column {mismatch[0]}: got {mismatch[1]!r}, expected {mismatch[2]!r})"
            )

        for row in reader:
            if len(row) < adv.MIN_COLUMNS:
                continue
            if not row[adv.C_PRIMARY_NAME].strip():
                continue
            if not AUM_MIN <= parse_aum(row[adv.C_5F_TOTAL_AUM]) <= AUM_MAX:
                continue

            crd = row[adv.C_CRD].strip()
            if not crd:
                continue
            if crd in seen_crds:
                continue

            bucket = classify(row)
            if len(selected[bucket]) >= BUCKET_QUOTAS[bucket]:
                continue

            seen_crds.add(crd)
            selected[bucket].append(adv.build_record(row, selection_bucket=bucket))

            if all(len(selected[b]) == q for b, q in BUCKET_QUOTAS.items()):
                break

    firms = [firm for bucket in ('clean', 'disciplinary', 'recent', 'thin_web')
             for firm in selected[bucket]]

    short = {b: q - len(selected[b]) for b, q in BUCKET_QUOTAS.items() if len(selected[b]) < q}
    if short:
        print(f"Warning: could not fill every bucket, short by {short}", file=sys.stderr)

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(firms, f, indent=2)

    print(f"Selected {len(firms)} firms -> {args.out}")
    for firm in firms:
        fees = firm['compensation']
        billing = [v['adv_item'] for k, v in fees.items()
                   if isinstance(v, dict) and v.get('value')]
        print(f"  [{firm['selection_bucket']:12}] CRD {firm['crd']:>7}  {firm['name'][:38]:38}"
              f"  ${firm['aum']['total_usd']:,.0f}"
              f"  disciplinary={firm['disciplinary']['any_disclosure']}")
        print(f"                 compensated by: {'; '.join(billing) or 'nothing reported'}")


if __name__ == "__main__":
    main()