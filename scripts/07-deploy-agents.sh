#!/usr/bin/env bash
# Deploy the two agents and make sure BOTH end up in the Agent Registry
# `agents` collection.
#
# 1. Guideline agent -> Cloud Run, serving the A2A protocol via to_a2a().
#    A2A_HOST/PROTOCOL/PORT describe its PUBLIC address (baked into the agent
#    card); uvicorn still binds to $PORT inside the container. Once the
#    service answers, its live agent card is captured, validated, and
#    registered in Agent Registry as an A2A agent (--agent-spec-type=
#    a2a-agent-card). The registry indexes the card's url and skills; the
#    gateway later governs egress to that url with an agent-to-agent policy.
#
# 2. Clinical assistant -> Agent Runtime with identity_type=AGENT_IDENTITY
#    and the gateway binding. This is the step that mints the agent's SPIFFE
#    identity; there is no standalone "create agent identity" command. Agent
#    Runtime registers this agent in Agent Registry automatically, and the
#    deploy enables OpenTelemetry traces/metrics/logs with message content
#    capture OFF (see 11-observability.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

# Agent Registry keeps two names for every entry. `services create NAME` makes a
# `services/NAME` resource (what describe/delete use), and the registry derives
# the collection entry (mcpServers/, agents/, endpoints/) under a generated ID
# such as agentregistry-00000000-.... IAP egress policies and ADK registry
# discovery address that derived entry, so look it up by display name and
# insist on exactly one match.
registry_resource() {  # usage: registry_resource <mcp-servers|agents|endpoints> <display name>
  local collection="$1" display="$2" names count
  # Exact match in awk, not in --filter: gcloud's `=` operator is moving
  # toward pattern matching, which would let aiplatform-region also match
  # aiplatform-region-mtls.
  names="$(gcloud agent-registry "${collection}" list \
    --project="${PROJECT_ID}" --location="${REGION}" \
    --format="csv[no-heading,separator=|](displayName,name)" \
    | awk -F'|' -v want="${display}" '$1 == want { print $2 }')"
  count="$(grep -c . <<<"${names}" || true)"
  if [[ "${count}" != "1" ]]; then
    echo "ERROR: expected exactly one ${collection} entry with displayName '${display}', found ${count}." >&2
    echo "       Run scripts/05-register-registry.sh (and 07-deploy-agents.sh for agents) first." >&2
    return 1
  fi
  echo "${names}"
}
mkdir -p scripts/rendered

echo "== 1) Guideline agent (A2A on Cloud Run) =="
A2A_HOST="${GUIDELINE_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
gcloud run deploy "${GUIDELINE_SERVICE}" \
  --source agents/guideline_agent \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --allow-unauthenticated \
  --set-env-vars "A2A_HOST=${A2A_HOST},A2A_PROTOCOL=https,A2A_PORT=443,GOOGLE_GENAI_USE_ENTERPRISE=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},ENABLE_CLOUD_TRACE=true,OTEL_SERVICE_NAME=${GUIDELINE_SERVICE}"

echo
echo "== 1a) Capture and validate the live A2A agent card =="
echo "Agent card: ${GUIDELINE_AGENT_CARD_URL}"
curl -fsS "${GUIDELINE_AGENT_CARD_URL}" > "${GUIDELINE_AGENT_CARD_FILE}"
python3 -m json.tool "${GUIDELINE_AGENT_CARD_FILE}" | head -40
python3 clients/validate_agent_card.py \
  --card-file "${GUIDELINE_AGENT_CARD_FILE}" \
  --expected-url "${GUIDELINE_AGENT_URL}" \
  --expected-name guideline_agent

echo
echo "== 1b) Register the specialist in Agent Registry from its agent card =="
# --agent-spec-type=a2a-agent-card puts the service in the `agents`
# collection: the registry reads url + skills from the card, and 08-grant-egress-and-enforce.sh
# binds roles/iap.egressor to it with --agent (an agent-to-agent policy).
if gcloud agent-registry services describe "${GUIDELINE_REGISTRY_NAME}" \
     --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
  echo "exists: ${GUIDELINE_REGISTRY_NAME} (left unchanged)."
  echo "If the card changed (new url or skills), describe the entry, then delete and"
  echo "re-create it from ${GUIDELINE_AGENT_CARD_FILE} before running 08-grant-egress-and-enforce.sh."
