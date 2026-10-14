#!/usr/bin/env bash
# Tear everything down. Order matters: agent first (it holds the gateway
# binding), then policies/extensions, then the gateway, registry entries,
# Cloud Run services, and the Model Armor template.
set -uo pipefail
cleanup_errors=0
cd "$(dirname "$0")/.."

if [ -f scripts/rendered/agent.env ]; then
  source scripts/rendered/agent.env
  echo "== Delete the Agent Runtime agent =="
  curl -fsS -X DELETE \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    "https://${REGION}-aiplatform.googleapis.com/v1beta1/${AGENT_ENGINE_NAME}?force=true" || cleanup_errors=$((cleanup_errors + 1))
fi

echo "== Delete observability resources (11-observability.sh) =="
gcloud beta network-services telemetry-policies delete "${GATEWAY_TRACING_POLICY}" \
  --location="${REGION}" --project="${PROJECT_ID}" --quiet || cleanup_errors=$((cleanup_errors + 1))
for policy in $(gcloud alpha monitoring policies list --project="${PROJECT_ID}" \
    --filter="displayName=\"${EGRESS_DENIAL_ALERT_DISPLAY_NAME}\"" --format="value(name)" 2>/dev/null); do
  gcloud alpha monitoring policies delete "$policy" --project="${PROJECT_ID}" --quiet \
    || cleanup_errors=$((cleanup_errors + 1))
done
for dashboard in $(gcloud monitoring dashboards list --project="${PROJECT_ID}" \
    --filter="displayName=\"${DASHBOARD_DISPLAY_NAME}\"" --format="value(name)" 2>/dev/null); do
  gcloud monitoring dashboards delete "$dashboard" --project="${PROJECT_ID}" --quiet \
    || cleanup_errors=$((cleanup_errors + 1))
done
for metric in "${EGRESS_DENIAL_METRIC}" "${MODEL_ARMOR_BLOCK_METRIC}"; do
  gcloud logging metrics delete "$metric" --project="${PROJECT_ID}" --quiet \
    || cleanup_errors=$((cleanup_errors + 1))
done
# The _Default log bucket's Observability Analytics upgrade is one-way and
# free; it is intentionally left in place.

echo "== Delete authz policies and extensions =="
for p in "${GATEWAY_NAME}-authz-policy-iap" "${GATEWAY_NAME}-authz-policy-model-armor"; do
  gcloud network-security authz-policies delete "$p" \
    --location="${REGION}" --project="${PROJECT_ID}" --quiet || cleanup_errors=$((cleanup_errors + 1))
done
for e in "${GATEWAY_NAME}-authz-iap" "${GATEWAY_NAME}-authz-model-armor"; do
  gcloud beta service-extensions authz-extensions delete "$e" \
    --location="${REGION}" --project="${PROJECT_ID}" --quiet || cleanup_errors=$((cleanup_errors + 1))
done

echo "== Delete the gateway =="
gcloud network-services agent-gateways delete "${GATEWAY_NAME}" \
  --location="${REGION}" --project="${PROJECT_ID}" --quiet || cleanup_errors=$((cleanup_errors + 1))

echo "== Delete registry entries =="
# The clinical assistant's own registry entry is removed with the engine
# (Agent Runtime auto-registration). The guideline agent entry was created
# from its agent card by 07-deploy-agents.sh; the platform endpoints come from 00-env.sh.
PLATFORM_ENDPOINT_NAMES=""
for pair in ${PLATFORM_ENDPOINTS}; do PLATFORM_ENDPOINT_NAMES="${PLATFORM_ENDPOINT_NAMES} ${pair%%=*}"; done
for s in "${MCP_REGISTRY_NAME}" "${GUIDELINE_REGISTRY_NAME}" ${PLATFORM_ENDPOINT_NAMES}; do
  gcloud agent-registry services delete "$s" \
    --location="${REGION}" --project="${PROJECT_ID}" --quiet || cleanup_errors=$((cleanup_errors + 1))
done

echo "== Delete Cloud Run services =="
for svc in "${IDP_SERVICE}" "${MCP_SERVICE}" "${GUIDELINE_SERVICE}"; do
  gcloud run services delete "$svc" \
    --region="${REGION}" --project="${PROJECT_ID}" --quiet || cleanup_errors=$((cleanup_errors + 1))
done

echo "== Delete Model Armor and SDP templates =="
for template in "${MODEL_ARMOR_TEMPLATE_ID}" "${MODEL_ARMOR_PHI_TEMPLATE_ID}"; do
  gcloud model-armor templates delete "$template" \
    --location="${REGION}" --project="${PROJECT_ID}" --quiet || cleanup_errors=$((cleanup_errors + 1))
done
curl -fsS -X DELETE \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://dlp.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/inspectTemplates/${SDP_INSPECT_TEMPLATE_ID}" \
  || cleanup_errors=$((cleanup_errors + 1))

echo "== Delete the staging bucket =="
gcloud storage rm -r "${STAGING_BUCKET}" || cleanup_errors=$((cleanup_errors + 1))

if [ "$cleanup_errors" -gt 0 ]; then
  echo "Cleanup had $cleanup_errors failed operations (including resources already absent)."
  echo "Verify remaining resources (codelab clean-up step) before considering cleanup complete."
  exit 1
fi
echo "Deletion requests completed. Verify asynchronous engine deletion and remaining artifacts (codelab clean-up step)."
