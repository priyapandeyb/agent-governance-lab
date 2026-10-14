"""Talk to the DEPLOYED clinical assistant on Agent Runtime as a clinician.

This exercises the full governed path:
clinician token (hospital IdP) -> session state -> Agent Runtime agent
(Agent Identity) -> Agent Gateway (IAP egress policy, default deny)
-> Patient Records MCP on Cloud Run (validates the clinician JWT).

The clinician JWT is placed in session state at session creation; the
deployed agent's header_provider reads it from there and attaches it to
every MCP call. The hospital token is data handed to the session, never a
Google credential.

Usage:
    python clients/chat_remote.py \
        --engine "$AGENT_ENGINE_NAME" \
        --idp-url "$IDP_URL" --email "$CLINICIAN_A_EMAIL" \
        --message "List my patients."

--engine is the full resource name printed by deploy_root_agent.py:
projects/PROJECT/locations/REGION/reasoningEngines/ID
"""

import argparse
import os

import requests
import vertexai
from google.genai import types as genai_types


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, help="reasoningEngines resource name")
    parser.add_argument("--idp-url", default=os.environ.get("IDP_URL"), required=False)
    parser.add_argument("--email", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--omit-clinician-token", action="store_true",
                        help="Negative control: invoke the agent without a hospital token")
    args = parser.parse_args()

    if not args.idp_url and not args.omit_clinician_token:
        parser.error("Set IDP_URL or provide --idp-url for the deployed hospital IdP")

    state = {}
    if not args.omit_clinician_token:
        response = requests.post(
            f"{args.idp_url.rstrip('/')}/token",
            json={"email": args.email, "name": args.email.split("@")[0]},
            timeout=30,
        )
        response.raise_for_status()
        state["clinician_token"] = response.json()["access_token"]
        print(f"[chat] Mock hospital persona: {args.email}")
    else:
        print("[chat] Negative control: no clinician token in session")

    project = args.engine.split("/")[1]
    location = args.engine.split("/")[3]
    client = vertexai.Client(
        project=project,
        location=location,
        http_options=genai_types.HttpOptions(api_version="v1beta1"),
    )
    remote_agent = client.agent_engines.get(name=args.engine)

    # The AdkApp template exposes ADK's session and query operations on the
    # deployed engine. The clinician token rides in the session state.
    session = remote_agent.create_session(
        user_id=args.email,
        state=state,
    )
    session_id = session["id"] if isinstance(session, dict) else session.id

    printed_text = False
    for event in remote_agent.stream_query(
        user_id=args.email,
        session_id=session_id,
        message=args.message,
    ):
        if not isinstance(event, dict):
            print(f"\n[event] {event!r}"[:2000])
            continue
        content = event.get("content")
        if content:
            for part in content.get("parts", []):
                if part.get("text"):
                    printed_text = True
                    print(f"\n[{event.get('author', 'agent')}] {part['text']}")
        # A failure inside the deployed agent (gateway 403, tool or model
        # error) arrives as an event with no text; show it instead of exiting
        # silently.
        error = {k: event[k] for k in ("code", "message", "error", "error_code",
                                        "error_message", "detail") if event.get(k)}
        if error:
            print(f"\n[error from the deployed agent] {error}"[:4000])

    if not printed_text:
        print("\n[chat] The agent returned no text. Check the engine logs:\n"
              f"  gcloud logging read 'resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" "
              f"AND resource.labels.reasoning_engine_id=\"{args.engine.rsplit('/', 1)[-1]}\" "
              f"AND severity>=WARNING' --project={project} --freshness=15m --limit=30 "
              "--format='value(timestamp.date(\"%H:%M:%S\"),severity,textPayload)'")


if __name__ == "__main__":
    main()
