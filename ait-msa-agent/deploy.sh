#!/usr/bin/env bash
# Despliega el backend en Cloud Run con el CLI de gcloud.
#
# Uso:  PROJECT_ID=mi-proyecto ./deploy.sh
# Requisitos previos (una sola vez):
#   gcloud auth login
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
#   printf '%s' "$GROQ_API_KEY" | gcloud secrets create groq-api-key --data-file=-
#   gcloud secrets add-iam-policy-binding groq-api-key \
#     --member="serviceAccount:$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
#     --role=roles/secretmanager.secretAccessor
set -euo pipefail

: "${PROJECT_ID:?Define PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-ait-msa-agent}"
REPO="${REPO:-ait-msa}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:$(date +%Y%m%d-%H%M%S)"
CORS_ORIGIN_REGEX="${CORS_ORIGIN_REGEX:-^https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?\$|^https://.*\\.run\\.app\$}"

gcloud config set project "$PROJECT_ID"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"

# Construye la imagen con Cloud Build (no requiere Docker local)
gcloud builds submit --tag "$IMAGE" .

# timeout > timeout global del agente (45 s); 1 CPU por solicitud porque el agente es síncrono.
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 --memory 1Gi \
  --concurrency 10 --max-instances 3 \
  --timeout 120 \
  --set-secrets "GROQ_API_KEY=groq-api-key:latest" \
  --set-env-vars "^@^CORS_ORIGIN_REGEX=${CORS_ORIGIN_REGEX}@LANGSMITH_TRACING=false"

gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)'
