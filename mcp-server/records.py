"""Patient record store with per-user authorization.

All access checks key off the *end user's* verified email (the clinician),
never the calling agent's identity. The agent's identity gets the request
through IAP; the clinician's identity decides which rows come back.
"""

import json
import os
import pathlib


class AccessDenied(Exception):
    """Requesting user is not on the patient's care team."""


class NotFound(Exception):
    """No patient matches the given identifier."""


def _load_patients() -> list[dict]:
    raw = pathlib.Path(__file__).with_name("patients.json").read_text()
    clinician_a = os.environ.get("CLINICIAN_A_EMAIL", "clinician-a@example.org")
    clinician_b = os.environ.get("CLINICIAN_B_EMAIL", "clinician-b@example.org")
    raw = raw.replace("{{CLINICIAN_A}}", clinician_a).replace("{{CLINICIAN_B}}", clinician_b)
    return json.loads(raw)["patients"]


_PATIENTS = _load_patients()


def _get(patient_id: str) -> dict:
    for p in _PATIENTS:
        if p["patient_id"] == patient_id or p["mrn"] == patient_id:
            return p
    raise NotFound(f"No patient with id or MRN {patient_id!r}")


def _authorize(user_email: str, patient: dict) -> None:
    if user_email.lower() not in (m.lower() for m in patient["care_team"]):
        raise AccessDenied(
            f"{user_email} is not on the care team for patient {patient['patient_id']}. "
            "Access to patient records is limited to the treating care team."
        )


def list_my_patients(user_email: str) -> list[dict]:
    """Patients whose care team includes the requesting clinician."""
    return [
        {
            "patient_id": p["patient_id"],
            "name": p["name"],
            "dob": p["dob"],
            "conditions": [c["display"] for c in p["conditions"]],
        }
        for p in _PATIENTS
        if user_email.lower() in (m.lower() for m in p["care_team"])
    ]


def get_patient_summary(user_email: str, patient_id: str) -> dict:
    p = _get(patient_id)
    _authorize(user_email, p)
    return {
        "patient_id": p["patient_id"],
        "mrn": p["mrn"],
        "name": p["name"],
        "dob": p["dob"],
        "sex": p["sex"],
        "conditions": p["conditions"],
        "medications": p["medications"],
        "allergies": p["allergies"],
    }


def get_recent_labs(user_email: str, patient_id: str) -> dict:
    p = _get(patient_id)
    _authorize(user_email, p)
    return {"patient_id": p["patient_id"], "name": p["name"], "recent_labs": p["recent_labs"]}


def get_clinical_notes(user_email: str, patient_id: str) -> dict:
    p = _get(patient_id)
    _authorize(user_email, p)
    return {"patient_id": p["patient_id"], "name": p["name"], "recent_notes": p["recent_notes"]}


def add_progress_note(user_email: str, patient_id: str, text: str) -> dict:
    """The only write operation. In-memory only: Cloud Run instances are
    stateless, so the note survives until the instance is recycled. That is
    fine here - the tool exists so the codelab can demonstrate a gateway
    policy that allows read-only tools while blocking writes."""
    p = _get(patient_id)
    _authorize(user_email, p)
    import datetime

    note = {
        "date": datetime.date.today().isoformat(),
        "author": user_email,
        "type": "progress",
        "text": text,
    }
    p["recent_notes"].append(note)
    return {"patient_id": p["patient_id"], "status": "note added", "note": note}