else
  gcloud agent-registry services create "${GUIDELINE_REGISTRY_NAME}" \
    --project="${PROJECT_ID}" --location="${REGION}" \
    --display-name="${GUIDELINE_REGISTRY_DISPLAY_NAME}" \
    --agent-spec-type=a2a-agent-card \
    --agent-spec-content="${GUIDELINE_AGENT_CARD_FILE}"
fi

echo
echo "== 1c) A2A smoke test: card discovery + message/send (synthetic question) =="
python3 clients/test_a2a.py --agent-url "${GUIDELINE_AGENT_URL}"

echo
echo "== 2) Clinical assistant (Agent Runtime + Agent Identity + gateway) =="
# Agent Runtime resolves agent_gateway_config with the project's Vertex AI
# service agent, which needs networkservices.agentGateways.get. In a brand-new
# project that service agent may not exist yet (Google creates it lazily), and
# the deploy fails with 400 FAILED_PRECONDITION "Permission denied to get Agent
# Gateway". Create it explicitly and give it read-only access to gateways.
gcloud beta services identity create --service=aiplatform.googleapis.com \
  --project="${PROJECT_ID}" >/dev/null
VERTEX_SERVICE_AGENT="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"
if gcloud projects get-iam-policy "${PROJECT_ID}" --flatten="bindings[].members" \
     --filter="bindings.role=roles/networkservices.viewer" \
     --format="value(bindings.members)" | grep -qx "${VERTEX_SERVICE_AGENT}"; then
  echo "Vertex AI service agent can already read the gateway."
else
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="${VERTEX_SERVICE_AGENT}" \
    --role="roles/networkservices.viewer" --condition=None --quiet >/dev/null
  echo "Granted the Vertex AI service agent read access to gateways; waiting 60 s for IAM to propagate..."
  sleep 60
fi
# ADK discovers the records toolset by its derived mcpServers/ resource name.
export MCP_REGISTRY_RESOURCE="$(registry_resource mcp-servers "${MCP_REGISTRY_DISPLAY_NAME}")"
echo "Records MCP registry entry: ${MCP_REGISTRY_RESOURCE}"
python3 -u deploy/deploy_root_agent.py | tee scripts/rendered/root-agent-deploy.out

# Persist the outputs the later scripts need.
grep -E "^(AGENT_ENGINE_NAME|AGENT_IDENTITY)=" scripts/rendered/root-agent-deploy.out \
  | sed 's/^/export /' > scripts/rendered/agent.env
echo
echo "Saved to scripts/rendered/agent.env:"
cat scripts/rendered/agent.env
echo
echo "Load them with:  source scripts/rendered/agent.env"

echo
echo "== 3) Verify both agents are in the Agent Registry agents collection =="
# Agent Runtime registration is automatic but can lag the deploy by a moment.
for attempt in 1 2 3 4 5 6; do
  AGENTS_LIST="$(gcloud agent-registry agents list --project="${PROJECT_ID}" --location="${REGION}" \
    --format="value(displayName)" 2>/dev/null || true)"
  if grep -q "clinical-assistant" <<<"${AGENTS_LIST}" && grep -qF "${GUIDELINE_REGISTRY_DISPLAY_NAME}" <<<"${AGENTS_LIST}"; then
    break
  fi
  echo "waiting for registry to list both agents (attempt ${attempt}/6)..."
  sleep 10
done
gcloud agent-registry agents list --project="${PROJECT_ID}" --location="${REGION}" \
  --format="table(displayName, name)"
echo
echo "Expected: '${GUIDELINE_REGISTRY_DISPLAY_NAME}' (registered from its card; the registry uses the card's name) and"
echo "'clinical-assistant' (registered automatically by Agent Runtime)."
