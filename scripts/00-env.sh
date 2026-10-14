# Source this file in every shell before running the other scripts:
#   export PROJECT_ID=your-project-id
#   source scripts/00-env.sh
#
# Requirements baked into these defaults:
# - The project may or may not belong to a Google Cloud organization. Agent
#   Identity anchors the agent's principal to a trust domain:
#     in an organization:      agents.global.org-ORG_ID.system.id.goog
#     without an organization: agents.global.proj-PROJECT_NUMBER.system.id.goog
#   Every script takes the principal from the deployed engine's
#   effectiveIdentity, so both forms work unchanged.
# - us-central1 satisfies every regional constraint in this stack (Agent
#   Registry, Agent Gateway, Model Armor sanitize endpoints, Agent Runtime).

export PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID before sourcing this file}"
export PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
# Empty for a project without an organization (personal / trial accounts).
export ORG_ID=$(gcloud projects get-ancestors "${PROJECT_ID}" --format="csv[no-heading](id,type)" | awk -F, '$2=="organization"{print $1}')
if [[ -n "${ORG_ID}" ]]; then
  export AGENT_TRUST_DOMAIN="agents.global.org-${ORG_ID}.system.id.goog"
else
  export AGENT_TRUST_DOMAIN="agents.global.proj-${PROJECT_NUMBER}.system.id.goog"
fi
export REGION="${REGION:-us-central1}"

# Clinician personas. A = you (runs the demo). B = a colleague whose panel
# you should NOT be able to read. Any two distinct emails work; the mock IdP
# mints tokens for whatever email it is asked for.
export CLINICIAN_A_EMAIL="${CLINICIAN_A_EMAIL:-$(gcloud config get-value account 2>/dev/null)}"
export CLINICIAN_B_EMAIL="${CLINICIAN_B_EMAIL:-dr.b@hospital-demo.example}"

# Service and resource names.
export IDP_SERVICE="hospital-idp"
export MCP_SERVICE="patient-records-mcp"
export GUIDELINE_SERVICE="guideline-agent"
export GATEWAY_NAME="healthcare-agents-gateway"
export MODEL_ARMOR_TEMPLATE_ID="healthcare-agents-armor"
export MODEL_ARMOR_PHI_TEMPLATE_ID="healthcare-agents-phi"
export SDP_INSPECT_TEMPLATE_ID="healthcare-agents-phi-inspect"
# Both directions default to injection screening; 09-model-armor-gateway.sh's PHI exercise switches response
# screening to the strict MRN-blocking profile for a separate PHI exercise.
export MODEL_ARMOR_REQUEST_TEMPLATE_ID="${MODEL_ARMOR_REQUEST_TEMPLATE_ID:-$MODEL_ARMOR_TEMPLATE_ID}"
export MODEL_ARMOR_RESPONSE_TEMPLATE_ID="${MODEL_ARMOR_RESPONSE_TEMPLATE_ID:-$MODEL_ARMOR_TEMPLATE_ID}"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GOOGLE_CLOUD_LOCATION="$REGION"
export GOOGLE_GENAI_USE_ENTERPRISE=TRUE
export MCP_REGISTRY_NAME="patient-records-mcp"
# Display names identify the derived registry entries (mcpServers/, agents/),
# whose IDs are generated; see registry_resource() in scripts 07 and 08.
export MCP_REGISTRY_DISPLAY_NAME="Patient Records MCP"
# The guideline specialist is registered in the Agent Registry *agents*
# collection from its A2A agent card (07-deploy-agents.sh), not as a generic endpoint.
export GUIDELINE_REGISTRY_NAME="guideline-agent"
# For A2A agents the registry takes the display name from the card's `name`
# (ADK sets it to the agent name), not from --display-name.
export GUIDELINE_REGISTRY_DISPLAY_NAME="guideline_agent"
export STAGING_BUCKET="gs://${PROJECT_ID}-healthcare-agents-staging"

# Cloud Run URLs are deterministic: SERVICE-PROJECTNUMBER.REGION.run.app.
export IDP_URL="https://${IDP_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
export MCP_URL="https://${MCP_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
export MCP_SERVER_URL="${MCP_URL}/mcp"
export GUIDELINE_AGENT_URL="https://${GUIDELINE_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
export GUIDELINE_AGENT_CARD_URL="${GUIDELINE_AGENT_URL}/.well-known/agent-card.json"
export GUIDELINE_AGENT_CARD_FILE="scripts/rendered/guideline-agent-card.json"

# Google API hostnames the agent process itself must reach through the
# default-deny gateway (registry discovery, model calls, Model Armor,
# project-number lookups, and telemetry export). Agent Identity calls Google
# APIs over mTLS by default, so the .mtls. variants listed in the Agent Gateway
# troubleshooting guide's startup list are registered alongside the plain ones. The agent's Cloud Run ID token comes from its own Agent
# Identity (no service account is impersonated), but the Agent Runtime process
# still calls IAM Credentials over mTLS, and the gateway denies it as
# unregistered unless it is listed here. The gateway matches hostnames EXACTLY, and a
# Google API can resolve through several hostnames depending on the SDK,
# regional configuration, or mTLS use, so the common variants are listed.
# Single source of truth for scripts 05 (register), 08 (grant), 99 (cleanup).
# Space-separated name=url pairs (plain string: arrays do not survive export).
export PLATFORM_ENDPOINTS="\
agentregistry-api=https://agentregistry.googleapis.com/ \
iamcredentials-api=https://iamcredentials.googleapis.com/ \
iamcredentials-mtls=https://iamcredentials.mtls.googleapis.com/ \
telemetry-api=https://telemetry.googleapis.com/ \
telemetry-mtls=https://telemetry.mtls.googleapis.com/ \
logging-api=https://logging.googleapis.com/ \
monitoring-api=https://monitoring.googleapis.com/ \
monitoring-mtls=https://monitoring.mtls.googleapis.com/ \
cloudtrace-api=https://cloudtrace.googleapis.com/ \
cloudtrace-mtls=https://cloudtrace.mtls.googleapis.com/ \
cloudresourcemanager-api=https://cloudresourcemanager.googleapis.com/ \
cloudresourcemanager-mtls=https://cloudresourcemanager.mtls.googleapis.com/ \
aiplatform-api=https://aiplatform.googleapis.com/ \
aiplatform-mtls=https://aiplatform.mtls.googleapis.com/ \
aiplatform-region=https://${REGION}-aiplatform.googleapis.com/ \
aiplatform-region-mtls=https://${REGION}-aiplatform.mtls.googleapis.com/ \
modelarmor-region=https://modelarmor.${REGION}.rep.googleapis.com/"

# Observability resources (11-observability.sh).
export GATEWAY_TRACING_POLICY="${GATEWAY_NAME}-tracing"
export DASHBOARD_DISPLAY_NAME="Healthcare agents: governance and audit"
export EGRESS_DENIAL_METRIC="egress-denials"
export MODEL_ARMOR_BLOCK_METRIC="model-armor-blocks"
export EGRESS_DENIAL_ALERT_DISPLAY_NAME="Healthcare agents: egress denied at the gateway"

echo "Project:      ${PROJECT_ID} (${PROJECT_NUMBER}), org ${ORG_ID:-none (project without an organization)}"
echo "Trust domain: ${AGENT_TRUST_DOMAIN}"
echo "Region:       ${REGION}"
echo "Clinician A:  ${CLINICIAN_A_EMAIL}"
echo "Clinician B:  ${CLINICIAN_B_EMAIL}"
