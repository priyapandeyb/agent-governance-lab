"""Clinical guidelines specialist without records tools.

The root prompt asks for anonymized questions, but a RemoteA2aAgent may
forward session history. This lab uses synthetic records only; this agent
is not a deterministic de-identification boundary.
"""

from google.adk.agents.llm_agent import Agent

root_agent = Agent(
    # A Vertex AI model ID served in us-central1. The `gemini-flash-latest`
    # alias exists only on the Gemini Developer API and returns 404 here.
    model="gemini-2.5-flash",
    name="guideline_agent",
    description=(
        "Clinical guidelines specialist. Explains guideline-based management "
        "options for common chronic conditions (diabetes, hypertension, "
        "asthma, heart failure, depression) in plain language. Has no access "
        "to patient records tools. Use synthetic questions only."
    ),
    instruction=(
        "You are a clinical guidelines reference assistant for licensed "
        "clinicians. You receive anonymized clinical questions: a condition, "
        "relevant lab values or medication classes, and a management "
        "question. Respond with a concise, guideline-oriented educational "
        "summary of the standard options and the factors that favor each. "
        "Rules: never request or repeat patient names, identifiers, or "
        "record numbers; if a message contains them, answer using only the "
        "clinical facts and omit the identifiers. Do not give a specific "
        "dose for a specific patient. Always end with a one-line reminder "
        "that management decisions rest with the treating clinician."
    ),
)
