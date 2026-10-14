"""Probe Model Armor directly with synthetic MRN-bearing versus minimal content.

This tests the configured template, NOT gateway enforcement. Use the
agent-mediated call (after 10-model-armor-gateway.sh) plus gateway/Model Armor logs to establish the latter.
"""
import json
import os
import subprocess
import requests


def main():
    project, region = os.environ["PROJECT_ID"], os.environ["REGION"]
    template = os.environ.get("MODEL_ARMOR_PHI_TEMPLATE_ID", "healthcare-agents-phi")
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    endpoint = f"https://modelarmor.{region}.rep.googleapis.com/v1/projects/{project}/locations/{region}/templates/{template}:sanitizeModelResponse"
    cases = [
        ("minimal clinical facts", "Synthetic case: HbA1c 8.2%; eGFR 58.", "NO_MATCH_FOUND"),
        ("record identifier", "Synthetic record: MRN-448291; HbA1c 8.2%.", "MATCH_FOUND"),
    ]
    for name, text, expected in cases:
        response = requests.post(endpoint, headers={"Authorization": f"Bearer {token}"},
            json={"modelResponseData": {"text": text}}, timeout=60)
        response.raise_for_status()
        result = response.json()["sanitizationResult"]
        print(json.dumps({"case": name, "verdict": result["filterMatchState"],
                          "filters": list(result.get("filterResults", {}))}))
        assert result["filterMatchState"] == expected, f"Unexpected verdict for {name}"
    print("Template checks passed. Gateway enforcement still requires gateway-path evidence (10-model-armor-gateway.sh).")


if __name__ == "__main__":
    main()
