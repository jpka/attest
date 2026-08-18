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
- `gemini-3.5-flash-lite` is an AI Studio model and is **not** a Vertex publisher model in
  `us-central1`. The deployed image runs Vertex (`GOOGLE_GENAI_USE_ENTERPRISE=1`), so
  `deploy.sh` sets `ATTEST_MODEL=gemini-2.5-flash-lite` for the container; the local default
  stays `gemini-3.5-flash-lite`. A missing `roles/monitoring.metricWriter` shows up as a
  repeating `Failed to export metrics batch code: 403` — granted in `cmd_infra` too.

## Layout

```
agents/attest_orchestrator/
    agent.py            root_agent + 3 tools
    ground_truth.json   the 5 premise-test firms, keyed by CRD
    requirements.txt    extra deps (google-adk comes from the image)
deploy.sh               idempotent gcloud driver
local_test.py           run everything locally first
```

## Run it locally first

```bash
pip install google-adk
python local_test.py
```

All six checks should pass. The last one makes a real model call.

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
uses one, so don't casually add more). Locally, model calls run on `gemini-3.5-flash-lite`
(AI Studio); the deployed image runs `gemini-2.5-flash-lite` on Vertex via `ATTEST_MODEL`
(see the adjustments above). Keep every high-volume loop on a `-flash-lite` model —
`gemini-3.5-flash` is capped at 20 requests/day on free tier and will stall a batch run.

## Design notes carried forward

**Ground truth is keyed by CRD, never by name.** The SEC roster contains distinct firms sharing
a primary business name; name-keying silently merges them. Four of the surviving premise-test
findings are models confusing exactly these entities.

**`append_evidence` is the shape the Evidence Archive keeps**, not a placeholder to be
redesigned. Each entry carries `sha256(payload)`, `prev_hash`, timestamp and model id, so any
retroactive edit is detectable. Aug 24–25 swaps the log line for an append-only Firestore write
plus a GCS object; the entry structure does not change.

**Memory Bank stays separate from the archive.** Memory Bank is the agent's working memory:
semantic, mutable. The Evidence Archive is the legal record: append-only, hash-chained, never
rewritten. Conflating them is the obvious mistake and the separation is the point of the
product — a compliance record an agent can freely edit is not a record.

**The agent's instruction already carries the Part 1A scope limits** from `scorer_prompt_v2.py`.
Those limits are why the premise-test findings survive audit; they belong everywhere a model
touches ADV data, not only in the Scorer.

## Next

Aug 18–19 per the re-plan: move `ground_truth.json` into Firestore and stand up the Battery
Registry with the existing content-hash scheme (`BATTERY_VERSION` in `premise_test.py`) rather
than inventing a second one. Only `_load_firms()` in `agent.py` should need to change.
