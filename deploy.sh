#!/usr/bin/env bash
# Attest — deploy path. Cloud Scheduler -> Pub/Sub -> ADK agent on Cloud Run.
#
# Idempotent: safe to re-run. Every step prints what it did.
# Run steps 1-3 once, then step 4 whenever the agent changes.
#
#   ./deploy.sh apis        enable services (once, ~2 min)
#   ./deploy.sh infra       service accounts, Pub/Sub topic, Firestore
#   ./deploy.sh deploy      build + deploy the agent to Cloud Run
#   ./deploy.sh wire        push subscription + Cloud Scheduler job
#   ./deploy.sh memory      provision the Memory Bank reasoning engine
#   ./deploy.sh smoke       publish one message and tail the logs
#   ./deploy.sh all         apis, infra, deploy, wire, smoke
set -euo pipefail

PROJECT="${ATTEST_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${ATTEST_REGION:-us-central1}"
# Where the MODEL is served, which is not where the SERVICE runs. The 3.x Gemini
# models resolve only on Vertex's `global` location — every regional endpoint
# 404s them (verified Aug 18 against us-central1, us-east5, us-west1,
# europe-west4). Cloud Run, Firestore and Pub/Sub stay in $REGION.
MODEL_LOCATION="${ATTEST_MODEL_LOCATION:-global}"
# Keep this the model the premise-test corpus was measured on, or the battery
# results stop being comparable with premise_test_results_v3.csv.
MODEL="${ATTEST_MODEL:-gemini-3.5-flash-lite}"
SERVICE="attest-orchestrator"
APP="attest_orchestrator"          # must match the folder under agents/
TOPIC="attest-runs"
SUBSCRIPTION="attest-runs-push"
JOB="attest-monthly"
RUNTIME_SA="attest-runtime"        # what the service runs as
PUSH_SA="attest-pubsub-push"       # what Pub/Sub authenticates as

[[ -n "$PROJECT" ]] || { echo "Set ATTEST_PROJECT or run: gcloud config set project <id>"; exit 1; }
RUNTIME_SA_EMAIL="${RUNTIME_SA}@${PROJECT}.iam.gserviceaccount.com"
PUSH_SA_EMAIL="${PUSH_SA}@${PROJECT}.iam.gserviceaccount.com"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

ensure_sa() {
  gcloud iam service-accounts describe "$1" --project "$PROJECT" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "${1%%@*}" --project "$PROJECT" --display-name "$2"
}

cmd_apis() {
  say "Enabling APIs (safe to re-run)"
  gcloud services enable --project "$PROJECT" \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    pubsub.googleapis.com \
    cloudscheduler.googleapis.com \
    firestore.googleapis.com \
    aiplatform.googleapis.com \
    cloudtrace.googleapis.com \
    secretmanager.googleapis.com
}

cmd_infra() {
  say "Service accounts"
  ensure_sa "$RUNTIME_SA_EMAIL" "Attest Cloud Run runtime"
  ensure_sa "$PUSH_SA_EMAIL"    "Attest Pub/Sub push identity"

  say "Runtime permissions (least privilege — no project editor)"
  for role in roles/datastore.user roles/aiplatform.user \
              roles/cloudtrace.agent roles/logging.logWriter \
              roles/monitoring.metricWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT" \
      --member "serviceAccount:${RUNTIME_SA_EMAIL}" --role "$role" \
      --condition=None --quiet >/dev/null
    echo "  granted $role"
  done

  say "Default compute SA gets Cloud Build (needed for 'gcloud run deploy --source')"
  local project_number; project_number="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${project_number}-compute@developer.gserviceaccount.com" \
    --role roles/cloudbuild.builds.builder \
    --condition=None --quiet >/dev/null
  echo "  granted roles/cloudbuild.builds.builder to ${project_number}-compute@developer.gserviceaccount.com"

  say "Pub/Sub topic"
  gcloud pubsub topics describe "$TOPIC" --project "$PROJECT" >/dev/null 2>&1 \
    || gcloud pubsub topics create "$TOPIC" --project "$PROJECT"

  say "Firestore (Native mode). Skips if a database already exists."
  gcloud firestore databases describe --project "$PROJECT" >/dev/null 2>&1 \
    || gcloud firestore databases create --project "$PROJECT" --location "$REGION" --type firestore-native

  say "GCS bucket for the Evidence Archive"
  local bucket="${PROJECT}-evidence"
  if gsutil ls -b "gs://${bucket}" >/dev/null 2>&1; then
    echo "  bucket ${bucket} exists"
  else
    gsutil mb -p "$PROJECT" -l "$REGION" "gs://${bucket}"
    echo "  created ${bucket}"
  fi
  gsutil uniformbucketlevelaccess set on "gs://${bucket}"
  gcloud storage buckets add-iam-policy-binding "gs://${bucket}" \
    --member "serviceAccount:${RUNTIME_SA_EMAIL}" --role roles/storage.objectCreator \
    --quiet >/dev/null
  echo "  granted storage.objectCreator to ${RUNTIME_SA_EMAIL}"
  # CR: export for the deploy step — but cmd_deploy also computes this
  # from $PROJECT, so a standalone ./deploy.sh deploy works without infra.
  export ATTEST_EVIDENCE_BUCKET="$bucket"
}

