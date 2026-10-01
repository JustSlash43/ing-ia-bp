#!/usr/bin/env bash
# Despliega SOLO el frontend en Cloud Run, apuntando a un backend externo.
#
# Uso: API_URL=https://xxxx.ngrok-free.app ./deploy.sh
#      PROJECT_ID=otro REGION=us-east1 API_URL=... ./deploy.sh
#
# El backend debe permitir el origen del frontend en CORS_ORIGIN_REGEX
# (por defecto acepta ^https://ait-msa-frontend-.*\.run\.app$ si lo configuras en su .env).
set -euo pipefail
cd "$(dirname "$0")"

: "${API_URL:?Define API_URL (URL pública del backend)}"
PROJECT_ID="${PROJECT_ID:-armario-digital-299c1}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-ait-msa-frontend}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# --source construye el Dockerfile de esta carpeta con Cloud Build (no requiere Docker local)
gcloud run deploy "$SERVICE" --source . --region "$REGION" --allow-unauthenticated --quiet \
  --port 8080 --cpu 1 --memory 256Mi --max-instances 2 --set-env-vars "API_URL=${API_URL}"

gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)'
