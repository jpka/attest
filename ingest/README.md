# ADV ingestion — ground truth from the SEC bulk roster

The Battery Registry's roster is generated, not hand-assembled. These two scripts turn the
SEC's Form ADV Part 1A bulk export into `agents/attest_orchestrator/ground_truth.json`, the
reviewable source `publish_registry.py` content-hashes into Firestore.

```
SEC bulk roster CSV ──> select_firms.py ──> real selection ──> anonymize.py ──> ground_truth.json ──> publish_registry.py ──> Firestore
                          │
                          └── adv_schema.py (column map, Item labels transcribed from the form)
```

`select_firms.py` is a port of the private root repo's `resources/data/firms/csv_data/` — the
same code that produced the premise-test ground truth, so a regeneration here is directly
comparable with `premise_test_results_v3.csv`. `anonymize.py` is new: it was added because the
committed ground truth must never name a real firm. Fidelity is the point: the schema's column
indices and Item 5.A–5.I / Item 11 labels were transcribed from Form ADV Part 1A and
validated against the roster's `headers.json` (see the research pack). Do not paraphrase ADV
item text; that is what makes a finding citable.

## How to run

The roster CSV is not committed here — it is a 42 MB SEC export, and real registrant data stays
in the private root repo. Download a fresh copy from the SEC's bulk-data page, then:

```bash
python ingest/select_firms.py <roster.csv> --out /tmp/selected.json   # real firms; never commit
python ingest/anonymize.py /tmp/selected.json                          # writes ground_truth.json (fictional)
python publish_registry.py                                            # content-hash + upload
```

`select_firms.py`'s output is real SEC registrant data, so it must stay out of the repo — its
`--out` default is the scratch file `selected_firms.json`, which is gitignored. `anonymize.py`
then replaces each firm's identity fields and writes the committed, fictionalized
`ground_truth.json`. Note that only the identities are fictionalized, not the quantitative
record shapes (AUM, client counts, compensation, services, disciplinary item codes) — a reader
with the public SEC roster could in principle link those values back to a registrant. Keeping
them real is deliberate: they are the precise values the scorer compares a model's claims against.
Republishing unchanged data is a no-op (the version *is* the content), so
`publish_registry.py` is safe to re-run whenever `ground_truth.json` changes.

## Reproducing the committed roster

The committed `ground_truth.json` (roster `32a6587ad82b`) is fictionalized. It was produced on
2026-08-11 from the `IA_SEC_-_FIRM_ROSTER_FOIA_DOWNLOAD_-_34640308.CSV` release.
`select_firms.py` is deterministic against the pinned `REFERENCE_DATE` — the same roster CSV
always yields the same five real firms — and `anonymize.py` then maps them positionally to the
fixed fictional identities (NORTHBRIDGE CAPITAL ADVISORS, HARBOR & ROWE INVESTMENT COUNSEL,
SENTINEL WEALTH MANAGEMENT PARTNERS, ASPEN CREEK ADVISORY GROUP, CLAREMONT CAPITAL ADVISORS).
Running the two steps against that release therefore yields the identical committed file.
Selection state is deliberately pinned:

- **`REFERENCE_DATE = 2026-08-11`** — the `recent` bucket is "registered in any month within
  the 18 months before the roster release", not "within 18 months of today". The age is
  month-granular: a firm registered 2025-02-25 reads as 18 months at 17.5 actual. A later run
  against a later release should advance this to that release's date.
- **Selection order is roster row order.** The script walks the CSV top to bottom and stops
  when every bucket is full. Different SEC releases may name different firms even at the same
  AUM band; that is expected and why the roster carries a version.
- **Form Version is pinned to `10/2021`.** Rows with any other Form Version are skipped and
  counted in the run summary. Older form versions carry the legacy 5.D layout in different
  columns and would serialize an empty `clients.by_type`, which a scorer would misread as "no
  clients of any type".

## Known limitation, tracked for the Scorer phase

`adv_schema.py` derives `compensation.is_fee_only` and `receives_commissions` from Item 5.E
alone, which mislabels dual registrants — a firm with no 5.E commissions but registered
representatives of a broker-dealer reads as fee-only. In the current roster three firms are
marked `is_fee_only`; two are dual registrants and therefore known-wrong — SENTINEL WEALTH
MANAGEMENT PARTNERS (CRD 900003) and CLAREMONT CAPITAL ADVISORS (CRD 900005) — while ASPEN
CREEK ADVISORY GROUP (CRD 900004) is genuinely fee-only. The Scorer-phase ticket is to fix
the two known-wrong records, not to consider a schema improvement. This is the §6.5 flaw-3 fix
that the Scorer phase (Aug 21–23) applies; it is not fixed here because changing the schema now
would change the roster content and break comparability with the premise-test ground truth. Do
not "fix" it during ingestion.