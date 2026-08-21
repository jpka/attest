# Attest

**A compliance agent that audits what a model claims about SEC-registered investment
advisers — and records what it could not verify.**

Ask a general-purpose model a factual question about a registered investment adviser and
it will usually answer. Some of those answers are right. Some are confidently wrong in
ways that matter to a compliance officer: a firm's disciplinary history, whether it is
fee-only, how many of its representatives are also registered reps of a broker-dealer.
Attest runs a fixed battery of those questions on a schedule, scores every answer against
Form ADV Part 1A ground truth, and writes the result into an append-only hash-chained
record.

The product's thesis is the part most demos skip: **it refuses to adjudicate what it
cannot source, and it tells you which document it would have needed.** A scorer that
returns a verdict for every question is easy to build and worthless to rely on.

Open [`docs/architecture.html`](docs/architecture.html) for the architecture diagram —
a standalone file, no build step.

---

## Google Cloud services used

| Service | Role here |
|---|---|
| **Cloud Run** | the ADK agent, `--no-allow-unauthenticated`, min 0 / max 3 |
| **Pub/Sub** | topic `attest-runs` + authenticated push subscription |
| **Cloud Scheduler** | `attest-monthly`, `0 6 1 * *` UTC |
| **Firestore** (Native) | Battery Registry, evidence-chain tail |
| **Cloud Storage** | one immutable object per evidence entry |
| **Vertex AI** | subject model `gemini-3.5-flash-lite`; Memory Bank reasoning engine |
| **Cloud Trace / Logging / Monitoring** | agent tool calls as spans |

Built on the **Google Agent Development Kit** (`google-adk`). Agent Engine is not used.

---

## The one non-obvious thing about this deployment

**The model is served from a different location than the service runs in.**

`gemini-3.5-flash-lite` 404s on every *regional* Vertex endpoint — verified against
`us-central1`, `us-east5`, `us-west1` and `europe-west4` — and resolves only on Vertex's
**`global`** location, which is where the 3.x generation is published. So Cloud Run,
Firestore, Pub/Sub and the Memory Bank engine stay in `$REGION` while
`GOOGLE_CLOUD_LOCATION=global` for model calls.

The Memory Bank engine cannot share that variable: reasoning engines are *regional*
resources and do not exist in `global`. That is why `ATTEST_MEMORY_LOCATION` is separate
from `ATTEST_MODEL_LOCATION` rather than one "location" setting.

An earlier revision of this file concluded the 3.x models were AI-Studio-only and pinned
the container to `gemini-2.5-flash-lite`. That would have run the battery on a different
model generation than the one every premise-test result was measured on — the results
would have looked fine and meant nothing.

---

## Spin it up

### 0. Prerequisites

```bash
gcloud auth login
gcloud config set project <your-project-id>
export ATTEST_PROJECT=<your-project-id>
export ATTEST_REGION=us-central1
pip install google-adk google-cloud-firestore google-cloud-storage
```

`adk` must be on `$PATH` — `./deploy.sh deploy` shells out to it.

### 1. Local first

Running locally costs nothing but a model call and turns a Cloud Run failure into an
infrastructure problem rather than an agent problem, which is the whole point of doing it.

```bash
export GOOGLE_API_KEY=<AI Studio key>
gcloud auth application-default login   # ground truth is a Firestore read
python publish_registry.py              # once, or after editing ground_truth.json
python local_test.py
```

`publish_registry.py` is **not optional and must run first**. Ground truth lives in
Firestore, not in the container, so an unpublished roster means `local_test.py` fails at
the registry check rather than silently scoring against something else.

### 2. Deploy

```bash
./deploy.sh apis      # enable services — once, ~2 min
./deploy.sh infra     # service accounts, IAM, Pub/Sub topic, Firestore, GCS bucket
./deploy.sh memory    # Memory Bank reasoning engine
./deploy.sh deploy    # Cloud Build + Cloud Run, ~4 min
./deploy.sh wire      # push subscription + Cloud Scheduler
./deploy.sh smoke     # publish one message, tail the logs
```

`./deploy.sh all` runs the lot in that order. Every step is idempotent and safe to re-run:
`infra` skips what exists, and `memory` reuses an engine by display name rather than
accumulating duplicates.

**Re-run `infra` after pulling changes, not just once.** It is the step that creates the
GCS evidence bucket and grants `storage.objectCreator`. Those were added after `infra` had
already been run successfully, so a deployment that skipped the re-run had a live service,
green CI, and an Evidence Archive that 404'd on every append. See the note on this below.

### 3. What "working" looks like

`./deploy.sh smoke` should show, in the logs it tails:

```
INFO - evidence_archive.py:181 - evidence.append seq=3 hash=5c2a91abee001c66
INFO:     "POST /apps/attest_orchestrator/trigger/pubsub HTTP/1.1" 200 OK
```

