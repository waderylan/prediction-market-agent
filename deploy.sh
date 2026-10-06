#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  echo 'Missing local .env file.' >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

project_id="${1:-${GCP_PROJECT_ID:-}}"
if [[ -z "$project_id" || -z "${GEMINI_API_KEY:-}" ]]; then
  echo 'Set GCP_PROJECT_ID and GEMINI_API_KEY in .env, or pass the project ID as argument 1.' >&2
  exit 1
fi
if ! command -v gcloud >/dev/null 2>&1; then
  echo 'Install and authenticate the Google Cloud CLI before deploying.' >&2
  exit 1
fi

region=us-west1
service=csci599-a1
repository=sportswatch
image="${region}-docker.pkg.dev/${project_id}/${repository}/sportswatch-mcp"
runtime_vars="GEMINI_API_KEY=${GEMINI_API_KEY},GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.8-flash}"
if [[ -n "${TAVILY_API_KEY:-}" ]]; then
  runtime_vars+=",TAVILY_API_KEY=${TAVILY_API_KEY}"
fi

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "$project_id"
existing_repository="$(gcloud artifacts repositories list \
  --location "$region" --project "$project_id" --format 'value(name)' |
  grep -Fx "$repository" || true)"
if [[ -z "$existing_repository" ]]; then
  gcloud artifacts repositories create "$repository" --repository-format docker \
    --location "$region" --project "$project_id"
fi
gcloud builds submit . --tag "$image" --project "$project_id"
gcloud run deploy "$service" \
  --image "$image" \
  --project "$project_id" \
  --platform managed \
  --region "$region" \
  --allow-unauthenticated \
  --memory 512Mi \
  --min-instances 0 \
  --max-instances 1 \
  --set-env-vars "$runtime_vars"
service_url="$(gcloud run services describe "$service" --project "$project_id" \
  --region "$region" --format 'value(status.url)')"
[[ -n "$service_url" ]] || { echo 'Could not read Cloud Run URL.' >&2; exit 1; }
printf '%s\n' "$service_url" > .cloud-run-url
