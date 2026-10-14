"""Mock hospital identity provider. DEMO ONLY.

Stands in for the hospital's real IdP (Okta, Microsoft Entra ID, Keycloak, ...)
so the codelab can show a clinician token that is NOT a Google credential
flowing through the agent stack. It mints RS256-signed OIDC-style access
tokens and publishes the matching JWKS at the standard well-known path,
which is what the MCP server uses to validate tokens.

The /token endpoint performs no password check - anyone who can reach it can
mint a token for any email. That is acceptable only because every record in
this codelab is synthetic. Never expose anything like this outside a
throwaway demo project.

Deploy with --max-instances=1: the signing key is generated in memory at
startup, so a second instance would publish a different JWKS. To pin the key
across restarts, set PRIVATE_KEY_PEM instead.
"""

import json
import hashlib
import os
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from pydantic import BaseModel

TOKEN_AUDIENCE = os.environ.get("TOKEN_AUDIENCE", "patient-records-mcp")
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "3600"))

if os.environ.get("PRIVATE_KEY_PEM"):
    _private_key = serialization.load_pem_private_key(
        os.environ["PRIVATE_KEY_PEM"].encode(), password=None
    )
else:
    _private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_private_pem = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
# A new key must have a new kid so cached JWKS clients refresh after restart.
KID = hashlib.sha256(_private_key.public_key().public_bytes(
    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
)).hexdigest()[:24]
_public_jwk = json.loads(
    jwt.algorithms.RSAAlgorithm.to_jwk(_private_key.public_key())
)
_public_jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})

app = FastAPI(title="Demo Hospital IdP (mock)")


def _issuer(request: Request) -> str:
    # ISSUER should be set to the service's public URL after deploy so tokens
    # validate consistently; the request URL is a fallback for local runs.
    return os.environ.get("ISSUER") or str(request.base_url).rstrip("/")


@app.get("/.well-known/openid-configuration")
def openid_configuration(request: Request):
    issuer = _issuer(request)
    return {
        "issuer": issuer,
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@app.get("/.well-known/jwks.json")
def jwks():
    return {"keys": [_public_jwk]}


class TokenRequest(BaseModel):
    email: str
    name: str = "Demo Clinician"
    role: str = "clinician"


@app.post("/token")
def token(body: TokenRequest, request: Request):
    now = int(time.time())
    claims = {
        "iss": _issuer(request),
        "sub": f"hospital-directory|{body.email}",
        "aud": TOKEN_AUDIENCE,
        "email": body.email,
        "name": body.name,
        "role": body.role,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
    }
    access_token = jwt.encode(
        claims, _private_pem, algorithm="RS256", headers={"kid": KID}
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL_SECONDS,
    }
