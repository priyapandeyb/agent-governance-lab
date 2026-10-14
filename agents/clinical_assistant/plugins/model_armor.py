"""Model Armor safety plugin for the healthcare example agents.

Screens three points of the agent loop against one Model Armor template
(pattern follows Google's ModelArmorSafetyFilterPlugin in google/adk-samples,
python/agents/safety-plugins):

- on_user_message_callback: sanitizeUserPrompt on the clinician's message.
  Catches DIRECT prompt injection ("ignore your instructions and ...").
- after_tool_callback: sanitizeUserPrompt on every tool result. Catches
  INDIRECT prompt injection, such as instructions planted inside an imported
  clinical document that comes back through the MCP server. There is no
  separate API for this; Google's documented pattern is to screen retrieved
  content with the same sanitizeUserPrompt endpoint.
- after_model_callback: sanitizeModelResponse on model output.

Sanitize calls must use the regional endpoint
modelarmor.LOCATION.rep.googleapis.com, and the template must live in that
same region.
"""

import asyncio
import logging
import os

from google.adk.plugins.base_plugin import BasePlugin
from google.api_core.client_options import ClientOptions
from google.cloud import modelarmor_v1
from google.genai import types

logger = logging.getLogger(__name__)

_BLOCKED_USER_MESSAGE = (
    "[Safety] The message was withheld by Model Armor before it "
    "reached the model"
)
_BLOCKED_TOOL_MESSAGE = (
    "Tool result withheld by Model Armor: the retrieved content matched "
    "safety filters and was not shown to the model. This can indicate a "
    "prompt-injection attempt embedded in a record or document"
)
_BLOCKED_MODEL_MESSAGE = (
    "[Safety] The model's response was withheld by Model Armor"
)


class ModelArmorPlugin(BasePlugin):
    """Runs Model Armor sanitization at the seams of the agent loop."""

    def __init__(
        self,
        project_id: str = os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        location_id: str = os.environ.get("GOOGLE_CLOUD_LOCATION", ""),
        template_id: str = os.environ.get("MODEL_ARMOR_TEMPLATE_ID", ""),
    ) -> None:
        super().__init__(name="ModelArmorPlugin")
        if not all((project_id, location_id, template_id)):
            raise ValueError("Model Armor requires project_id, location_id, and template_id")
        self._location_id = location_id
        self._template_name = (
            f"projects/{project_id}/locations/{location_id}"
            f"/templates/{template_id}"
        )
        self._lazy_client: modelarmor_v1.ModelArmorClient | None = None

    @property
    def _client(self) -> modelarmor_v1.ModelArmorClient:
        # Created on first use: the client resolves Application Default
        # Credentials at construction, and deferring that keeps the plugin
        # importable and constructible in environments without ADC.
        #
        # transport="rest", not the default gRPC. Behind the Agent Gateway the
        # gRPC request's authority is "modelarmor.REGION.rep.googleapis.com:443";
        # the gateway matches registry hostnames exactly, does not find that
        # string, and denies the call as unregistered ("403 Egress request is
        # not authorized ... unregistered in the Agent Registry"). Over REST the
        # Host header is the bare hostname that 05-register-registry.sh
        # registered, like every other Google API call the agent makes.
        if self._lazy_client is None:
            self._lazy_client = modelarmor_v1.ModelArmorClient(
                transport="rest",
                client_options=ClientOptions(
                    api_endpoint=(
                        f"modelarmor.{self._location_id}.rep.googleapis.com"
                    )
                ),
            )
        return self._lazy_client

    def _matched_filters(self, api: str, text: str) -> list[str] | None:
        """Return the names of matched filters, or None when clean."""
        if not text:
            return None
        if api == "sanitizeUserPrompt":
            result = self._client.sanitize_user_prompt(
                request=modelarmor_v1.SanitizeUserPromptRequest(
                    name=self._template_name,
                    user_prompt_data=modelarmor_v1.DataItem(text=text),
                ), timeout=30
            )
        else:
            result = self._client.sanitize_model_response(
                request=modelarmor_v1.SanitizeModelResponseRequest(
                    name=self._template_name,
                    model_response_data=modelarmor_v1.DataItem(text=text),
                ), timeout=30
            )
        sanitization = result.sanitization_result
        if sanitization.filter_match_state == modelarmor_v1.FilterMatchState.NO_MATCH_FOUND:
            return None
        if sanitization.filter_match_state != modelarmor_v1.FilterMatchState.MATCH_FOUND:
            raise RuntimeError("Model Armor returned an inconclusive verdict; stopping the turn")
        # Each entry in filter_results nests its own match_state; the proto
        # repr spells matched states as MATCH_FOUND, so filter on that.
        return [
            name
            for name, filter_result in sanitization.filter_results.items()
            if "MATCH_FOUND" in str(filter_result)
        ] or ["overall_match"]

    async def on_user_message_callback(self, *, invocation_context, user_message):
        text = " ".join(
            part.text for part in (user_message.parts or []) if part.text
        )
        if matched := await asyncio.to_thread(self._matched_filters, "sanitizeUserPrompt", text):
            logger.warning("Model Armor blocked user message: %s", matched)
            return types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            f"{_BLOCKED_USER_MESSAGE} (filters: "
                            f"{', '.join(matched)}). Tell the clinician their "
                            "message was blocked by the safety policy and ask "
                            "them to rephrase."
                        )
                    )
                ],
            )
        return None

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        if matched := await asyncio.to_thread(self._matched_filters, "sanitizeUserPrompt", str(result)):
            logger.warning(
                "Model Armor blocked result of tool %s: %s", tool.name, matched
            )
            return {
                "error": f"{_BLOCKED_TOOL_MESSAGE} (filters: {', '.join(matched)})."
            }
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        content = llm_response.content
        if not content or not content.parts:
            return None
        text = " ".join(part.text for part in content.parts if part.text)
        if matched := await asyncio.to_thread(self._matched_filters, "sanitizeModelResponse", text):
            logger.warning("Model Armor blocked model response: %s", matched)
            llm_response.content = types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text=(
                            f"{_BLOCKED_MODEL_MESSAGE} (filters: "
                            f"{', '.join(matched)})."
                        )
                    )
                ],
            )
            return llm_response
        return None
