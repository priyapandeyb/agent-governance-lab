#!/usr/bin/env bash
# Required for the full workshop, Preview: attach Model Armor at the gateway as a CONTENT_AUTHZ
# extension, screening agent->tool requests and tool->agent responses at the
# network layer (defense in depth under the ADK plugin).
#
# IAM: the Service Extensions service agent performs the Model Armor callout
# and needs calloutUser + serviceUsageConsumer, plus modelarmor.user on the
# template's project.
# Caveat: with gateway-attached Model Armor, ADK agents only support
# streaming sanitization via the streamQuery method.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p scripts/rendered

# The Model Armor integration guide names the project's Service Extensions
# service agent; the gateway also reports the account it calls extensions with
# in agentGatewayCard.serviceExtensionsServiceAccount (a Google-managed tenant
# account). Grant the same three roles to both so the callout cannot 403.
SE_SERVICE_AGENTS="service-${PROJECT_NUMBER}@gcp-sa-dep.iam.gserviceaccount.com"
CARD_SA="$(gcloud network-services agent-gateways describe "${GATEWAY_NAME}" \
  --location="${REGION}" --project="${PROJECT_ID}" \
  --format='value(agentGatewayCard.serviceExtensionsServiceAccount)')"
if [[ -n "${CARD_SA}" && "${CARD_SA}" != "${SE_SERVICE_AGENTS}" ]]; then
  SE_SERVICE_AGENTS="${SE_SERVICE_AGENTS} ${CARD_SA}"
fi
for sa in ${SE_SERVICE_AGENTS}; do
  echo "Granting Model Armor callout roles to ${sa}"
  for role in roles/modelarmor.calloutUser roles/serviceusage.serviceUsageConsumer roles/modelarmor.user; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${sa}" \
      --role="${role}" \
      --condition=None >/dev/null
  done
done

for tmpl in model-armor-extension model-armor-policy; do
  envsubst < "scripts/yaml/${tmpl}.yaml.tmpl" > "scripts/rendered/${tmpl}.yaml"
done

gcloud beta service-extensions authz-extensions import "${GATEWAY_NAME}-authz-model-armor" \
  --source="scripts/rendered/model-armor-extension.yaml" \
  --location="${REGION}" \
  --project="${PROJECT_ID}"

gcloud network-security authz-policies import "${GATEWAY_NAME}-authz-policy-model-armor" \
  --source="scripts/rendered/model-armor-policy.yaml" \
  --location="${REGION}" \
  --project="${PROJECT_ID}"

echo "Model Armor CONTENT_AUTHZ policy attached to ${GATEWAY_NAME}."
