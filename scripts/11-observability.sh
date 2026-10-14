#!/usr/bin/env bash
# Observability for the governed healthcare stack.
#
# The agents already emit telemetry (07-deploy-agents.sh enabled OpenTelemetry on the
# Agent Runtime deploy and Cloud Trace export on the A2A specialist). This
# script adds the pieces that live OUTSIDE the agents:
#
#   1. Cloud Trace on the Agent Gateway (Preview): a telemetry policy that
#      makes every intercepted MCP/A2A request a span under the agent's trace,
#      plus the roles/cloudtrace.agent grants the gateway service agents need.
#   2. Observability Analytics on the _Default log bucket, which the gateway's
#      built-in Observability dashboard reads from.
#   3. Two log-based metrics (egress denials, Model Armor blocks), an alert
#      policy on the first, and one custom Cloud Monitoring dashboard that
#      puts gateway, agent, MCP server, and audit signals on a single page.
#
# Idempotent: every step checks before it creates. Requires 00-env.sh and a
# deployed gateway (06-create-gateway.sh) and agents (07-deploy-agents.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p scripts/rendered

echo "== 1) Cloud Trace on the Agent Gateway =="
# The gateway writes spans through these service agents. The grant is a
# project-level IAM binding; a missing service agent (API never used yet)
# is reported, not fatal, so the rest of the module still completes.
for sa in \
  "service-${PROJECT_NUMBER}@compute-system.iam.gserviceaccount.com" \
  "service-${PROJECT_NUMBER}@gcp-sa-networksecurity.iam.gserviceaccount.com" \
  "service-${PROJECT_NUMBER}@gcp-sa-agentgateway.iam.gserviceaccount.com"; do
  if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
       --member="serviceAccount:${sa}" --role=roles/cloudtrace.agent \
       --condition=None --quiet >/dev/null 2>&1; then
    echo "granted roles/cloudtrace.agent to ${sa}"
  else
    echo "WARNING: could not grant roles/cloudtrace.agent to ${sa} (service agent may not exist yet); re-run after the first gateway request"
  fi
done

# The A2A specialist exports its own parent spans from Cloud Run, so its
# runtime service account also needs to write traces.
GUIDELINE_RUNTIME_SA="$(gcloud run services describe "${GUIDELINE_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)"
GUIDELINE_RUNTIME_SA="${GUIDELINE_RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${GUIDELINE_RUNTIME_SA}" --role=roles/cloudtrace.agent \
  --condition=None --quiet >/dev/null
echo "granted roles/cloudtrace.agent to ${GUIDELINE_RUNTIME_SA} (guideline agent runtime)"

envsubst < scripts/yaml/gateway-tracing-policy.yaml.tmpl \
  > scripts/rendered/gateway-tracing-policy.yaml
gcloud beta network-services telemetry-policies import "${GATEWAY_TRACING_POLICY}" \
  --source=scripts/rendered/gateway-tracing-policy.yaml \
  --location="${REGION}" \
  --project="${PROJECT_ID}"
gcloud beta network-services telemetry-policies describe "${GATEWAY_TRACING_POLICY}" \
  --location="${REGION}" --project="${PROJECT_ID}" \
  --format="yaml(displayName, tracingConfiguration)"

echo
echo "== 2) Observability Analytics on the _Default log bucket =="
# The gateway's console Observability dashboard queries the _Default bucket's
# _AllLogs view through Observability Analytics. The upgrade is one-way and
# free; it is a no-op if already enabled.
gcloud logging buckets update _Default --location=global --enable-analytics \
  --project="${PROJECT_ID}" >/dev/null 2>&1 \
  && echo "_Default bucket: analytics enabled" \
  || echo "_Default bucket: analytics already enabled or not changeable here (check Logging > Logs storage)"

echo
echo "== 3) Log-based metrics =="
metric_create() {
  local name="$1" description="$2" filter="$3"
  if gcloud logging metrics describe "${name}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "exists: ${name}"
  else
    gcloud logging metrics create "${name}" --project="${PROJECT_ID}" \
      --description="${description}" --log-filter="${filter}"
  fi
}
# Gateway denials: the authoritative record that an agent tried to reach a
# tool or destination it is not authorized for. Steady state is ~0.
metric_create "${EGRESS_DENIAL_METRIC}" \
  "Agent egress requests denied at the ${GATEWAY_NAME} gateway" \
  "resource.type=\"networkservices.googleapis.com/Gateway\" AND resource.labels.gateway_name=\"${GATEWAY_NAME}\" AND httpRequest.status=403 AND -httpRequest.requestMethod=\"CONNECT\""
