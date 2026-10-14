"""Patient Records MCP server for Cloud Run.

Two identities arrive on every tool call, and they are checked at different
layers:

- The AGENT's identity (SPIFFE-based Agent Identity) is checked twice before
  any code here runs: Agent Gateway + IAP authorize the specific tool on the
  agent's egress path, and Cloud Run's invoker IAM check validates the
  agent's own ID token at this private service's front door. Cloud Run
  strips that token's signature and forwards the verified claims in
  X-Serverless-Authorization, so this application can read and log the
  calling agent's SPIFFE ID without verifying anything itself.

- The CLINICIAN's identity is a JWT minted by the hospital IdP (a non-Google
  identity provider - the mock-idp service in this codelab). It rides in the
  X-Clinician-Token header. This server validates it against the IdP's JWKS
  and uses its email claim for row-level authorization: clinicians can only
  read patients whose care team they are on.

An agent that is allowed through the gateway still gets nothing without a
valid clinician token - that is the on-behalf-of contract.
"""

import base64
import binascii
import json
import logging
import os

import uvicorn

import jwt
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

import records

logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

CLINICIAN_TOKEN_HEADER = "x-clinician-token"
# Cloud Run checks this header (an OIDC ID token for the caller) in preference
# to Authorization, then removes its signature before the request reaches the
# container. Only requests that passed the invoker IAM check arrive here, so
# the remaining claims are trustworthy, but the token cannot be replayed.
INGRESS_TOKEN_HEADER = "x-serverless-authorization"
IDP_ISSUER = os.environ.get("CLINICIAN_IDP_ISSUER", "http://localhost:9000")
JWKS_URL = os.environ.get("CLINICIAN_JWKS_URL", f"{IDP_ISSUER.rstrip('/')}/.well-known/jwks.json")
TOKEN_AUDIENCE = os.environ.get("CLINICIAN_TOKEN_AUDIENCE", "patient-records-mcp")

_jwks_client = jwt.PyJWKClient(JWKS_URL, timeout=10)

mcp = FastMCP("Patient Records MCP")


class AuthError(Exception):
    pass


def _agent_identity(headers: dict) -> str | None:
    """Return the calling agent's identity (the ID token subject, a SPIFFE ID
    for Agent Identity callers) from the Cloud Run-verified ingress token, or
    None when the request did not carry one."""
    value = headers.get(INGRESS_TOKEN_HEADER, "")
    if not value.lower().startswith("bearer "):
        return None
    segments = value[7:].strip().split(".")
    if len(segments) < 2:
        return None
    try:
        payload = segments[1] + "=" * (-len(segments[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, binascii.Error):
        return None
    subject = claims.get("sub") or claims.get("email")
    return subject if isinstance(subject, str) and subject else None


def _extract_clinician_token(headers: dict) -> str | None:
    """The clinician JWT may arrive in X-Clinician-Token or as an
    Authorization bearer token. The custom header is checked first: in
    hardened deployments the Authorization header is occupied by a Google
    credential (Cloud Run IAM ID token or IAP), so the custom header is the
    slot that stays free for the hospital token."""
    if headers.get(CLINICIAN_TOKEN_HEADER):
        return headers[CLINICIAN_TOKEN_HEADER]
    authz = headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:]
    return None


def _decode_clinician_token(token: str) -> dict:
    """Validate the token's signature (against the IdP's JWKS), issuer,
    audience, and expiry. Raises jwt.PyJWTError on any failure."""
    signing_key = _jwks_client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=TOKEN_AUDIENCE,
        issuer=IDP_ISSUER,
        options={"require": ["exp", "iss", "aud", "sub", "email"]},
    )