cmd_deploy() {
  say "Deploying $APP to Cloud Run as $SERVICE"
  # --trigger_sources=pubsub registers POST /apps/{app}/trigger/pubsub
  # --trace_to_cloud + --otel_to_cloud give the Agent Observability requirement.
  # Everything after -- is passed straight to `gcloud run deploy`.
  local env_vars="GOOGLE_GENAI_USE_ENTERPRISE=1,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${MODEL_LOCATION},ATTEST_MODEL=${MODEL}"
  if [[ -n "${ATTEST_EVIDENCE_BUCKET:-}" ]]; then
    env_vars="${env_vars},ATTEST_EVIDENCE_BUCKET=${ATTEST_EVIDENCE_BUCKET}"
  fi
  if [[ -n "${ATTEST_MEMORY_ENGINE_ID:-}" ]]; then
    env_vars="${env_vars},ATTEST_MEMORY_ENGINE_ID=${ATTEST_MEMORY_ENGINE_ID}"
  fi
  adk deploy cloud_run \
    --project "$PROJECT" \
    --region "$REGION" \
    --service_name "$SERVICE" \
    --app_name "$APP" \
    --trigger_sources pubsub \
    --trace_to_cloud \
    --otel_to_cloud \
    --log_level info \
    ./agents/"$APP" \
    -- \
    --no-allow-unauthenticated \
    --service-account "$RUNTIME_SA_EMAIL" \
    --min-instances 0 \
    --max-instances 3 \
    --memory 1Gi \
    --set-env-vars "$env_vars"
}

service_url() {
  gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
    --format 'value(status.url)'
}

cmd_wire() {
  local url; url="$(service_url)"
  [[ -n "$url" ]] || { echo "Service not deployed yet — run: $0 deploy"; exit 1; }
  say "Service URL: $url"

  say "Letting Pub/Sub invoke the service"
  gcloud run services add-iam-policy-binding "$SERVICE" \
    --project "$PROJECT" --region "$REGION" \
    --member "serviceAccount:${PUSH_SA_EMAIL}" --role roles/run.invoker --quiet >/dev/null

  say "Push subscription -> /apps/${APP}/trigger/pubsub"
  local endpoint="${url}/apps/${APP}/trigger/pubsub"
  if gcloud pubsub subscriptions describe "$SUBSCRIPTION" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud pubsub subscriptions update "$SUBSCRIPTION" --project "$PROJECT" \
      --push-endpoint "$endpoint" \
      --push-auth-service-account "$PUSH_SA_EMAIL"
  else
    gcloud pubsub subscriptions create "$SUBSCRIPTION" --project "$PROJECT" \
      --topic "$TOPIC" \
      --push-endpoint "$endpoint" \
      --push-auth-service-account "$PUSH_SA_EMAIL" \
      --ack-deadline 600 \
      --min-retry-delay 60s --max-retry-delay 600s
  fi

  say "Cloud Scheduler — monthly, 06:00 UTC on the 1st"
  # Free tier is 3 jobs per BILLING ACCOUNT, not per project. Keep it to one.
  local args=(--project "$PROJECT" --location "$REGION"
              --schedule "0 6 1 * *" --time-zone UTC
              --topic "$TOPIC" --message-body '{"run":"monthly","crd":"900001"}')
  gcloud scheduler jobs describe "$JOB" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1 \
    && gcloud scheduler jobs update pubsub "$JOB" "${args[@]}" \
    || gcloud scheduler jobs create pubsub "$JOB" "${args[@]}"
}

