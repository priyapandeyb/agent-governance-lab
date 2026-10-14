#!/usr/bin/env bash
# Enable every API this codelab touches. The Agent Gateway setup docs list a
# long set; enabling them all up front avoids failures halfway through.
# Service Usage accepts at most 20 services per `gcloud services enable` call
# (SU_MAX_BATCH_SIZE_EXCEEDED above that), so the 25 are enabled in two batches.
# Service Extensions (authz extensions, callouts) is served by networkservices.googleapis.com;
# there is no separate service-extensions API.
set -euo pipefail

# Batch 1: compute, source builds, identity, Agent Runtime, Agent Registry.
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  compute.googleapis.com \
  dns.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  agentregistry.googleapis.com \
  agentidentity.googleapis.com \
  apphub.googleapis.com \
  apptopology.googleapis.com \
  cloudapiregistry.googleapis.com \
  --project="${PROJECT_ID}"

# Batch 2: Agent Gateway + IAP, Model Armor, observability.
gcloud services enable \
  iap.googleapis.com \
  networkservices.googleapis.com \
  networksecurity.googleapis.com \
  modelarmor.googleapis.com \
  dlp.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  cloudtrace.googleapis.com \
  telemetry.googleapis.com \
  observability.googleapis.com \
  --project="${PROJECT_ID}"

gcloud storage ls "${STAGING_BUCKET}" >/dev/null 2>&1 || gcloud storage buckets create "${STAGING_BUCKET}" --location="${REGION}" --project="${PROJECT_ID}"
echo "APIs enabled and staging bucket ready."
