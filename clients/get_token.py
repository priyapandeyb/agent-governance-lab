"""Mint a clinician access token from the mock hospital IdP.

Usage:
    python clients/get_token.py --idp-url https://hospital-idp-....run.app \
        --email dr.a@example.org --name "Dr. Alice Demo"

Prints the raw JWT to stdout so it can be captured:
    export CLINICIAN_TOKEN=$(python clients/get_token.py ...)
"""

import argparse

import requests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idp-url", required=True, help="Mock IdP base URL")
    parser.add_argument("--email", required=True, help="Clinician email")
    parser.add_argument("--name", default="Demo Clinician")
    args = parser.parse_args()

    resp = requests.post(
        f"{args.idp_url.rstrip('/')}/token",
        json={"email": args.email, "name": args.name},
        timeout=30,
    )
    resp.raise_for_status()
    print(resp.json()["access_token"])


if __name__ == "__main__":
    main()
