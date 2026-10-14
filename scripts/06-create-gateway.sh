#!/usr/bin/env bash
# Create the egress Agent Gateway and attach the IAP authorization extension
# in DRY_RUN (audit-only) mode. Enforcement is flipped on later, after the
# IAM grants exist (scripts/08-grant-egress-and-enforce.sh), following the
# documented rollout: observe in dry run, then enforce.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p scripts/rendered

for tmpl in agent-gateway-egress iap-authz-extension-dryrun iap-authz-policy; do
  envsubst < "scripts/yaml/${tmpl}.yaml.tmpl" > "scripts/rendered/${tmpl}.yaml"
done

echo "== Create the gateway =="
gcloud network-services agent-gateways import "${GATEWAY_NAME}" \
  --source="scripts/rendered/agent-gateway-egress.yaml" \
  --location="${REGION}" \
  --project="${PROJECT_ID}"

echo "== Create the IAP authorization (service) extension, DRY_RUN =="
gcloud beta service-extensions authz-extensions import "${GATEWAY_NAME}-authz-iap" \
  --source="scripts/rendered/iap-authz-extension-dryrun.yaml" \
  --location="${REGION}" \
  --project="${PROJECT_ID}"

echo "== Bind the extension to the gateway with an authorization policy =="
gcloud network-security authz-policies import "${GATEWAY_NAME}-authz-policy-iap" \
  --source="scripts/rendered/iap-authz-policy.yaml" \
  --location="${REGION}" \
  --project="${PROJECT_ID}"

echo
gcloud network-services agent-gateways describe "${GATEWAY_NAME}" \
  --location="${REGION}" --project="${PROJECT_ID}"
