"""Deploy the clinical assistant to Agent Runtime with:

- identity_type AGENT_IDENTITY: the agent gets its own SPIFFE-based Agent
  Identity (not a shared service account). This principal is what the
  gateway's IAP policies authorize.
- agent_gateway_config: binds the agent's egress to the Agent Gateway, so
  every outbound MCP and A2A call is intercepted and policy-checked.
- OpenTelemetry enabled (GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY) with
  message-content capture OFF, so traces and GenAI metrics flow to Cloud
  Observability while prompts, patient rows, and model output do not.

Agent Runtime registers the engine in Agent Registry automatically; 07-deploy-agents.sh
verifies it alongside the manually registered A2A guideline agent.

Requires env vars (see scripts/00-env.sh): PROJECT_ID, REGION, GATEWAY_NAME,
STAGING_BUCKET, MCP_REGISTRY_NAME, GUIDELINE_AGENT_URL,
MODEL_ARMOR_TEMPLATE_ID. Optional: TELEMETRY_CONTENT_CAPTURE (default
NO_CONTENT; anything else exports message bodies and is for synthetic data
only).

Prints the reasoningEngines resource name and the effective Agent Identity
principal; save both, the IAM grant scripts need them.

Usage:
  python deploy/deploy_root_agent.py            # create a NEW engine (new identity)
  python deploy/deploy_root_agent.py --update   # patch $AGENT_ENGINE_NAME in place;
                                                # same engine ID, same Agent Identity,
                                                # so existing IAM grants keep working
"""

import os
import sys

import vertexai
from vertexai import types
from vertexai.agent_engines import AdkApp

AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents"))
sys.path.insert(0, AGENTS_DIR)

from clinical_assistant.agent import build_registry_agent  # noqa: E402
from clinical_assistant.plugins.model_armor import ModelArmorPlugin  # noqa: E402

PROJECT_ID = os.environ["PROJECT_ID"]
REGION = os.environ["REGION"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
STAGING_BUCKET = os.environ["STAGING_BUCKET"]
# The derived mcpServers/ resource (generated ID) that ADK discovery resolves;
# 07-deploy-agents.sh looks it up by display name and exports it.
MCP_REGISTRY_NAME = os.environ.get("MCP_REGISTRY_RESOURCE") or os.environ["MCP_REGISTRY_NAME"]
GUIDELINE_AGENT_URL = os.environ["GUIDELINE_AGENT_URL"]
MODEL_ARMOR_TEMPLATE_ID = os.environ["MODEL_ARMOR_TEMPLATE_ID"]

# Observability: what the agent is allowed to export about message CONTENT.
# NO_CONTENT (default) exports spans, metrics, and logs without prompts, tool
# results, or model output. The other OpenTelemetry GenAI values copy message
# bodies into Cloud Logging/Trace, which for this agent means patient data.
# Only override for synthetic, non-PHI debugging, and never in production.
_ALLOWED_CONTENT_CAPTURE = {"NO_CONTENT", "SPAN_ONLY", "EVENT_ONLY", "SPAN_AND_EVENT"}
TELEMETRY_CONTENT_CAPTURE = os.environ.get("TELEMETRY_CONTENT_CAPTURE", "NO_CONTENT").upper()
if TELEMETRY_CONTENT_CAPTURE not in _ALLOWED_CONTENT_CAPTURE:
    raise SystemExit(
        f"TELEMETRY_CONTENT_CAPTURE={TELEMETRY_CONTENT_CAPTURE!r} is not one of "
        f"{sorted(_ALLOWED_CONTENT_CAPTURE)}"
    )
if TELEMETRY_CONTENT_CAPTURE != "NO_CONTENT":
    print(
        f"WARNING: TELEMETRY_CONTENT_CAPTURE={TELEMETRY_CONTENT_CAPTURE}: prompts, tool "
        "results, and model output will be exported to Cloud Observability. Use only "
        "with synthetic data.",
        file=sys.stderr,
    )

vertexai.init(project=PROJECT_ID, location=REGION)

agent = build_registry_agent(
    project_id=PROJECT_ID,
    location=REGION,
    mcp_server_name=MCP_REGISTRY_NAME,
    guideline_agent_url=GUIDELINE_AGENT_URL,
    mcp_audience=os.environ["MCP_URL"],
)
app = AdkApp(agent=agent, plugins=[ModelArmorPlugin(
    project_id=PROJECT_ID, location_id=REGION,
    template_id=MODEL_ARMOR_TEMPLATE_ID,
)])

# What Agent Runtime installs before it unpickles the agent. The agent object
# is serialized here and rebuilt there, so every library whose classes it
# holds must be installed at the SAME version: the registry MCP toolset
# (mcp), the A2A sub-agent (a2a-sdk), the ID-token helper (google-auth >= 2.62
# for bind_id_token), and the serializer itself (cloudpickle, pydantic).
# Missing one fails the deploy with "No module named ..." in the engine logs.
def _pinned(dist: str) -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return f"{dist}=={version(dist)}"
    except PackageNotFoundError:
        sys.exit(f"{dist} is not installed in this environment; run `uv sync` first.")


RUNTIME_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent_engines]",
    "google-adk[a2a,agent-identity]==2.7.1",
    "google-cloud-modelarmor",
    _pinned("mcp"),
    _pinned("a2a-sdk"),
    _pinned("google-auth"),
    _pinned("cloudpickle"),
    _pinned("pydantic"),
]
print("Runtime requirements:", ", ".join(RUNTIME_REQUIREMENTS))