A `200` on the trigger route **and** an `evidence.append` line with a hash. The 200 alone
is not sufficient — that is the failure mode described below.

Then open Cloud Trace: the run appears as a trace with the agent's tool calls as spans.
A `500` here is what Pub/Sub sees as a nack, so it will retry with backoff.

---

## Layout

```
agents/attest_orchestrator/
    agent.py              root_agent + the six model-reachable tools
    registry.py           Battery Registry — content-addressed ground truth
    evidence_archive.py   append-only SHA-256 hash chain on Firestore + GCS
    memory_bank.py        Vertex AI Memory Bank; purge stays operator-only
    scorer.py             the v2 rubric
    scorer_prompts.py     Part 1A scope limits, shared with the agent instruction
    ground_truth.json     fictionalized roster; the reviewable source
ingest/
    adv_schema.py         Form ADV Part 1A column map — the ground-truth schema
    select_firms.py       SEC bulk roster -> real selection (gitignored, never committed)
    anonymize.py          real selection -> fictionalized ground_truth.json
deploy.sh                 idempotent gcloud driver
publish_registry.py       ground_truth.json -> Firestore
local_test.py             run everything locally first
tests/                    126 tests; ruff + pytest on every push and PR
```

## The Battery Registry

Ground truth is not a file the agent ships with. It is a **content-addressed roster** in
Firestore, so every run can name the version it was scored against:

```
rosters/{version}                 metadata: firm_count, crds, source
rosters/{version}/firms/{crd}     one firm, native fields
registry/current                  pointer: {"roster_version": ...}
```

`{version}` is `sha256(json.dumps(roster, sort_keys=True))[:12]` — deliberately the same
twelve-character scheme as `BATTERY_VERSION`, not a second one that looks similar. A
roster version and a battery version are comparable strings, and a run records both.

Two properties fall out of content addressing. Republishing unchanged data is a no-op that
lands on identical document paths, so the publisher is safe to re-run. And editing the
source produces a *new* version rather than mutating one in place, so a roster is immutable
under its own name — which is what makes "scored against roster `32a6587ad82b`" a claim
that still means something a month later.

`ATTEST_ROSTER_VERSION` pins a run to a specific roster. Unset means "follow
`registry/current`", which is right for a scheduled run and wrong for re-scoring an old one.

**No fallback to the bundled JSON.** A missing roster or missing credentials raises. An
agent that silently reads some other ground truth produces a run that looks normal and is
scored against a roster nobody chose — the exact failure mode this product exists to detect.

## The Evidence Archive

Firestore + Cloud Storage, not an in-process dict. Each entry carries `payload_sha256`,
`prev_hash`, a monotonic `sequence`, a timestamp and the model id, so any retroactive edit
is detectable.

A pure hash chain detects edits but not **truncation** — dropping the last N entries leaves
a valid chain. The sequence number closes that: a gap is evidence. The tail lives in
Firestore at `evidence_chain/meta` and is advanced inside a transaction with optimistic
concurrency, so two Cloud Run instances cannot fork the chain. The payload is written to
GCS *after* the transaction commits, with `if_generation_match=0`, so a retry is idempotent
rather than a second entry.

**That ordering leaves a window, and it is closed explicitly.** Firestore commits first on
purpose — writing GCS first would orphan an object whose entry never joined the chain, with
no tail to reconcile it against. But it means a failure between the two steps leaves a
committed entry and an advanced tail with no durable object behind it, and the next append
would read that tail, succeed, and bury the gap one entry deeper.

`reconcile_tail()` runs before every append: if the tail's object is missing, it re-writes
**that same committed entry** rather than rolling the chain back. Rolling back would retract
a hash that may already have been reported to a caller, and because entries are
content-addressed, a re-write is either byte-identical or it is corruption — nothing is
invented. It refuses loudly rather than guessing when the tail names a sequence with no entry
document, when the entry's own `sequence` disagrees with its document id, when the entry fails
its own verification, or when an object already sitting at the path is not the committed entry.

**`if_generation_match=0` is overwrite protection, not request idempotency, and the
difference matters.** It guarantees an existing object is never silently replaced. It does not
make a *retried call* a no-op: after Firestore commits and the GCS write fails, retrying
`append` repairs the prior object and then appends a **second entry**, because nothing in the
request identifies it as the same logical append. Genuine end-to-end idempotency needs a
durable client-supplied request id, which this does not have. What is guaranteed is narrower
and stated deliberately: **no entry is ever lost or overwritten, and no gap survives the next
append.** A duplicate entry for a retried request is possible; a silently altered or missing
one is not.

This is not a hypothetical window. Entries 1 and 2 of the live chain exist in Firestore with
no GCS object, permanently, because the bucket did not exist when they were written and
nothing reconciled before entry 3 was appended. See the note further down.

