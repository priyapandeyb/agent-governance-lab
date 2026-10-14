"""Smoke-test the Patient Records MCP server directly (no agents involved).

Demonstrates the on-behalf-of contract at the MCP layer:
- with a valid clinician token, panel patients are returned;
- for a patient off the clinician's panel, the server answers access_denied;
- with no token, every tool answers unauthenticated.

Usage:
    python clients/test_mcp.py --mcp-url https://patient-records-mcp-....run.app/mcp \
        --token "$CLINICIAN_TOKEN"
    python clients/test_mcp.py --mcp-url ... --token ""      # no-token case
"""

import argparse
import asyncio
import json
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


def _print(label: str, result) -> None:
    text = result.content[0].text if result.content else "<empty>"
    try:
        text = json.dumps(json.loads(text), indent=2)
    except (ValueError, TypeError):
        pass
    print(f"\n=== {label} ===\n{text}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", required=True, help=".../mcp endpoint URL")
    parser.add_argument("--token", required=True, help="Clinician JWT ('' for none)")
    args = parser.parse_args()

    headers = {}
    if args.token:
        headers = {
            "X-Clinician-Token": args.token,
        }
    # Google ID token for Cloud Run IAM is distinct from the hospital access
    # token. Never use gcloud print-access-token in this slot.
    if cloud_run_id_token := os.environ.get("CLOUD_RUN_ID_TOKEN"):
        headers["X-Serverless-Authorization"] = f"Bearer {cloud_run_id_token}"
    transport = StreamableHttpTransport(url=args.mcp_url, headers=headers)

    async with Client(transport) as client:
        tools = await client.list_tools()
        print("Tools:", ", ".join(t.name for t in tools))

        _print("whoami", await client.call_tool("whoami", {}))
        _print("list_my_patients", await client.call_tool("list_my_patients", {}))
        _print(
            "get_recent_labs P-1001 (on clinician A's panel)",
            await client.call_tool("get_recent_labs", {"patient_id": "P-1001"}),
        )
        _print(
            "get_patient_summary P-2001 (NOT on clinician A's panel)",
            await client.call_tool("get_patient_summary", {"patient_id": "P-2001"}),
        )


if __name__ == "__main__":
    asyncio.run(main())
