#!/usr/bin/env bash
# Deploy the clipping agent as a Cloud Run Job that a Cloud Scheduler trigger runs every few minutes.
# Run it in Google Cloud Shell (it has gcloud and git), from the repo root:
#
#   PROJECT_ID=my-clipper-project ./deploy/cloudrun.sh
#
# Safe to re-run: it rebuilds the image and updates the job. Keys are asked for once and kept in
# Secret Manager; re-running offers to keep them.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID to a project with billing enabled (not your Gemini project)}"
REGION="${REGION:-us-central1}"          # us-central1/us-east1/us-west1 keep the bucket in the free tier
EVERY_MINUTES="${EVERY_MINUTES:-10}"     # how often to check the marketplace
CPU="${CPU:-2}"
MEMORY="${MEMORY:-4Gi}"
BUCKET="${BUCKET:-${PROJECT_ID}-clipper-data}"
JOB=clipper
SA_NAME=clipper-runner
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/clipper/clipper:latest"

gcloud config set project "$PROJECT_ID" >/dev/null
echo "== Enabling APIs (one-time, free)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com storage.googleapis.com iam.googleapis.com

echo "== Image registry and build"
gcloud artifacts repositories describe clipper --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create clipper --repository-format docker --location "$REGION"
gcloud builds submit --tag "$IMAGE" .

echo "== Storage bucket for job state and clips"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access

echo "== Service account"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name "Clipping agent runner"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$SA" --role roles/storage.objectAdmin >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA" \
  --role roles/secretmanager.secretAccessor --condition None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA" \
  --role roles/run.invoker --condition None >/dev/null

echo "== Secrets"
put_secret() {  # name, prompt
  local name="$1" prompt="$2" value
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    read -r -p "$name already stored. Replace it? [y/N] " yn
    [[ "$yn" =~ ^[Yy] ]] || return 0
  fi
  read -r -s -p "$prompt: " value; echo
  [[ -n "$value" ]] || { echo "  skipped (empty)"; return 0; }
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
  else
    printf '%s' "$value" | gcloud secrets create "$name" --replication-policy automatic --data-file=- >/dev/null
  fi
}
put_secret market-agent-token "MARKET_AGENT_TOKEN (aat_...)"
put_secret groq-api-key "GROQ_API_KEY (gsk_...)"
put_secret gemini-api-key "GEMINI_API_KEY (AIza..., Enter to skip)"
SECRETS="MARKET_AGENT_TOKEN=market-agent-token:latest,GROQ_API_KEY=groq-api-key:latest"
gcloud secrets describe gemini-api-key >/dev/null 2>&1 && SECRETS="$SECRETS,GEMINI_API_KEY=gemini-api-key:latest"
ENV_VARS="WORK_DIR=/data,KEEP_DAYS=10"
if [[ -n "${COOKIES_FILE:-}" ]]; then  # optional: YouTube cookies.txt for the bot check
  gcloud secrets describe ytdlp-cookies >/dev/null 2>&1 \
    && gcloud secrets versions add ytdlp-cookies --data-file="$COOKIES_FILE" >/dev/null \
    || gcloud secrets create ytdlp-cookies --replication-policy automatic --data-file="$COOKIES_FILE" >/dev/null
  SECRETS="$SECRETS,/secrets/cookies.txt=ytdlp-cookies:latest"
  ENV_VARS="$ENV_VARS,YTDLP_COOKIES=/secrets/cookies.txt"
fi

echo "== Cloud Run Job"
JOB_FLAGS=(--image "$IMAGE" --region "$REGION" --service-account "$SA"
  --command clipper --args worker,--once
  --cpu "$CPU" --memory "$MEMORY" --tasks 1 --max-retries 0 --task-timeout 3h
  --set-env-vars "$ENV_VARS" --set-secrets "$SECRETS"
  --add-volume "name=data,type=cloud-storage,bucket=$BUCKET"
  --add-volume-mount "volume=data,mount-path=/data")
if gcloud run jobs describe "$JOB" --region "$REGION" >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" "${JOB_FLAGS[@]}"
else
  gcloud run jobs create "$JOB" "${JOB_FLAGS[@]}"
fi

echo "== Schedule: every $EVERY_MINUTES minutes"
URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
SCHED_FLAGS=(--location "$REGION" --schedule "*/${EVERY_MINUTES} * * * *" --uri "$URI" --http-method POST
  --oauth-service-account-email "$SA")
if gcloud scheduler jobs describe clipper-tick --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http clipper-tick "${SCHED_FLAGS[@]}"
else
  gcloud scheduler jobs create http clipper-tick "${SCHED_FLAGS[@]}"
fi

echo
echo "Done. First run now:  gcloud run jobs execute $JOB --region $REGION --wait"
echo "Logs:                 gcloud logging read 'resource.type=cloud_run_job' --limit 50 --format='value(textPayload)'"
echo "Pause:                gcloud scheduler jobs pause clipper-tick --location $REGION"
