# Attest — deploy path skeleton

Cloud Scheduler → Pub/Sub → ADK agent on Cloud Run, with Firestore and Cloud Trace.
This is the Aug 17 phase of `attest-replan-0816.md`: prove the deployment before building
on it. It is deliberately thin. What it is *not* is the finished orchestrator.

**Three of the five qualifying Google Cloud infrastructure services** named in the hackathon
rules (Cloud Run, Pub/Sub, Firestore) plus the ADK agent framework requirement. Agent Engine
is not required and is not used here.

## What's verified vs what isn't

Verified in a sandbox against `google-adk==2.7.0`:

- the agent and its three tools load, and the ADV lookup returns the right firm by CRD
- the hash chain links (`prev_hash` of entry N equals `entry_hash` of entry N-1)
- `get_fast_api_app(..., trigger_sources=["pubsub"])` registers
  `POST /apps/{app_name}/trigger/pubsub`
- a real Pub/Sub push envelope routes through that endpoint, decodes, loads the agent and
  reaches the model call

**Not verified — you are the first to run these:** anything touching Google Cloud. The
`gcloud` invocations in `deploy.sh` were written against current flags but never executed.
Expect one or two to need adjusting; that is what today is for.

**Status after the Aug 18 deployment:** the full path (`./deploy.sh all`) runs end to end.
Three adjustments were needed and are baked into `deploy.sh`:

- Cloud Build needs the default compute SA granted `roles/cloudbuild.builds.builder`, or
  `gcloud run deploy --source` dies on the `run-sources` bucket with `PERMISSION_DENIED`
  (added to `cmd_infra`).
- `--trace_to_cloud`/`--otel_to_cloud` lazy-import the OTel GCP exporters at startup and the
  container crashes before the port opens if they're absent — `requirements.txt` carries the
  `google-adk[otel-gcp]` extras.
- The model is served from a different location than the service runs in. `gemini-3.5-flash-lite`
  404s on every *regional* Vertex endpoint (verified Aug 18 against `us-central1`, `us-east5`,
  `us-west1`, `europe-west4`) but resolves on Vertex's **`global`** location, which is where the
  3.x generation is published. So `deploy.sh` sets `GOOGLE_CLOUD_LOCATION=global` for model calls
  while Cloud Run, Firestore and Pub/Sub stay in `$REGION`; `ATTEST_MODEL` stays
  `gemini-3.5-flash-lite` on both surfaces. Overridable via `ATTEST_MODEL_LOCATION`.
  An earlier revision of this file concluded 3.x was AI-Studio-only and pinned the container to
  `gemini-2.5-flash-lite`; that would have run the battery on a different model generation than
  the one every premise-test result was measured on.
- A missing `roles/monitoring.metricWriter` shows up as a repeating
  `Failed to export metrics batch code: 403` — granted in `cmd_infra` too.

## Layout

```
agents/attest_orchestrator/
    agent.py            root_agent + 3 tools
    registry.py         Battery Registry — content-addressed ground truth
    ground_truth.json   the 5 premise-test firms; the reviewable source
    requirements.txt    extra deps (google-adk comes from the image)
deploy.sh               idempotent gcloud driver
publish_registry.py     ground_truth.json -> Firestore
local_test.py           run everything locally first
```

## Run it locally first

```bash
pip install google-adk google-cloud-firestore
gcloud auth application-default login   # ground truth is a Firestore read
python publish_registry.py              # once, or after editing ground_truth.json
python local_test.py
```

All nine checks should pass. The last one makes a real model call.

## Then deploy

```bash
# gcloud auth login - already done
# gcloud config set project attest-505313
# export ATTEST_PROJECT=attest-505313
# export ATTEST_REGION=us-central1

./deploy.sh apis      # ~2 min, once
./deploy.sh infra     # service accounts, Pub/Sub topic, Firestore
./deploy.sh deploy    # Cloud Build + Cloud Run, ~4 min
./deploy.sh wire      # push subscription + Cloud Scheduler
./deploy.sh smoke     # publish one message, tail the logs
```

`./deploy.sh all` does the lot. Every step is safe to re-run.

## What "working" looks like

`./deploy.sh smoke` should show a 200 on the trigger route and an `evidence.append` log line
containing an `entry_hash`. Then open Cloud Trace — the run should appear as a trace with the
agent's tool calls as spans. **Screenshot that now.** It is the Agent Observability evidence
the demo video needs, and finding out in week three that tracing was never enabled is a bad
day.

