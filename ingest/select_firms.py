"""Select the 5 premise-test firms from the Form ADV bulk roster and emit their
full ground-truth records.

Selection policy (unchanged): 5 SEC-registered advisers in the $250M-$2B regulatory
AUM band, spread across four buckets so the test covers different firm shapes -
2 clean, 1 with an Item 11 disciplinary disclosure, 1 recently registered, 1 with
no website on file.

Firms are identified by CRD, not by name: the roster contains distinct firms sharing
a primary business name, and a name-keyed record silently merges them.

Usage:  python3 ingest/select_firms.py <roster.csv> [--out <path>]

The default `--out` is `selected_firms.json`, a scratch file in the current
directory: the firms selected here are real SEC registrants, so this output must
never be committed. The committed `agents/attest_orchestrator/ground_truth.json`
is produced by the anonymization step `ingest/anonymize.py`, which replaces each
firm's identity with a fixed fictional one before the ground truth is reviewed or
published.

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

try:
    from . import adv_schema as adv
except ImportError:  # running as a script: python ingest/select_firms.py
    import adv_schema as adv

AUM_MIN = 250_000_000
AUM_MAX = 2_000_000_000

# Registration statuses that mean "actively registered". The FOIA roster only
# contains Approved and 120-Day Approval rows today, but a stale or wider
# export could carry blank or terminated statuses; those must not fill a bucket.
ACTIVE_STATUSES = frozenset(('Approved', '120-Day Approval'))

REFERENCE_DATE = datetime(2026, 8, 11)
RECENT_REGISTRATION_MONTHS = 18

BUCKET_QUOTAS = {'clean': 2, 'disciplinary': 1, 'recent': 1, 'thin_web': 1}

DEFAULT_OUT = "selected_firms.json"


class BucketShortfallError(RuntimeError):
    """Not every selection bucket could be filled; do not publish a partial roster."""


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
    """Which selection bucket this firm belongs to, or None if it doesn't qualify.

    Returns None when the registration date is missing, unparseable or in the
    future: such a row must not fill any bucket silently.
    """
    has_disciplinary = adv._yes(row, adv.C_ITEM_11_ANY) or any(
        adv._yes(row, col) for col in adv.DISCIPLINARY_FIELDS
    )
    age = months_since(row[adv.C_SEC_STATUS_DATE])
    if age is None:
        return None
    is_recent = 0 <= age <= RECENT_REGISTRATION_MONTHS
    is_thin_web = row[adv.C_WEBSITE_COUNT].strip() in ('0', '')

    if has_disciplinary:
        return 'disciplinary'
    if is_recent:
        return 'recent'
    if is_thin_web:
        return 'thin_web'
    return 'clean'


def run(roster_csv, out):
    """Read the roster, select the firms, write `out`; returns (firms, summary)."""
    summary = {
        'scanned': 0,
        'short': 0,
        'no_name': 0,
        'no_crd': 0,
        'aum_out_of_band': 0,
        'form_version': 0,
        'inactive_status': 0,
        'invalid_status_date': 0,
    }
    selected = {bucket: [] for bucket in BUCKET_QUOTAS}
    seen_crds = set()

    with open(roster_csv, encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            header = None
        if header is None:
            raise SystemExit(
                "roster is empty; expected the pinned 2026-08-11 header"
            )
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
            summary['scanned'] += 1
            if len(row) < adv.MIN_COLUMNS:
                summary['short'] += 1
                continue
            if not row[adv.C_PRIMARY_NAME].strip():
                summary['no_name'] += 1
                continue
            if row[adv.C_FORM_VERSION].strip() != adv.EXPECTED_FORM_VERSION:
                summary['form_version'] += 1
                continue
            if row[adv.C_SEC_STATUS].strip() not in ACTIVE_STATUSES:
                summary['inactive_status'] += 1
                continue
            if not AUM_MIN <= parse_aum(row[adv.C_5F_TOTAL_AUM]) <= AUM_MAX:
                summary['aum_out_of_band'] += 1
                continue

            crd = row[adv.C_CRD].strip()
            if not crd:
                summary['no_crd'] += 1
                continue
            if crd in seen_crds:
                continue

            bucket = classify(row)
            if bucket is None:
                summary['invalid_status_date'] += 1
                continue
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
        raise BucketShortfallError(f"could not fill every bucket, short by {short}")

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(firms, f, indent=2)
        f.write("\n")

    return firms, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roster_csv')
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    args = parser.parse_args()

    try:
        firms, summary = run(args.roster_csv, args.out)
    except BucketShortfallError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    print(f"Selected {len(firms)} firms -> {args.out}")
    print(
        f"  scanned {summary['scanned']} rows; skipped: "
        f"{summary['short']} short, {summary['no_name']} no-name, "
        f"{summary['no_crd']} no-CRD, {summary['aum_out_of_band']} out-of-AUM-band, "
        f"{summary['form_version']} wrong form version, "
        f"{summary['inactive_status']} inactive status, "
        f"{summary['invalid_status_date']} invalid status date"
    )
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