client = vertexai.Client(
    project=PROJECT_ID,
    location=REGION,
    http_options=dict(api_version="v1beta1"),  # required for identity_type
)

# extra_packages keeps the path as given, and Agent Runtime puts the package
# root on sys.path. Packaging "agents/clinical_assistant" would land the code
# under agents/ and unpickling fails with "No module named clinical_assistant",
# so package it from inside agents/ as the top-level "clinical_assistant".
_previous_cwd = os.getcwd()
os.chdir(AGENTS_DIR)

UPDATE_EXISTING = "--update" in sys.argv[1:]
if UPDATE_EXISTING and not os.environ.get("AGENT_ENGINE_NAME"):
    sys.exit("--update needs AGENT_ENGINE_NAME (source scripts/rendered/agent.env).")

CONFIG = {
        "display_name": "clinical-assistant",
        # Agent Runtime registers this engine in Agent Registry automatically;
        # the description is what other developers see in the Agents tab.
        "description": (
            "Clinician-facing assistant over synthetic patient records. Reads "
            "records through the governed Patient Records MCP server and "
            "delegates guideline questions to guideline_agent over A2A."
        ),
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "agent_gateway_config": {
            "agent_to_anywhere_config": {
                "agent_gateway": (
                    f"projects/{PROJECT_ID}/locations/{REGION}"
                    f"/agentGateways/{GATEWAY_NAME}"
                )
            }
        },
        "staging_bucket": STAGING_BUCKET,
        "requirements": RUNTIME_REQUIREMENTS,
        "extra_packages": ["clinical_assistant"],   # relative to AGENTS_DIR (see chdir above)
        "env_vars": {
            # GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION are reserved: Agent
            # Runtime sets them itself and rejects a deploy that passes them
            # (400 FAILED_PRECONDITION). The agent and plugin already received
            # the project and region as constructor arguments above.
            "MCP_REGISTRY_NAME": MCP_REGISTRY_NAME,
            "GUIDELINE_AGENT_URL": GUIDELINE_AGENT_URL,
            "MODEL_ARMOR_TEMPLATE_ID": MODEL_ARMOR_TEMPLATE_ID,
            "ADK_SUPPRESS_A2A_EXPERIMENTAL_FEATURE_WARNINGS": "true",
            # --- Observability (11-observability.sh) -------------------------------
            # Exports ADK traces, GenAI metrics (tokens, latency per model and
            # tool), and logs through the Telemetry/Logging/Monitoring APIs,
            # which is why those hostnames are registered platform endpoints.
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
            # PHI boundary: span/metric METADATA only. Prompts, tool results
            # (patient rows), and model responses are never exported. Do not
            # set EVENT_ONLY/SPAN_AND_EVENT here: that would copy patient data
            # into Cloud Logging. Override only against synthetic data.
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": TELEMETRY_CONTENT_CAPTURE,
            "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
            # --- Agent Gateway + Google APIs (WORKAROUND) ---------------------------
            # By default google-auth requests Google API access tokens bound to the
            # agent's X.509 certificate (mTLS). Behind the gateway the TLS session
            # is intercepted and re-originated, so the Google API never sees that
            # certificate, and google-auth does not send DPoP. Every call made from
            # inside the engine (Sessions API, Vertex AI, Model Armor, telemetry)
            # then fails with 401 even though the gateway logged ALLOWED.
            # This opts out of the binding for Google API tokens only. The gateway
            # still enforces IAP egress per registry entry, and Cloud Run calls
            # still use the agent's ID token. Remove once the runtime binds tokens
            # with DPoP across the gateway.
            "GOOGLE_API_ENABLE_RUNTIME_BOUND_TOKEN": "false",
        },
}

if UPDATE_EXISTING:
    print(f"Updating {os.environ['AGENT_ENGINE_NAME']} in place (identity unchanged).")
    remote_app = client.agent_engines.update(
        name=os.environ["AGENT_ENGINE_NAME"], agent=app, config=CONFIG,
    )
else:
    remote_app = client.agent_engines.create(agent=app, config=CONFIG)

os.chdir(_previous_cwd)
resource = remote_app.api_resource
print("\nDeployed.")
print(f"AGENT_ENGINE_NAME={resource.name}")
print(f"AGENT_IDENTITY={resource.spec.effective_identity}")