## Cost

Everything here sits inside Always Free: Cloud Run (2M requests/month), Firestore (1 GiB,
50k reads/day), Pub/Sub (10 GiB/month), Cloud Scheduler (3 jobs per *billing account* — this
uses one, so don't casually add more). Model calls run on `gemini-3.5-flash-lite` both
locally (AI Studio) and deployed (Vertex, `global` location — see the adjustments above), so
deployed runs stay comparable with the premise-test corpus. Vertex bills per token with no free
tier; a full battery run is single-digit dollars against the $150 credit. Keep every high-volume
loop on a `-flash-lite` model —
`gemini-3.5-flash` is capped at 20 requests/day on free tier and will stall a batch run.

## The Battery Registry

Ground truth is not a file the agent ships with. It is a **content-addressed roster** in
Firestore, so every run can name the version it was scored against:

```
rosters/{version}                 metadata: firm_count, crds, source
rosters/{version}/firms/{crd}     one firm, native fields
registry/current                  pointer: {"roster_version": ...}
```

`{version}` is `sha256(json.dumps(roster, sort_keys=True))[:12]` — deliberately the same
twelve-character scheme as `BATTERY_VERSION` in `premise_test.py`, not a second one that looks
similar. A roster version and a battery version are comparable strings and a run records both.

Two properties fall out of content addressing. Republishing unchanged data is a no-op that
lands on identical document paths, so the publisher is safe to re-run. And editing the source
produces a *new* version rather than mutating one in place, so a roster is immutable under its
own name — which is what makes "scored against roster 794e76b2e12f" a claim that means
something a month later.

`ATTEST_ROSTER_VERSION` pins a run to a specific roster. Unset means "follow
`registry/current`", which is right for a scheduled run and wrong for re-scoring an old one.

**No fallback to the bundled JSON.** A missing roster or missing credentials raises. An agent
that silently reads some other ground truth produces a run that looks normal and is scored
against a roster nobody chose — the exact failure mode this product exists to detect.

## Design notes carried forward

**Ground truth is keyed by CRD, never by name.** The SEC roster contains distinct firms sharing
a primary business name; name-keying silently merges them. Four of the surviving premise-test
findings are models confusing exactly these entities.

**`append_evidence` is the shape the Evidence Archive keeps**, not a placeholder to be
redesigned. Each entry carries `sha256(payload)`, `prev_hash`, timestamp and model id, so any
retroactive edit is detectable. Aug 24–25 swaps `_chain_tail`/`_commit` for an append-only
Firestore write plus a GCS object; the entry structure does not change.

**The agent cannot supply the linkage.** `append_evidence` takes only a payload; it reads the
tail itself and computes `prev_hash`. The earlier version accepted `prev_hash` as a tool
argument, which let the model fork or reset the chain by passing a stale value — undetectable
downstream, and the exact failure the product exists to prevent. `local_test.py` asserts the
parameter stays gone. Until Aug 24–25 the tail is in-process, so the chain is per-instance and
does not survive a restart; the Firestore transaction fixes durability and must also reject a
stale tail rather than overwrite it.

**Open question for Aug 24–25:** a pure hash chain detects edits but not *truncation* — dropping
the last N entries leaves a valid chain. A monotonic sequence number in the entry closes that.
It is one field, and it is cheaper to add before the archive is written than after.

**Memory Bank stays separate from the archive.** Memory Bank is the agent's working memory:
semantic, mutable. The Evidence Archive is the legal record: append-only, hash-chained, never
rewritten. Conflating them is the obvious mistake and the separation is the point of the
product — a compliance record an agent can freely edit is not a record.

**The agent's instruction already carries the Part 1A scope limits** from `scorer_prompt_v2.py`.
Those limits are why the premise-test findings survive audit; they belong everywhere a model
touches ADV data, not only in the Scorer.

## Next

Aug 19 per the re-plan: ADV ingestion — port `adv_schema.py` and `select_firms.py` so the
roster is generated from the SEC bulk data rather than hand-assembled, then republish it. The
registry layout above does not change; only what feeds `ground_truth.json` does.

Aug 20: run the probe battery through the deployed runtime via Pub/Sub, recording both the
roster version and `BATTERY_VERSION` on each run.