cmd_memory() {
  say "Memory Bank — Vertex AI reasoning engine"
  # The engine is provisioned via REST because gcloud has no first-class
  # reasoning-engine create with memoryBankConfig. The service agent needs
  # roles/aiplatform.user (granted Aug 20) to generate embeddings.
  local project_number; project_number="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
  local base="https://${REGION}-aiplatform.googleapis.com/v1beta1"
  local parent="projects/${PROJECT}/locations/${REGION}"
  local token; token="$(gcloud auth print-access-token)"

  # Reuse an existing engine if ATTEST_MEMORY_ENGINE_ID is already set.
  if [[ -n "${ATTEST_MEMORY_ENGINE_ID:-}" ]]; then
    echo "  ATTEST_MEMORY_ENGINE_ID=${ATTEST_MEMORY_ENGINE_ID} already set; skipping create"
    return 0
  fi

  local response; response=$(curl -s -X POST \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    "${base}/${parent}/reasoningEngines" \
    -d "{
      \"displayName\": \"attest-memory-bank\",
      \"description\": \"Attest surveillance working memory (Memory Bank).\",
      \"contextSpec\": {
        \"memoryBankConfig\": {
          \"generationConfig\": {\"model\": \"projects/${PROJECT}/locations/${REGION}/publishers/google/models/gemini-2.5-flash\"},
          \"similaritySearchConfig\": {\"embeddingModel\": \"projects/${PROJECT}/locations/${REGION}/publishers/google/models/text-embedding-005\"}
        }
      }
    }")
  local op_name; op_name="$(echo "$response" | python3 -c 'import sys,json; print(json.load(sys.stdin)["name"])')"
  echo "  create LRO: $op_name"

  local engine_name=""
  for _ in $(seq 1 40); do
    local op; op=$(curl -s -H "Authorization: Bearer ${token}" "${base}/${op_name}")
    if echo "$op" | grep -q '"done": *true'; then
      engine_name="$(echo "$op" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("response",{}).get("name",""))')"
      break
    fi
    sleep 5
  done
  [[ -n "$engine_name" ]] || { echo "  engine creation did not finish"; return 1; }
  local engine_id="${engine_name##*/}"
  echo "  engine: $engine_name"
  echo ""
  echo "  *** Set ATTEST_MEMORY_ENGINE_ID=${engine_id} and re-run ./deploy.sh deploy ***"
  echo "  (or add it to your shell environment before deploying)"
}

cmd_smoke() {
  say "Publishing one test message"
  gcloud pubsub topics publish "$TOPIC" --project "$PROJECT" \
    --message '{"crd":"900001"}' --attribute "battery_version=4ea67a1f35e1"
  echo "Waiting 45s for delivery..."
  sleep 45
  say "Recent logs (look for evidence.append and a 200 on the trigger route)"
  gcloud logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE}" \
    --project "$PROJECT" --limit 40 --freshness 10m \
    --format 'value(timestamp, textPayload, httpRequest.status)'
  say "Traces: https://console.cloud.google.com/traces/list?project=${PROJECT}"
}

case "${1:-all}" in
  apis)   cmd_apis ;;
  infra)  cmd_infra ;;
  deploy) cmd_deploy ;;
  wire)   cmd_wire ;;
  memory) cmd_memory ;;
  smoke)  cmd_smoke ;;
  all)    cmd_apis; cmd_infra; cmd_memory; cmd_deploy; cmd_wire; cmd_smoke ;;
  *)      sed -n '2,14p' "$0"; exit 1 ;;
esac
