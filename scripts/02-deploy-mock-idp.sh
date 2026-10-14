#!/usr/bin/env bash
# Deploy the mock hospital IdP. --max-instances=1 keeps the in-memory signing
# key consistent (each instance would otherwise publish a different JWKS).
# The ISSUER env var must equal the public URL so token iss claims validate.
set -euo pipefail
cd "$(dirname "$0")/.."

gcloud run deploy "${IDP_SERVICE}" \
  --source mock-idp \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --allow-unauthenticated \
  --max-instances=1 \
  --set-env-vars "ISSUER=${IDP_URL}"

echo
echo "Mock IdP: ${IDP_URL}"
echo "JWKS:     ${IDP_URL}/.well-known/jwks.json"
echo
echo "Mint a token for clinician A:"
echo "  export CLINICIAN_TOKEN=\$(python clients/get_token.py --idp-url ${IDP_URL} --email ${CLINICIAN_A_EMAIL})"
