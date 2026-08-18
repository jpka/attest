# ADV ingestion — ground truth from the SEC bulk roster

The Battery Registry's roster is generated, not hand-assembled. These two scripts turn the
SEC's Form ADV Part 1A bulk export into `agents/attest_orchestrator/ground_truth.json`, the
reviewable source `publish_registry.py` content-hashes into Firestore.

```
SEC bulk roster CSV ──> select_firms.py ──> ground_truth.json ──> publish_registry.py ──> Firestore
                          │
                          └── adv_schema.py (column map, Item labels transcribed from the form)
```

Both files are ports of the private root repo's `resources/data/firms/csv_data/` — the same
code that produced the premise-test ground truth, so a regeneration here is directly
comparable with `premise_test_results_v3.csv`. Fidelity is the point: the schema's column
indices and Item 5.A–5.I / Item 11 labels were transcribed from Form ADV Part 1A and
validated against the roster's `headers.json` (see the research pack). Do not paraphrase ADV
item text; that is what makes a finding citable.

## How to run

The roster CSV is not committed here — it is a 42 MB SEC export and real registrant data stays
in the private root repo. Download a fresh copy from the SEC's bulk-data page, then:

```bash
python ingest/select_firms.py <roster.csv>                # writes ground_truth.json
python ingest/select_firms.py <roster.csv> --out /tmp/gt.json   # dry run / scratch
python publish_registry.py                                # content-hash + upload
```

`--out` defaults to `agents/attest_orchestrator/ground_truth.json`, so the plain invocation
is the entire ingestion path. Republishing unchanged data is a no-op (the version *is* the
content), so `publish_registry.py` is safe to re-run whenever `ground_truth.json` changes.

## Reproducing the committed roster

The committed `ground_truth.json` (roster `3bcbcd032c48`) was selected on 2026-08-11 from the
`IA_SEC_-_FIRM_ROSTER_FOIA_DOWNLOAD_-_34640308.CSV` release. `select_firms.py` is
deterministic — the same roster CSV and the pinned `REFERENCE_DATE` produce the same five
firms — so running it against that release yields the identical roster. Selection state is
deliberately pinned:

- **`REFERENCE_DATE = 2026-08-11`** — the `recent` bucket is "registered within 18 months of
  the roster release", not "within 18 months of today". A later run against a later release
  should advance this to that release's date.
- **Selection order is roster row order.** The script walks the CSV top to bottom and stops
  when every bucket is full. Different SEC releases may name different firms even at the same
  AUM band; that is expected and why the roster carries a version.

## Known limitation, tracked for the Scorer phase

`adv_schema.py` derives `compensation.is_fee_only` and `receives_commissions` from Item 5.E
alone, which mislabels dual registrants — a firm with no 5.E commissions but registered
representatives of a broker-dealer reads as fee-only. This is the §6.5 flaw-3 fix that the
Scorer phase (Aug 21–23) applies; it is not fixed here because changing the schema now would
change the roster content and break comparability with the premise-test ground truth. Do not
"fix" it during ingestion.