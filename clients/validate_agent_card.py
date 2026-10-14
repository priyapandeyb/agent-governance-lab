"""Validate an A2A agent card before it is registered in Agent Registry.

Agent Registry indexes an A2A agent from its card: the `url` becomes the
destination the gateway matches egress against, and `skills` become the
discoverable capabilities. A card that advertises the wrong `url` (for
example `http://localhost:8000`, the to_a2a() default when A2A_HOST is not
set) would register a destination the agent can never reach, and the root
agent's RemoteA2aAgent would try to call localhost from inside Agent Runtime.

Usage:
    python clients/validate_agent_card.py \
        --card-file scripts/rendered/guideline-agent-card.json \
        --expected-url "$GUIDELINE_AGENT_URL" \
        --expected-name guideline_agent

Exits non-zero with a plain explanation when a check fails, so the deploy
script stops before registering a broken card.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SUPPORTED_PROTOCOL_VERSIONS = ("0.3", "1.0")
MAX_CARD_BYTES = 10 * 1024  # Agent Registry rejects spec files above 10 KB.


def _normalize_url(url: str) -> str:
    """Drop a trailing slash and the scheme's default port, so
    https://host:443/ and https://host compare equal."""
    url = (url or "").strip().rstrip("/")
    for scheme, port in (("https://", ":443"), ("http://", ":80")):
        if url.startswith(scheme):
            rest = url[len(scheme):]
            host, sep, path = rest.partition("/")
            if host.endswith(port):
                host = host[: -len(port)]
            url = scheme + host + (sep + path if sep else "")
    return url.rstrip("/")


def card_endpoint(card: dict) -> tuple[str, str]:
    """Return (rpc_url, protocol_version) for both card shapes.

    A2A 0.3 cards carry a top-level `url` and `protocolVersion`. A2A 1.0 cards
    (a2a-sdk 1.x, which ADK's to_a2a() uses) move them into
    `supportedInterfaces[]`, one entry per protocol binding; the JSON-RPC
    entry is the one clients and the registry use.
    """
    if card.get("url"):
        return card["url"], str(card.get("protocolVersion") or "")
    interfaces = card.get("supportedInterfaces") or []
    preferred = [i for i in interfaces if str(i.get("protocolBinding", "")).upper() == "JSONRPC"]
    for interface in preferred or interfaces:
        if interface.get("url"):
            return interface["url"], str(interface.get("protocolVersion") or "")
    return "", ""


def check_card(card: dict, expected_url: str, expected_name: str | None) -> list[str]:
    """Return a list of human-readable problems; empty means the card is usable."""
    problems: list[str] = []

    name = card.get("name")
    if not name:
        problems.append("card has no `name`")
    elif expected_name and name != expected_name:
        problems.append(f"card `name` is {name!r}, expected {expected_name!r}")

    if not card.get("description"):
        problems.append("card has no `description` (the registry shows this to other developers)")

    raw_url, version = card_endpoint(card)
    url = _normalize_url(raw_url)
    want = _normalize_url(expected_url)
    if not url:
        problems.append("card has no `url` (0.3) or `supportedInterfaces[].url` (1.0)")
    elif url != want:
        problems.append(
            f"card `url` is {url!r}, expected {want!r}; "
            "set A2A_HOST/A2A_PROTOCOL/A2A_PORT on the Cloud Run service"
        )
    elif not url.startswith("https://"):
        problems.append(f"card `url` must be https for a public Cloud Run service, got {url!r}")
    elif raw_url.strip().rstrip("/").split("://", 1)[-1].split("/", 1)[0].endswith((":443", ":80")):
        problems.append(
            f"card `url` {raw_url!r} carries an explicit default port. Agent Registry "
            "stores it verbatim, the Agent Gateway matches the request hostname (no "
            "port) exactly, and every A2A call is denied as unregistered. Publish the "
            "URL without :443 (see agents/guideline_agent/a2a_main.py)"
        )

    if not any(version.startswith(v) for v in SUPPORTED_PROTOCOL_VERSIONS):
        problems.append(
            f"card `protocolVersion` is {version!r}; Agent Registry supports A2A "
            f"{' and '.join(SUPPORTED_PROTOCOL_VERSIONS)}"
        )

    skills = card.get("skills") or []
    if not isinstance(skills, list) or not skills:
        problems.append("card advertises no `skills`; nothing would be indexed for discovery")
    else:
        for i, skill in enumerate(skills):
            if not skill.get("id") or not skill.get("name"):
                problems.append(f"skill #{i} is missing `id` or `name`")

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--card-file", required=True, help="Path to the saved agent-card.json")
    parser.add_argument("--expected-url", required=True,
                        help="Public HTTPS URL the card must advertise (the Cloud Run service URL)")
    parser.add_argument("--expected-name", default=None,
                        help="Agent name the card must carry (the ADK agent's `name`)")
    args = parser.parse_args()

    path = Path(args.card_file)
    raw = path.read_bytes()
    if len(raw) > MAX_CARD_BYTES:
        print(f"FAIL: {path} is {len(raw)} bytes; Agent Registry caps spec files at {MAX_CARD_BYTES}")
        sys.exit(1)
    try:
        card = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL: {path} is not valid JSON: {exc}")
        sys.exit(1)

    problems = check_card(card, args.expected_url, args.expected_name)
    if problems:
        print("FAIL: agent card is not ready to register:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    skills = ", ".join(s.get("id", "?") for s in card.get("skills", []))
    print("OK: agent card is ready to register")
    print(f"  name:            {card['name']}")
    rpc_url, version = card_endpoint(card)
    print(f"  url:             {rpc_url}")
    print(f"  protocolVersion: {version}")
    print(f"  transport:       {card.get('preferredTransport', 'JSONRPC')}")
    print(f"  skills:          {skills}")
    print(f"  securitySchemes: {'present' if card.get('securitySchemes') else 'none (public workshop endpoint)'}")


if __name__ == "__main__":
    main()
