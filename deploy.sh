#!/usr/bin/env bash
# Despliega la imagen única (frontend + API) en Cloud Run.
#
# Uso: ./deploy.sh                       (proyecto armario-digital-299c1 por defecto)
#      PROJECT_ID=otro REGION=us-east1 ./deploy.sh
#
# La primera vez crea el secreto groq-api-key con el GROQ_API_KEY de ait-msa-agent/.env.
# Para rotar la clave: gcloud secrets versions add groq-api-key --data-file=<archivo>
set -euo pipefail
cd "$(dirname "$0")"

PROJECT_ID="${PROJECT_ID:-armario-digital-299c1}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-ait-msa-agent}"
REPO="${REPO:-ait-msa}"
SECRET="${SECRET:-groq-api-key}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:$(date +%Y%m%d-%H%M%S)"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com

# Secreto con la API key de Groq (se lee del .env sin imprimirla)
if ! gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  grep -E '^GROQ_API_KEY=' ait-msa-agent/.env | head -1 | cut -d= -f2- | tr -d '\r\n' \
    | gcloud secrets create "$SECRET" --replication-policy=automatic --data-file=-
fi
RUN_SA="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:${RUN_SA}" \
  --role=roles/secretmanager.secretAccessor >/dev/null

gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"

# Construye con Cloud Build (no requiere Docker local)
gcloud builds submit --tag "$IMAGE" .

# timeout > timeout global del agente (45 s). max-instances acota el gasto de la API key.
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 --memory 1Gi \
  --concurrency 10 --max-instances 2 \
  --timeout 120 \
  --set-secrets "GROQ_API_KEY=${SECRET}:latest" \
  --set-env-vars "LANGSMITH_TRACING=false"

gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)'
