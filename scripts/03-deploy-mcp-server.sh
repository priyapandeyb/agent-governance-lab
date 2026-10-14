#!/usr/bin/env bash
# Private MCP backend: Cloud Run IAM plus independent clinician authorization.
# --min-instances=1: ADK's MCP client waits only 5 s for the MCP handshake. A
# cold start of this service takes about 6 s, the handshake times out, and the
# agent silently runs with zero tools ("I cannot access patient-specific lab
# results with the available tools"). One warm instance avoids that.
set -euo pipefail
cd "$(dirname "$0")/.."

gcloud run deploy "${MCP_SERVICE}" \
  --source mcp-server \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --no-allow-unauthenticated \
  --invoker-iam-check \
  --min-instances=1 \
  --set-env-vars "CLINICIAN_IDP_ISSUER=${IDP_URL},CLINICIAN_TOKEN_AUDIENCE=patient-records-mcp,CLINICIAN_A_EMAIL=${CLINICIAN_A_EMAIL},CLINICIAN_B_EMAIL=${CLINICIAN_B_EMAIL}"

# Remove both public principal forms when upgrading an existing workshop.
mkdir -p scripts/rendered
gcloud run services get-iam-policy "${MCP_SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format=json \
  > scripts/rendered/mcp-iam-before.json
python - <<'PYIAM'
import json
from pathlib import Path
p = json.loads(Path('scripts/rendered/mcp-iam-before.json').read_text())
for binding in p.get('bindings', []):
    binding['members'] = [m for m in binding.get('members', [])
                          if m not in ('allUsers', 'allAuthenticatedUsers')]
p['bindings'] = [b for b in p.get('bindings', []) if b['members']]
Path('scripts/rendered/mcp-iam-private.json').write_text(json.dumps(p))
PYIAM
gcloud run services set-iam-policy "${MCP_SERVICE}" scripts/rendered/mcp-iam-private.json \
  --project="${PROJECT_ID}" --region="${REGION}"

# No invoker is granted here. The only principal that will ever hold
# roles/run.invoker on this service is the clinical assistant's own Agent
# Identity, bound in 08-grant-egress-and-enforce.sh once the agent exists.
echo "Private MCP endpoint: ${MCP_SERVER_URL}"
echo "Anonymous and hospital-token-only calls must fail at Cloud Run."
echo "Agent access becomes available once 08-grant-egress-and-enforce.sh binds run.invoker to the agent's identity."