def _authenticated_clinician() -> str:
    """Validate the hospital-IdP JWT and return the clinician's email."""
    headers = get_http_headers()

    if headers.get("x-goog-iap-jwt-assertion"):
        # Unverified header presence is diagnostic only: public callers can
        # spoof it. Gateway logs, not this header, establish the egress path.
        logger.info(">>> Unverified IAP assertion header present")

    token = _extract_clinician_token(headers)
    if not token:
        raise AuthError(
            f"No clinician token. The caller must send the hospital IdP access "
            f"token in the {CLINICIAN_TOKEN_HEADER} header (or as an "
            f"Authorization bearer token). Patient data is only served on "
            f"behalf of an authenticated clinician."
        )
    try:
        claims = _decode_clinician_token(token)
    except jwt.PyJWTError as e:
        raise AuthError(f"Clinician token rejected: {e}") from e

    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        raise AuthError("Clinician token has no valid email claim.")
    logger.info(">>> Authenticated clinician: %s", email)
    return email


def _run(fn, *args) -> dict:
    try:
        agent = _agent_identity(get_http_headers())
        logger.info(">>> Calling agent: %s", agent or "<no Cloud Run ingress token>")
        clinician = _authenticated_clinician()
        result = fn(clinician, *args)
        return {"status": "ok", "requested_by": clinician, "result": result}
    except AuthError as e:
        return {"status": "unauthenticated", "error": str(e)}
    except records.AccessDenied as e:
        return {"status": "access_denied", "error": str(e)}
    except records.NotFound as e:
        return {"status": "not_found", "error": str(e)}


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def whoami() -> dict:
    """Report which identities arrived on this request: the calling agent
    (subject of the Cloud Run-verified ingress token), the authenticated
    clinician (from the hospital IdP token), and whether platform headers such
    as the IAP assertion are present. Use this to verify the on-behalf-of
    flow end to end. Reports identities and header presence, never tokens."""
    headers = get_http_headers()
    report = {
        "agent_identity": _agent_identity(headers),
        "clinician_token_header_present": bool(headers.get(CLINICIAN_TOKEN_HEADER)),
        "authorization_header_present": bool(headers.get("authorization")),
        "iap_assertion_present": bool(headers.get("x-goog-iap-jwt-assertion")),
    }
    try:
        report["authenticated_clinician"] = _authenticated_clinician()
        report["clinician_token_valid"] = True
    except AuthError as e:
        report["clinician_token_valid"] = False
        report["error"] = str(e)
    return report


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def list_my_patients() -> dict:
    """List the patients on the requesting clinician's panel (patients whose
    care team includes the authenticated clinician)."""
    return _run(records.list_my_patients)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def get_patient_summary(patient_id: str) -> dict:
    """Get demographics, conditions, medications, and allergies for one
    patient, by patient_id (P-xxxx) or MRN. Care-team access only.

    Args:
        patient_id: The patient identifier (e.g. P-1001) or MRN.
    """
    return _run(records.get_patient_summary, patient_id)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def get_recent_labs(patient_id: str) -> dict:
    """Get recent lab results with reference ranges and flags for one
    patient. Care-team access only.

    Args:
        patient_id: The patient identifier (e.g. P-1001) or MRN.
    """
    return _run(records.get_recent_labs, patient_id)


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def get_clinical_notes(patient_id: str) -> dict:
    """Get recent clinical notes (progress notes and imported external
    documents) for one patient. Care-team access only.

    Args:
        patient_id: The patient identifier (e.g. P-1001) or MRN.
    """
    return _run(records.get_clinical_notes, patient_id)


@mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": False})
def add_progress_note(patient_id: str, text: str) -> dict:
    """Add a progress note to a patient's chart. This is the only write tool
    on this server. Care-team access only.

    Args:
        patient_id: The patient identifier (e.g. P-1001) or MRN.
        text: The note text.
    """
    return _run(records.add_progress_note, patient_id, text)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info("Patient Records MCP server starting on port %s", port)
    # Emit application/json rather than SSE. Cloud verification must still
    # establish that the selected gateway integration sanitizes this transport.
    uvicorn.run(mcp.http_app(json_response=True, stateless_http=True),
                host="0.0.0.0", port=port)
