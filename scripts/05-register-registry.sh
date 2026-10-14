#!/usr/bin/env bash
# Register the destinations the agents are allowed to reach in Agent Registry.
# The registry serves two roles at once: a discovery catalog (the deployed
# agent finds the MCP toolset through it) and the Agent Gateway's allowlist
# (the gateway is default-deny; unregistered destinations get HTTP 403).
#
# Agent Registry has three resource collections, chosen by the spec flag you
# pass to `gcloud agent-registry services create`:
#   --mcp-server-spec-type ...  -> mcpServers  (tool catalog + per-tool policy)
#   --agent-spec-type ...       -> agents      (A2A agent cards, Agent Runtime)
#   --endpoint-spec-type ...    -> endpoints   (plain HTTP destinations)
#
# This script registers:
#   1. The Patient Records MCP server, with its toolspec (tool annotations
#      like readOnlyHint feed the gateway's per-tool CEL policies).
#   2. Platform endpoints the agent itself needs at startup and at runtime
#      (registry, Vertex AI, telemetry, logging, monitoring, trace, Model
#      Armor). Skipping these is a documented failure
#      mode: the agent 403s during its own startup.
#
# It does NOT register the two agents. Agent Runtime registers the clinical
# assistant automatically when it is deployed, and the guideline specialist
# is registered from its live A2A agent card right after its Cloud Run
# deploy (scripts/07-deploy-agents.sh), because the card only exists once
# the service is up.
#
# Note: depending on rollout, the agent-registry surface may require the
# alpha track (gcloud alpha agent-registry ...). If a command below reports
# an unknown command group, prepend "alpha".
set -euo pipefail
cd "$(dirname "$0")/.."

# Create-or-skip: re-running setup must not fail on ALREADY_EXISTS and must
# never delete a shared entry. Pass the service ID, then the create flags.
registry_create() {
  local name="$1"; shift
  if gcloud agent-registry services describe "${name}" \
       --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
    echo "exists: ${name} (left unchanged; describe it before updating)"
    return 0
  fi
  gcloud agent-registry services create "${name}" \
    --project="${PROJECT_ID}" --location="${REGION}" "$@"
}

echo "== 1) Patient Records MCP server (mcpServers collection, with toolspec) =="
registry_create "${MCP_REGISTRY_NAME}" \
  --display-name="${MCP_REGISTRY_DISPLAY_NAME}" \
  --mcp-server-spec-type=tool-spec \
  --mcp-server-spec-content=mcp-server/toolspec.json \
  --interfaces=url="${MCP_SERVER_URL}",protocolBinding=jsonrpc

echo
echo "== 2) Platform endpoints (endpoints collection; required for agent startup) =="
# PLATFORM_ENDPOINTS comes from scripts/00-env.sh: space-separated name=url.
for pair in ${PLATFORM_ENDPOINTS}; do
  name="${pair%%=*}"
  url="${pair#*=}"
  registry_create "${name}" \
    --display-name="${name}" \
    --endpoint-spec-type=no-spec \
    --interfaces=url="${url}",protocolBinding=http-json
done

echo
echo "== 3) Agents =="
echo "Not registered here. 07-deploy-agents.sh deploys both agents and registers them:"
echo "  - ${GUIDELINE_SERVICE}: from its A2A agent card (--agent-spec-type=a2a-agent-card)"
echo "  - clinical-assistant:   automatically, by Agent Runtime"

echo
echo "Registered MCP servers:"
gcloud agent-registry mcp-servers list --project="${PROJECT_ID}" --location="${REGION}" \
  --format="table(displayName, name)"
echo
echo "Registered endpoints:"
gcloud agent-registry endpoints list --project="${PROJECT_ID}" --location="${REGION}" \
  --format="table(displayName, name)"
echo
echo "Registered agents (until 07-deploy-agents.sh: none of ours; Google's built-in Workspace Agent may already be listed):"
gcloud agent-registry agents list --project="${PROJECT_ID}" --location="${REGION}" \
  --format="table(displayName, name)"