**The agent cannot supply the linkage.** `append_evidence` takes only a payload; it reads
the tail itself and computes `prev_hash`. An earlier version accepted `prev_hash` as a tool
argument, which let the model fork or reset the chain by passing a stale value —
undetectable downstream, and precisely the failure the product exists to prevent.
`local_test.py` asserts the parameter stays gone.

## Memory Bank stays separate from the archive

Memory Bank is the agent's working memory: semantic, mutable, consolidating. The Evidence
Archive is the legal record: append-only, hash-chained, never rewritten. Conflating them is
the obvious mistake, and keeping them apart is the point of the product — **a compliance
record an agent can freely edit is not a record.**

`purge_firm_memory` exists and is **not attached to the agent.** A Pub/Sub-triggered run
reaches `root_agent.tools` with a payload the operator did not write, and a purge with
`dry_run=False` deletes every memory for a firm. It is operator-only, the agent instruction
states the model has no delete tool, and `local_test.py` asserts the absence rather than
trusting the tool list to stay correct. It also defaults to `dry_run=True`, because the
Vertex `purge` API treats `force: false` as a dry run that reports a count and deletes
nothing.

## Design notes

**Ground truth is keyed by CRD, never by name.** The SEC roster contains distinct firms
sharing a primary business name; name-keying silently merges them. Four of the surviving
premise-test findings are models confusing exactly these entities.

**The agent's instruction carries the Part 1A scope limits** from `scorer_prompts.py`. Those
limits are why the findings survive audit; they belong everywhere a model touches ADV data,
not only in the scorer.

---

## Stated limits

These are scope boundaries, not bugs, and each one is deliberate.

**Category C — fees and account minimums — returns UNVERIFIABLE by construction.** Fee
schedules and account minimums live in **ADV Part 2A**, and the ground-truth layer here
ingests Part 1A only. The scorer therefore cannot adjudicate a fee-rate claim, and rather
than guessing it returns UNVERIFIABLE and **names Part 2A as the document it would have
needed.** Ingesting Part 2A brochures was scoped and deliberately rejected: it was new scope
on a schedule that reserved against new scope, and "this scorer refuses to adjudicate what
it cannot source" is the thesis rather than an apology for a thin category.

**`is_fee_only` is derived, and derived conservatively.** `ingest/README.md` documents the
cases where the Part 1A columns do not settle the question.

**Five firms, not five hundred.** The roster is deliberately small enough to be reviewed by
hand. The scoring machinery does not care about the count.

---

## Data provenance and the research pack

**Nothing published here names a real firm.**

The firm identities in `ground_truth.json` — names, CRDs, SEC numbers, addresses, websites —
are **synthetic**. The roster covers CRDs `900001`–`900005`, which are not assigned to any
registrant. Everything in this repository, the demo video and every screenshot uses those
fictional firms.

**What is real is the shape of the records.** The quantitative values — AUM, employee and
representative counts, client-type breakdowns, disciplinary-item structure — are taken from
real Form ADV Part 1A filings in the SEC's public bulk roster release of 2026-08-11. They
are real because they are what the scorer compares a model's claims against; a synthetic
distribution would make the findings untestable. `select_firms.py` performs the real
selection and its output is **gitignored and never committed**; `anonymize.py` applies a
deterministic positional fictionalization before `ground_truth.json` is written. The
committed roster `32a6587ad82b` regenerates identically from that SEC release followed by
that anonymization.

**Residual re-identification risk, acknowledged plainly.** The quantitative values are real
Form ADV figures, so a reader holding the same public bulk roster could in principle link a
record back to the originating registrant. Identities are fictionalized; the values are not.
Form ADV Part 1A is public record — including Item 11 disciplinary disclosure — so this is
not confidential data and there is no disclosure obligation attached to it. The rule being
kept is a self-imposed one: **a compliance product should not make an unsolicited
disciplinary claim about a named real adviser**, least of all in a demo. That rule is why
the published history was rewritten to remove real registrant identifiers before this
repository was made public, and why the pre-publication check sweeps the full reachable
history by content rather than re-checking the files someone expected to be affected.

**The research pack** — the premise-test corpus, the regrade report and the ADV ingestion
review — stays in a private repository. It contains real registrant data, including a real
firm's Item 11 disciplinary disclosure, and is the evidence base the findings were measured
on. It is available on request for judging.

**Known exceptions, enumerated.** The pre-publication sweep is scoped by *content*
rather than by the files an inspector expected to be affected — that is how the first leak
was found — and it covers the full reachable history. It returns two instances that survive
in published history by design, listed here in full:

