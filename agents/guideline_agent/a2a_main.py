"""Serve the guideline agent over the A2A protocol.

to_a2a() wraps the ADK agent in an ASGI app that speaks A2A and publishes an
auto-generated agent card at /.well-known/agent-card.json. The host/protocol/
port passed here are baked into the card's RPC URL, so they must describe the
address CLIENTS use to reach this server:

Set A2A_HOST to the Cloud Run service hostname (SERVICE-PROJECTNUMBER.
REGION.run.app). The published address uses HTTPS port 443; the container
still binds uvicorn to $PORT.

Observability: when ENABLE_CLOUD_TRACE=true, the process exports
OpenTelemetry spans to Cloud Trace and joins the caller's trace through the
W3C `traceparent` header the root agent and the Agent Gateway propagate, so
one trace shows clinician -> clinical_assistant -> gateway -> guideline_agent.
Only span metadata is exported: ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS stays
false, so question text and answers never leave the process as telemetry.
Any failure to set up tracing is logged and ignored; it must never stop the
A2A server from starting.
"""

import logging
import os

from google.adk.a2a.utils.agent_to_a2a import to_a2a

try:
    from agent import root_agent  # uvicorn run from inside this directory
except ImportError:
    from .agent import root_agent  # imported as a package member

logger = logging.getLogger("guideline_agent")

# Never export message bodies from this agent's spans, whatever the caller set.
os.environ.setdefault("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "false")
os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "NO_CONTENT")


def _configure_cloud_trace() -> bool:
    """Export spans to Cloud Trace. Returns True when tracing is active."""
    if os.environ.get("ENABLE_CLOUD_TRACE", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # exporter not installed: run without tracing
        logger.warning("Cloud Trace export disabled (missing dependency): %s", exc)
        return False
    try:
        provider = TracerProvider(resource=Resource.create({
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "guideline-agent"),
        }))
        provider.add_span_processor(BatchSpanProcessor(
            CloudTraceSpanExporter(project_id=os.environ.get("GOOGLE_CLOUD_PROJECT"))
        ))
        trace.set_tracer_provider(provider)
        logger.info("Cloud Trace export enabled for guideline_agent")
        return True
    except Exception as exc:  # noqa: BLE001 - observability must not break serving
        logger.warning("Cloud Trace export disabled (setup failed): %s", exc)
        return False


_TRACING = _configure_cloud_trace()

A2A_HOST = os.environ["A2A_HOST"]
A2A_PROTOCOL = os.environ.get("A2A_PROTOCOL", "https")
A2A_PORT = int(os.environ.get("A2A_PORT", "443"))


def _public_agent_card():
    """Build the card ourselves so its URL carries no default port.

    to_a2a() always writes the RPC URL as PROTOCOL://HOST:PORT/, which for the
    public Cloud Run address becomes https://HOST:443/. Agent Registry stores
    that string as the agent's interface, and the Agent Gateway matches the
    request hostname (no port) exactly against it: the specialist is then
    "unregistered" and every A2A call is denied with 403 (agentGatewayInfo is
    empty in the gateway log). Omitting the scheme's default port makes the
    card, the registry entry, and the request agree.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder

    default_port = {"https": 443, "http": 80}.get(A2A_PROTOCOL)
    authority = A2A_HOST if A2A_PORT == default_port else f"{A2A_HOST}:{A2A_PORT}"
    builder = AgentCardBuilder(agent=root_agent, rpc_url=f"{A2A_PROTOCOL}://{authority}/")
    # build() is async; run it on a private loop so this works whether or not
    # the importer (uvicorn) already has an event loop running.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, builder.build()).result()


a2a_app = to_a2a(
    root_agent,
    host=A2A_HOST,
    protocol=A2A_PROTOCOL,
    port=A2A_PORT,
    agent_card=_public_agent_card(),
)

if _TRACING:
    # Server-side spans join the incoming trace context (traceparent header)
    # so the A2A hop appears as a child of the root agent's invocation span.
    try:
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

        a2a_app = OpenTelemetryMiddleware(a2a_app)
    except ImportError as exc:
        logger.warning("ASGI trace propagation disabled (missing dependency): %s", exc)
