"""Render or apply the workshop's injection and PHI screening templates.

--render-only writes reviewable JSON without authenticating or calling Google.
The default applies only these named lab resources. Raw tokens are never saved.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import requests


def configurations(project, region, injection_id, phi_id, inspect_id):
    inspect_name = f"projects/{project}/locations/{region}/inspectTemplates/{inspect_id}"
    inspect = {
        "displayName": "Healthcare workshop synthetic MRN detector",
        "inspectConfig": {
            "customInfoTypes": [{
                "infoType": {"name": "HOSPITAL_MRN"},
                "likelihood": "VERY_LIKELY",
                "regex": {"pattern": r"\bMRN-[0-9]{6}\b"},
            }],
            "minLikelihood": "LIKELY", "includeQuote": False,
        },
    }
    base = {
        "filterConfig": {
            "piAndJailbreakFilterSettings": {
                "filterEnforcement": "ENABLED", "confidenceLevel": "MEDIUM_AND_ABOVE"},
            "maliciousUriFilterSettings": {"filterEnforcement": "ENABLED"},
        },
        "templateMetadata": {"enforcementType": "INSPECT_AND_BLOCK", "logSanitizeOperations": True},
    }
    phi = json.loads(json.dumps(base))
    phi["filterConfig"]["sdpSettings"] = {"advancedConfig": {"inspectTemplate": inspect_name}}
    return inspect, {injection_id: base, phi_id: phi}


def apply_resource(session, collection, identifier, body, wrapper=None, id_parameter="templateId"):
    payload = {wrapper: body, id_parameter: identifier} if wrapper else body
    params = None if wrapper else {id_parameter: identifier}
    response = session.post(collection, params=params, json=payload, timeout=60)
    if response.status_code == 409:
        # Update the named workshop resource instead of masking an error.
        payload = {wrapper: body} if wrapper else body
        fields = ",".join(body)
        response = session.patch(f"{collection}/{identifier}",
            params={"updateMask": fields}, json=payload, timeout=60)
    response.raise_for_status()
    print("Configured", response.json().get("name", identifier))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    project, region = os.environ["PROJECT_ID"], os.environ["REGION"]
    injection_id = os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "healthcare-agents-armor")
    phi_id = os.environ.get("MODEL_ARMOR_PHI_TEMPLATE_ID", "healthcare-agents-phi")
    inspect_id = os.environ.get("SDP_INSPECT_TEMPLATE_ID", "healthcare-agents-phi-inspect")
    inspect, templates = configurations(project, region, injection_id, phi_id, inspect_id)
    output = Path(__file__).resolve().parent / "rendered"
    output.mkdir(exist_ok=True)
    (output / "sdp-inspect-template.json").write_text(json.dumps(inspect, indent=2)+"\n")
    for identifier, body in templates.items():
        (output / f"{identifier}.json").write_text(json.dumps(body, indent=2)+"\n")
    if args.render_only:
        print("Rendered template JSON in", output)
        return
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    apply_resource(session, f"https://dlp.googleapis.com/v2/projects/{project}/locations/{region}/inspectTemplates",
                   inspect_id, inspect, wrapper="inspectTemplate")
    for identifier, body in templates.items():
        apply_resource(session, f"https://modelarmor.{region}.rep.googleapis.com/v1/projects/{project}/locations/{region}/templates",
                       identifier, body, id_parameter="template_id")


if __name__ == "__main__":
    main()
