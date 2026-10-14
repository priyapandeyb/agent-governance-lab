#!/usr/bin/env bash
# Authorize the deployed agent's identity for specific egress targets, then
# flip the IAP extension from DRY_RUN to enforced.
#
# The grants are deliberately narrow, and the MCP grant carries a CEL
# condition on the MCP tool's readOnlyHint annotation (from toolspec.json):
# the agent may call read-only tools, but add_progress_note (a write) is
# denied at the gateway with 403 "Egress request is not authorized." That
# denial is one of the codelab's key demos.
#
# Requires: source scripts/rendered/agent.env  (from 07-deploy-agents.sh)
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

source scripts/rendered/agent.env
# effectiveIdentity comes back as a bare trust-domain path
# (agents.global.org-ORG_ID.system.id.goog/resources/... in an organization,
# agents.global.proj-PROJECT_NUMBER.system.id.goog/resources/... without one),
# sometimes with a spiffe:// scheme. IAM members need the principal:// form.
AGENT_PRINCIPAL="principal://${AGENT_IDENTITY#*://}"
echo "Agent principal: ${AGENT_PRINCIPAL}"
if [[ -n "${AGENT_TRUST_DOMAIN:-}" && "${AGENT_PRINCIPAL}" != "principal://${AGENT_TRUST_DOMAIN}/"* ]]; then
  echo "WARNING: expected trust domain ${AGENT_TRUST_DOMAIN}; is AGENT_IDENTITY from this project?" >&2
fi

echo "== Cloud Run front door: only this agent's identity may invoke the private MCP service =="
# The agent presents an ID token for its own Agent Identity; Cloud Run accepts
# it because this binding names that identity. No service account is involved.
gcloud run services add-iam-policy-binding "${MCP_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --member="${AGENT_PRINCIPAL}" --role=roles/run.invoker

echo "== Destination IAM: the Google APIs the agent itself calls =="
# roles/iap.egressor (below) only lets a request LEAVE through the gateway.
# Each Google API then checks its own IAM against the agent's principal, and
# an Agent Identity starts with no project roles at all. Without these, the
# gateway logs ALLOWED and the API still answers 403 (for example the Model
# Armor plugin: 'modelarmor.templates.useToSanitizeUserPrompt' denied).
#   aiplatform.user            Gemini calls and Agent Runtime sessions
#   modelarmor.user            the in-agent Model Armor plugin's sanitize calls
#   agentregistry.viewer       discovering the MCP toolset from the registry
#   serviceusage.serviceUsageConsumer  using this project as the quota project
#   cloudtrace.agent, logging.logWriter, monitoring.metricWriter,
#   telemetry.tracesWriter     exporting traces, logs, and metrics (Task 12)
for role in roles/aiplatform.user roles/modelarmor.user roles/agentregistry.viewer \
            roles/serviceusage.serviceUsageConsumer roles/cloudtrace.agent \
            roles/logging.logWriter roles/monitoring.metricWriter roles/telemetry.tracesWriter; do
  if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
       --member="${AGENT_PRINCIPAL}" --role="${role}" \
       --condition=None --quiet --format=none 2>/tmp/agl-grant.err; then
    echo "granted ${role}"
  else
    echo "WARNING: could not grant ${role}: $(tail -1 /tmp/agl-grant.err)" >&2
  fi
done

echo "== Egress to the Patient Records MCP server: READ-ONLY tools only =="
MCP_SERVER_ID="$(registry_resource mcp-servers "${MCP_REGISTRY_DISPLAY_NAME}")"
MCP_SERVER_ID="${MCP_SERVER_ID##*/}"
echo "mcpServers entry: ${MCP_SERVER_ID}"
# The CEL expression contains a comma, so it must go through a condition
# file: gcloud's inline --condition parser splits on commas.
mkdir -p scripts/rendered
# Three clauses: (1) the tool is annotated read-only AND (2) its name is on the
# explicit allowlist, OR (3) the request carries no tool name at all. Clause 3
# admits the MCP lifecycle methods (initialize, tools/list) that the toolset
# needs before any tool can be called; a tools/call always carries a name, so
# it can never admit a write.
cat > scripts/rendered/readonly-condition.yaml <<'EOF'
expression: >-
  (api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true &&
  api.getAttribute('iap.googleapis.com/mcp.toolName', '') in
  ['whoami', 'list_my_patients', 'get_patient_summary', 'get_recent_labs', 'get_clinical_notes'])
  || api.getAttribute('iap.googleapis.com/mcp.toolName', '') == ''
title: read-only-tools
description: Allow the five read-only MCP tools, plus tool-less MCP lifecycle methods
EOF
gcloud iap web add-iam-policy-binding \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --resource-type=agent-registry \
  --mcp-server="${MCP_SERVER_ID}" \
  --member="${AGENT_PRINCIPAL}" \
  --role=roles/iap.egressor \
  --condition-from-file=scripts/rendered/readonly-condition.yaml

echo "== Egress to the guideline agent: agent-to-agent policy (A2A) =="
# The specialist is registered in the `agents` collection from its A2A agent
# card (07-deploy-agents.sh), so the binding uses --agent, not --endpoint. This is the
# documented agent-to-agent egress policy; the gateway matches the hostname
# from the card's url. No per-tool condition: A2A is governed per destination.
GUIDELINE_AGENT_ID="$(registry_resource agents "${GUIDELINE_REGISTRY_DISPLAY_NAME}")"
GUIDELINE_AGENT_ID="${GUIDELINE_AGENT_ID##*/}"
echo "agents entry: ${GUIDELINE_AGENT_ID}"
gcloud iap web add-iam-policy-binding \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --resource-type=agent-registry \
  --agent="${GUIDELINE_AGENT_ID}" \
  --member="${AGENT_PRINCIPAL}" \
  --role=roles/iap.egressor

echo "== Egress to platform endpoints =="
# Same list scripts/05-register-registry.sh registered (from 00-env.sh), so a
# registered-but-ungranted endpoint cannot slip through and 403 the agent.
for pair in ${PLATFORM_ENDPOINTS}; do
  endpoint_id="$(registry_resource endpoints "${pair%%=*}")"   # display name = entry name
  endpoint_id="${endpoint_id##*/}"
  echo "endpoints entry for ${pair%%=*}: ${endpoint_id}"
  gcloud iap web add-iam-policy-binding \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --resource-type=agent-registry \
    --endpoint="${endpoint_id}" \
    --member="${AGENT_PRINCIPAL}" \
    --role=roles/iap.egressor
done

echo "== Flip the IAP extension from DRY_RUN to ENFORCED =="
envsubst < scripts/yaml/iap-authz-extension-enforce.yaml.tmpl \
  > scripts/rendered/iap-authz-extension-enforce.yaml
gcloud beta service-extensions authz-extensions import "${GATEWAY_NAME}-authz-iap" \
  --source="scripts/rendered/iap-authz-extension-enforce.yaml" \
  --location="${REGION}" \
  --project="${PROJECT_ID}"

echo
echo "Enforcement active. Reads are allowed; add_progress_note and any"
echo "unregistered destination are now blocked at the gateway."
echo
echo "Review the bindings in the console (Agent Platform > Policies):"
echo "  https://console.cloud.google.com/agent-platform/policies/iam?project=${PROJECT_ID}"
