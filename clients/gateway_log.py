"""Print Agent Gateway decisions as one readable row per request.

Reads the gateway's request log (resource.type networkservices.googleapis.com/Gateway)
with gcloud and prints, per row:

    TIME  STATUS  MCP METHOD  TOOL  OVERALL  per-policy verdicts  DESTINATION

The fields come from the gateway's LoadBalancerLogEntry:

    jsonPayload.agentGatewayInfo.mcpInfo.method       initialize, tools/list, tools/call ...
    jsonPayload.agentGatewayInfo.mcpInfo.parameter    the tool name for tools/call
    jsonPayload.agentGatewayInfo.agentRegistryResource  the registry entry that matched
    jsonPayload.authzPolicyInfo.result                overall ALLOWED / DENIED
    jsonPayload.authzPolicyInfo.policies[].name/.result  one verdict per authz policy
                                                      (...-authz-policy-iap, ...-model-armor)
    jsonPayload.tlsSniHostname / enforcedGatewaySecurityPolicy.hostname  destination

Usage (after `source scripts/00-env.sh`):
    python clients/gateway_log.py                 # last 30 min, MCP calls only
    python clients/gateway_log.py --all           # every destination (Google APIs too)
    python clients/gateway_log.py --denied        # only rows some policy denied
    python clients/gateway_log.py --freshness 2h
"""

import argparse
import json
import os
import subprocess
import sys


def short_policy(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    for prefix in (os.environ.get("GATEWAY_NAME", "healthcare-agents-gateway") + "-authz-policy-",
                   "authz-policy-"):
        if base.startswith(prefix):
            return base[len(prefix):]
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--gateway", default=os.environ.get("GATEWAY_NAME"))
    parser.add_argument("--freshness", default="30m")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--all", action="store_true", help="include non-MCP destinations")
    parser.add_argument("--denied", action="store_true", help="only rows a policy denied")
    args = parser.parse_args()
    if not (args.project and args.gateway):
        sys.exit("Set PROJECT_ID and GATEWAY_NAME (source scripts/00-env.sh).")

    log_filter = (f'resource.type="networkservices.googleapis.com/Gateway" '
                  f'AND resource.labels.gateway_name="{args.gateway}"')
    if not args.all:
        log_filter += " AND jsonPayload.agentGatewayInfo.mcpInfo.method:*"
    if args.denied:
        log_filter += ' AND jsonPayload.authzPolicyInfo.result="DENIED"'

    out = subprocess.run(
        ["gcloud", "logging", "read", log_filter, f"--project={args.project}",
         f"--freshness={args.freshness}", f"--limit={args.limit}", "--format=json"],
        check=True, capture_output=True, text=True).stdout
    entries = json.loads(out or "[]")
    if not entries:
        print("No gateway rows match. Widen --freshness, or add --all to include Google API calls.")
        return

    print(f"{'TIME':8}  {'HTTP':4}  {'MCP METHOD':26}  {'TOOL':20}  {'OVERALL':8}  POLICIES  ->  DESTINATION")
    for e in reversed(entries):  # oldest first, like a story
        j = e.get("jsonPayload", {})
        mcp = (j.get("agentGatewayInfo") or {}).get("mcpInfo") or {}
        authz = j.get("authzPolicyInfo") or {}
        policies = ", ".join(f"{short_policy(p.get('name', ''))}={p.get('result', '?')}"
                             for p in authz.get("policies") or []) or "-"
        dest = (j.get("tlsSniHostname")
                or (j.get("enforcedGatewaySecurityPolicy") or {}).get("hostname") or "?")
        print(f"{e['timestamp'][11:19]:8}  {str((e.get('httpRequest') or {}).get('status', '-')):4}  "
              f"{mcp.get('method', '-'):26}  {mcp.get('parameter', ''):20}  "
              f"{authz.get('result', '-'):8}  {policies}  ->  {dest}")


if __name__ == "__main__":
    main()
