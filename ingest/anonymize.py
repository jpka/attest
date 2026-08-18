"""Anonymize the selected ground-truth firms before they are committed.

`select_firms.py` reads the SEC's bulk roster, so the firms it selects are real
registrants with real CRDs, names and addresses. The committed ground truth must
never name a real firm. This script therefore replaces each firm's identity fields
(`crd`, `name`, `legal_name`, `sec_number`, `city`, `state`, `country`, `website`)
with a fixed fictional identity, applied positionally in selection order, and
leaves the rest of the record — AUM, clients, employees, compensation, services,
disciplinary item codes — untouched, so the scorer still tests real record shapes.

The mapping is deterministic: the `i`-th selected firm becomes `FICTIONAL[i]`, so
regenerating from the same roster always yields the same fictionalized file.
"""

import argparse
import copy
import json
import sys
from pathlib import Path

DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / "agents" / "attest_orchestrator" / "ground_truth.json"
)

# Positional onto the selection output order clean/clean/disciplinary/recent/thin_web.
FICTIONAL = [
    {"crd": "900001", "name": "NORTHBRIDGE CAPITAL ADVISORS, LLC",
     "legal_name": "NORTHBRIDGE CAPITAL ADVISORS, LLC", "sec_number": "801-900001",
     "city": "AUSTIN", "state": "TX", "country": "United States",
     "website": "HTTPS://WWW.NORTHBRIDGEADVISORS.EXAMPLE"},
    {"crd": "900002", "name": "HARBOR & ROWE INVESTMENT COUNSEL",
     "legal_name": "HARBOR & ROWE INVESTMENT COUNSEL", "sec_number": "801-900002",
     "city": "PORTLAND", "state": "ME", "country": "United States",
     "website": "HTTP://WWW.HARBORROWE.EXAMPLE"},
    {"crd": "900003", "name": "SENTINEL WEALTH MANAGEMENT PARTNERS, INC.",
     "legal_name": "SENTINEL WEALTH MANAGEMENT PARTNERS, INC.", "sec_number": "801-900003",
     "city": "RALEIGH", "state": "NC", "country": "United States",
     "website": "HTTPS://WWW.SENTINELWM.EXAMPLE"},
    {"crd": "900004", "name": "ASPEN CREEK ADVISORY GROUP",
     "legal_name": "ASPEN CREEK ADVISORY GROUP", "sec_number": "801-900004",
     "city": "BOULDER", "state": "CO", "country": "United States",
     "website": "HTTPS://WWW.ASPENCREEKADVISORY.EXAMPLE"},
    {"crd": "900005", "name": "CLAREMONT CAPITAL ADVISORS",
     "legal_name": "CLAREMONT CAPITAL ADVISORS", "sec_number": "801-900005",
     "city": "NASHVILLE", "state": "TN", "country": "United States",
     "website": ""},
]


def anonymize(firms):
    """Return a new list with each firm's identity replaced by a fixed fictional one."""
    if len(firms) != len(FICTIONAL):
        sys.exit(f"expected {len(FICTIONAL)} selected firms, got {len(firms)}")
    result = []
    for i, firm in enumerate(firms):
        out = copy.deepcopy(firm)
        f = FICTIONAL[i]
        out['crd'] = f['crd']
        out['name'] = f['name']
        out['legal_name'] = f['legal_name']
        out['sec_number'] = f['sec_number']
        out['city'] = f['city']
        out['state'] = f['state']
        out['country'] = f['country']
        out['website'] = f['website']
        result.append(out)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selected_json')
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    args = parser.parse_args()

    with open(args.selected_json, encoding='utf-8') as f:
        firms = json.load(f)
    firms = anonymize(firms)

    for firm in firms:
        print(f"  [{firm['selection_bucket']:12}] CRD {firm['crd']:>7}  "
              f"{firm['name'][:38]:38}  {firm['city']}, {firm['state']}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(firms, f, indent=2)
        f.write("\n")
    print(f"Anonymized {len(firms)} firms -> {args.out}")


if __name__ == "__main__":
    main()