| Where | What | Why it stays |
|---|---|---|
| Stored diff of PR #3 | A CRD-validation fixture in `tests/test_memory_bank.py` carried a real registrant's CRD. The forward fix landed in `e12969a`; a `filter-repo` pass cleared it from all reachable commits. | A pull request's stored diff survives any force-push. Only a repository rebuild would clear it, and that would destroy PRs #1–#4 with their review threads and CI history. <!-- not-a-crd --> |
| Commits `7dc2a3c`…`3dace00` | The comment written to explain that fix named real CRDs itself. Removed from the working tree in this change. | Same reason. It is reachable history; clearing it means a second rewrite of an already-public repository, which does not unpublish anything. |

Neither instance names a firm or asserts anything about one; both are bare integers in test
scaffolding and prose. They are recorded because the rule is unqualified, and a rule carrying
silent exceptions for the cases someone judged harmless does not survive its next application.

**How the second one happened, because it is the more instructive of the two.** The commit
whose stated purpose was removing a real CRD from the test fixtures added a comment naming
several more, in the same file. The `filter-repo` pass that followed swept for the *quoted*
fixture literal and cleared it everywhere; the comment used the bare, unquoted form and was
untouched. Scoping a content sweep to the form you expect the value to take is the same
mistake as scoping it to the files you expect to be affected, one level down.

**The rule, restated so it is enforceable.** "No real registrant identifier in published
history" is no longer achievable — the two rows above cannot be cleared without costs worse
than the exposure. A rule that is permanently violated is not a rule, so it is split into
three that hold:

1. **The working tree contains no real registrant identifier.** Enforced by
   `tests/test_no_real_crd_in_tree.py`, which fails the build on either form — CRD-position
   (`"crd": "<n>"`) or bare integer — and is the check that would have caught `7dc2a3c` on
   the day it landed.
2. **Reachable history and stored PR diffs contain exactly the two instances above.**
   Re-derived, not assumed, whenever the sweep is run.
3. **The forward check runs pre-publication, against the diff**, not retrospectively against
   history. Retrospective sweeps found four separate things this repository had already
   published; each was scoped by something the next one had to widen.

---

## A failure this repo learned the hard way

`deploy.sh infra` gained the GCS bucket creation and the `storage.objectCreator` grant when
the Evidence Archive shipped — several days *after* `infra` had last been run. Nothing
re-ran it. The result:

- CI green. 113 tests passing, including 16 for the archive, all against fakes.
- `bash -n deploy.sh` clean.
- Cloud Run live and serving.
- Every `append_evidence` call 404ing on `The specified bucket does not exist`, and the
  Pub/Sub trigger returning 500 — for the one component the plan called "never cut."

The tests were right, the script was right, and the deployment was broken, because the tests
mocked the bucket and the script had not been executed since the code that needed the bucket
was written. Two related lessons, both earned rather than assumed:

**A script that parses is not a script that has run.** An earlier version of `cmd_memory`
passed `bash -n` and could not survive its own first loop iteration: under `set -e` a failing
command substitution aborts at the assignment, so the `rc=$?` meant to catch a not-yet-done
poll was unreachable. Underneath that sat a second defect it was hiding — an f-string
expression containing a backslash, a `SyntaxError` on Python 3.11. Each bug concealed the
other, and the operator-visible symptom was a timeout message for a compile error.

**Idempotent means "safe to re-run," which is only useful if you actually re-run it.** Every
step here is idempotent by design. That property bought nothing while nobody exercised it.

Both are now verified by execution rather than by reading: `apis`, `infra`, `memory`,
`deploy`, `wire` and `smoke` have each been run end to end against the live project, and the
hash chain has been read back out of Firestore and Cloud Storage — `prev_hash` of entry 4
equals `entry_hash` of entry 3, and the Firestore tail agrees with both.

**And it left a permanent scar, which is the useful part.** Reading the two stores side by
side shows Firestore holding entries 1, 2, 3, 4 and Cloud Storage holding only `3.json` and
`4.json`. Entries 1 and 2 committed during the window when the bucket did not exist; their
payload objects were never written and cannot be reconstructed, because the only durable copy
was the one that failed. The chain still verifies — the hashes link and the sequence has no
gap — which is exactly what makes it worth stating plainly rather than quietly renumbering:
**a hash chain proves nothing was altered, not that everything was stored.**

That is the concrete case for `reconcile_tail()`, which now runs before every append and
would have caught this at entry 2 instead of leaving it for a code review to find at entry 4.

---

## Cost

Everything outside the model calls sits inside Always Free: Cloud Run (2M requests/month),
Firestore (1 GiB, 50k reads/day), Pub/Sub (10 GiB/month), Cloud Storage (5 GiB), Cloud
Scheduler (3 jobs per *billing account* — this uses one, so don't casually add more).

Vertex bills per token with no free tier; a full battery run is single-digit dollars. Keep
every high-volume loop on a `-flash-lite` model — `gemini-3.5-flash` is capped at 20
requests/day on free tier and will stall a batch run.
