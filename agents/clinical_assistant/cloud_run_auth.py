"""Mint a short-lived Cloud Run ID token for the agent's own Agent Identity.

The clinical assistant runs on Agent Runtime with identity_type=AGENT_IDENTITY.
Its Application Default Credentials can issue OpenID Connect ID tokens whose
subject is the agent's SPIFFE ID, and Cloud Run validates such a token at
ingress once the agent principal holds roles/run.invoker on the records
service. No service account, impersonation, or key exists anywhere on this
path: the principal that holds run.invoker is the agent itself.

Two details decide whether Cloud Run accepts the token:

- Audience. Cloud Run compares `aud` with the service origin
  (https://SERVICE-PROJECTNUMBER.REGION.run.app), never a path, so the
  audience is the origin without /mcp.
- Binding. google-auth binds Agent Identity ID tokens to the agent's X.509
  certificate by default (a `cnf` claim). Cloud Run enforces that binding,
  which only works over the service's *.mtls.run.app endpoint with the
  certificate presented on the TLS handshake. The Agent Gateway, not the
  agent, opens the TLS connection to the records service, so the token is
  requested unbound (bind_id_token=False) for the plain *.run.app audience.

The token rides in X-Serverless-Authorization. Cloud Run checks that header
in preference to Authorization and removes the signature before the request
reaches the container, so the MCP server can read the verified claims (and
log the agent's SPIFFE ID) but cannot replay the token. Authorization stays
free for platform use, and the clinician's hospital token has its own header.
"""
import threading
from urllib.parse import urlsplit

from google.auth.transport.requests import Request
from google.oauth2 import id_token

INGRESS_TOKEN_HEADER = "X-Serverless-Authorization"

_lock = threading.Lock()
_credentials_by_audience: dict[str, object] = {}


def _cloud_run_origin(audience: str) -> str:
    target = urlsplit(audience)
    if (target.scheme != "https" or not target.hostname
            or not target.hostname.endswith(".run.app")
            or target.path not in ("", "/") or target.query or target.fragment
            or target.username or target.password or target.port):
        raise ValueError("Cloud Run audience must be the HTTPS service origin, without /mcp")
    return audience.rstrip("/")


def _id_token_credentials(audience: str):
    """One credential object per audience. google-auth caches the token and
    renews it from the runtime's metadata server shortly before it expires."""
    with _lock:
        credentials = _credentials_by_audience.get(audience)
        if credentials is None:
            credentials = id_token.fetch_id_token_credentials(audience, bind_id_token=False)
            _credentials_by_audience[audience] = credentials
        return credentials


def cloud_run_headers(audience: str) -> dict[str, str]:
    """Return the ingress header for a private Cloud Run service: a Google
    OIDC ID token for this agent's identity, audience-bound to the service."""
    origin = _cloud_run_origin(audience)
    credentials = _id_token_credentials(origin)
    if not credentials.valid:
        credentials.refresh(Request())
    token = credentials.token
    if not isinstance(token, str) or not token:
        raise RuntimeError("Agent Identity returned no Cloud Run ID token")
    return {INGRESS_TOKEN_HEADER: f"Bearer {token}"}
