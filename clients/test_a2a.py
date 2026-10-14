"""A2A smoke test: discover an agent from its card, then send it one message.

This is the same two-step contract the root agent's RemoteA2aAgent follows:

1. GET  {agent_url}/.well-known/agent-card.json  -> read the RPC url and `skills`
2. POST {card.url}  JSON-RPC 2.0 `message/send`   -> a Task or Message result

Run it from Cloud Shell against the public guideline specialist to prove the
A2A server works before you wire it behind the gateway. It sends ONLY a
synthetic, identifier-free clinical question; never paste real patient data.

Usage:
    python clients/test_a2a.py --agent-url "$GUIDELINE_AGENT_URL"
    python clients/test_a2a.py --agent-url "$GUIDELINE_AGENT_URL" \
        --message "For an adult with stage 1 hypertension and no comorbidities, what are first-line options?"

Exit code 0 means the card was readable and the agent returned at least one
text part. The raw JSON-RPC response is printed with --verbose.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import requests

import sys as _sys
from pathlib import Path as _Path

# Shared with the card validator so both read 0.3 and 1.0 card shapes the same way.
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from validate_agent_card import card_endpoint  # noqa: E402

AGENT_CARD_PATH = "/.well-known/agent-card.json"
DEFAULT_QUESTION = (
    "For a type 2 diabetes patient with HbA1c 8.2 percent and eGFR 58 already on "
    "metformin, what do guidelines suggest as the next medication class? "
    "No patient identifiers are included."
)


def fetch_agent_card(agent_url: str, timeout: int = 30) -> dict:
    """Step 1 of A2A discovery: read the public agent card."""
    resp = requests.get(agent_url.rstrip("/") + AGENT_CARD_PATH, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def build_send_request(text: str, request_id: str | None = None) -> dict:
    """Build an A2A `message/send` JSON-RPC 2.0 request for one text part."""
    return {
        "jsonrpc": "2.0",
        "id": request_id or str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


def extract_text_parts(result: dict) -> list[str]:
    """Pull text out of a Task (artifacts/status message) or a bare Message."""
    texts: list[str] = []

    def take(parts):
        for part in parts or []:
            if isinstance(part, dict) and part.get("kind") == "text" and part.get("text"):
                texts.append(part["text"])

    if not isinstance(result, dict):
        return texts
    if result.get("kind") == "message":
        take(result.get("parts"))
        return texts
    # Task shape: artifacts[].parts, then status.message.parts, then history.
    for artifact in result.get("artifacts") or []:
        take(artifact.get("parts"))
    status_message = (result.get("status") or {}).get("message") or {}
    take(status_message.get("parts"))
    if not texts:
        for message in reversed(result.get("history") or []):
            if message.get("role") == "agent":
                take(message.get("parts"))
                if texts:
                    break
    return texts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-url", required=True, help="Base URL of the A2A server")
    parser.add_argument("--message", default=DEFAULT_QUESTION,
                        help="Synthetic, identifier-free question to send")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--verbose", action="store_true", help="Print the raw JSON-RPC response")
    args = parser.parse_args()

    print(f"[a2a] 1) GET agent card from {args.agent_url.rstrip('/')}{AGENT_CARD_PATH}")
    card = fetch_agent_card(args.agent_url, timeout=args.timeout)
    rpc_url, protocol_version = card_endpoint(card)   # 0.3 `url` or 1.0 `supportedInterfaces`
    skills = [s.get("id") or s.get("name") for s in card.get("skills") or []]
    print(f"[a2a]    name={card.get('name')!r} protocolVersion={protocol_version!r} "
          f"transport={card.get('preferredTransport', 'JSONRPC')!r}")
    print(f"[a2a]    rpc url={rpc_url!r} skills={skills}")
    if not rpc_url:
        print("[a2a] FAIL: card has no `url`; nothing to call")
        sys.exit(1)

    print("[a2a] 2) POST message/send (JSON-RPC 2.0) to the card's url")
    payload = build_send_request(args.message)
    resp = requests.post(rpc_url, json=payload, timeout=args.timeout,
                         headers={"Content-Type": "application/json"})
    if args.verbose:
        print(json.dumps(resp.json() if resp.content else {}, indent=2)[:4000])
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        print(f"[a2a] FAIL: JSON-RPC error: {body['error']}")
        sys.exit(1)

    result = body.get("result") or {}
    state = ((result.get("status") or {}).get("state")) if isinstance(result, dict) else None
    texts = extract_text_parts(result)
    print(f"[a2a]    result kind={result.get('kind')!r} task state={state!r}")
    if state in ("failed", "rejected", "canceled", "TASK_STATE_FAILED", "TASK_STATE_REJECTED"):
        detail = texts[0].strip()[:600] if texts else "(no detail)"
        print(f"[a2a] FAIL: the specialist ran but the task ended {state!r}: {detail}")
        sys.exit(1)
    if not texts:
        print("[a2a] FAIL: no text parts in the response (re-run with --verbose)")
        sys.exit(1)

    print("[a2a] 3) Specialist reply (first text part):")
    print("-" * 60)
    print(texts[0].strip()[:2000])
    print("-" * 60)
    print("[a2a] OK: card discovery and message/send both worked")


if __name__ == "__main__":
    main()