# Model Armor blocks from both layers: the in-agent plugin's "Model Armor
# blocked" log line (user message, tool result, model response), and gateway
# rows where the Model Armor authorization policy returned DENIED
# (jsonPayload.authzPolicyInfo.policies[] carries one verdict per policy).
metric_create "${MODEL_ARMOR_BLOCK_METRIC}" \
  "Model Armor blocked a prompt, tool result, or model response (plugin or gateway)" \
  "\"Model Armor blocked\" OR (resource.type=\"networkservices.googleapis.com/Gateway\" AND resource.labels.gateway_name=\"${GATEWAY_NAME}\" AND jsonPayload.authzPolicyInfo.policies.name:\"authz-policy-model-armor\" AND jsonPayload.authzPolicyInfo.policies.result=\"DENIED\")"

echo
echo "== 4) Alert policy: egress denied at the gateway =="
envsubst < scripts/yaml/egress-denials-alert.json.tmpl \
  > scripts/rendered/egress-denials-alert.json
EXISTING_POLICY="$(gcloud alpha monitoring policies list --project="${PROJECT_ID}" \
  --filter="displayName=\"${EGRESS_DENIAL_ALERT_DISPLAY_NAME}\"" --format="value(name)" 2>/dev/null || true)"
if [ -n "${EXISTING_POLICY}" ]; then
  echo "exists: ${EXISTING_POLICY}"
else
  gcloud alpha monitoring policies create --project="${PROJECT_ID}" \
    --policy-from-file=scripts/rendered/egress-denials-alert.json
  echo "Add a notification channel to the policy in Monitoring > Alerting (none attached by default)."
fi

echo
echo "== 5) Custom dashboard: governance and audit on one page =="
envsubst < scripts/yaml/healthcare-dashboard.json.tmpl \
  > scripts/rendered/healthcare-dashboard.json
python3 -m json.tool scripts/rendered/healthcare-dashboard.json >/dev/null  # fail fast on bad JSON
EXISTING_DASHBOARD="$(gcloud monitoring dashboards list --project="${PROJECT_ID}" \
  --filter="displayName=\"${DASHBOARD_DISPLAY_NAME}\"" --format="value(name)" 2>/dev/null || true)"
if [ -n "${EXISTING_DASHBOARD}" ]; then
  echo "exists: ${EXISTING_DASHBOARD}"
else
  EXISTING_DASHBOARD="$(gcloud monitoring dashboards create --project="${PROJECT_ID}" \
    --config-from-file=scripts/rendered/healthcare-dashboard.json --format="value(name)")"
  echo "created: ${EXISTING_DASHBOARD}"
fi
DASHBOARD_ID="${EXISTING_DASHBOARD##*/}"

echo
echo "== Where to look =="
cat <<EOF
Agent traces (Session / Trace / Span views) and Observability tab:
  https://console.cloud.google.com/agent-platform/agent-registry/agents?project=${PROJECT_ID}
Agent Runtime deployment Playground (Trace, Event, State, Sessions panels):
  https://console.cloud.google.com/agent-platform/runtimes?project=${PROJECT_ID}
Agent Gateway Observability dashboard (authorization failures, unregistered blocks, IAP mode):
  https://console.cloud.google.com/agent-platform/gateways?project=${PROJECT_ID}
Custom dashboard (this module):
  https://console.cloud.google.com/monitoring/dashboards/builder/${DASHBOARD_ID}?project=${PROJECT_ID}
Trace explorer (gateway spans, filter: +root:invocation or status 403):
  https://console.cloud.google.com/traces/explorer?project=${PROJECT_ID}
Policies (IAP egress bindings per agent / MCP server / endpoint):
  https://console.cloud.google.com/agent-platform/policies/iam?project=${PROJECT_ID}

Three audit queries (also in the codelab's observability step):
  # 1. Gateway decisions per MCP call
  gcloud logging read 'resource.type="networkservices.googleapis.com/Gateway" AND resource.labels.gateway_name="${GATEWAY_NAME}"' --project=${PROJECT_ID} --limit=10 --freshness=1d \\
    --format='table(timestamp, httpRequest.status, jsonPayload.agentGatewayInfo.mcpInfo.method, jsonPayload.agentGatewayInfo.mcpInfo.parameter, jsonPayload.agentGatewayInfo.agentRegistryResource, trace)'
  # 2. IAP's own verdicts (who, what resource, granted?)
  gcloud logging read 'protoPayload.serviceName="iap.googleapis.com" AND protoPayload.authorizationInfo.permission="iap.resources.egressViaIAP"' --project=${PROJECT_ID} --limit=10 --freshness=1d \\
    --format='table(timestamp, protoPayload.authenticationInfo.principalSubject, protoPayload.authorizationInfo[0].resource, protoPayload.authorizationInfo[0].granted, labels."iap.googleapis.com/audited_resource_name")'
  # 3. Which clinician the MCP server authenticated
  gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="${MCP_SERVICE}" AND textPayload:"Authenticated clinician"' --project=${PROJECT_ID} --limit=10 --freshness=1d --format='table(timestamp, textPayload)'
EOF
