import os
import time
from typing import Any, Dict, Optional

import requests

from .config import (
    API_TIMEOUT,
    AVAILABLE_MODELS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    ENV_API_KEY,
    GEMINI_API_BASE_URL,
    GEMINI_MODEL,
    MAX_OUTPUT_TOKENS,
)
from .exceptions import APIError, APIKeyError
from .logger import logger

RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4


class GeminiClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get(ENV_API_KEY)
        if not self.api_key or self.api_key == "your_api_key_here":
            raise APIKeyError(
                f"API key required. Set {ENV_API_KEY} environment variable "
                "or pass api_key parameter"
            )

        self.base_url = GEMINI_API_BASE_URL
        # Validate model against allowlist
        if model and model in AVAILABLE_MODELS:
            self.model = model
        else:
            if model:
                logger.warning(f"Unknown model '{model}', falling back to {GEMINI_MODEL}")
            self.model = GEMINI_MODEL
        logger.info(f"Initialized Gemini client with model: {self.model}")

    def _build_endpoint_url(self) -> str:
        return f"{self.base_url}/{self.model}:generateContent"

    def _build_request_payload(
        self,
        prompt: str,
        image_base64: str,
        mime_type: str = "image/jpeg"
    ) -> Dict[str, Any]:
        return {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": image_base64
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": DEFAULT_TEMPERATURE,
                "topK": DEFAULT_TOP_K,
                "topP": DEFAULT_TOP_P,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
            }
        }

    def _get_request_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key
        }

    @staticmethod
    def _extract_response_text(response_data: Dict[str, Any]) -> str:
        """Join all non-thought text parts of the first candidate.

        Thinking models can return a "thought" part before the real answer,
        so reading only parts[0] can return the wrong text.
        """
        feedback = response_data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise APIError(f"Request blocked by Gemini safety filter: {feedback['blockReason']}")

        candidates = response_data.get("candidates")
        if not candidates:
            raise APIError("No candidates in API response")

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(
            p["text"] for p in parts
            if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought")
        ).strip()

        if not text:
            reason = candidate.get("finishReason", "UNKNOWN")
            raise APIError(f"Model returned no text (finishReason: {reason})")
        return text

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        try:
            return response.json().get("error", {}).get("message", "Unknown error")
        except Exception:
            return "Could not parse error response"

    def _fallback_models(self):
        """Other available models to try when the chosen one stays overloaded."""
        return [m for m in AVAILABLE_MODELS if m != self.model]

    def generate_content(
        self,
        prompt: str,
        image_base64: str,
        mime_type: str = "image/jpeg"
    ) -> str:
        models = [self.model] + self._fallback_models()
        last: Optional[APIError] = None
        for index, model in enumerate(models):
            try:
                return self._generate_with_model(model, prompt, image_base64, mime_type)
            except APIError as e:
                last = e
                overloaded = getattr(e, "overloaded", False)
                if not overloaded or index == len(models) - 1:
                    raise
                logger.warning(f"{model} is overloaded, falling back to {models[index + 1]}")
        raise last  # pragma: no cover

    def _generate_with_model(
        self,
        model: str,
        prompt: str,
        image_base64: str,
        mime_type: str
    ) -> str:
        endpoint_url = f"{self.base_url}/{model}:generateContent"
        headers = self._get_request_headers()
        payload = self._build_request_payload(prompt, image_base64, mime_type)

        last_error = "unknown error"
        overloaded = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                logger.info(f"Sending request to {model} (attempt {attempt}/{MAX_ATTEMPTS})")
                response = requests.post(
                    endpoint_url, headers=headers, json=payload, timeout=API_TIMEOUT
                )
            except requests.exceptions.Timeout:
                last_error = "API request timed out"
                overloaded = True
                if attempt >= 2:  # a slow model rarely recovers; don't wait 4x the timeout
                    break
            except requests.exceptions.RequestException as e:
                last_error = f"API request failed: {type(e).__name__}"
            else:
                if response.status_code == 200:
                    logger.info(f"Successfully received response from {model}")
                    return self._extract_response_text(response.json())

                detail = self._error_detail(response)
                last_error = f"API request failed with status {response.status_code}: {detail}"
                overloaded = response.status_code in (429, 503)
                if response.status_code not in RETRY_STATUSES:
                    logger.error(last_error)
                    raise APIError(last_error)

            logger.warning(last_error)
            if attempt < MAX_ATTEMPTS:
                # Overloaded models rarely recover in seconds: switch model sooner
                wait = 2 if overloaded else 2 ** attempt
                time.sleep(wait)
                if overloaded and attempt >= 2:
                    break

        err = APIError(f"[{model}] {last_error}")
        err.overloaded = overloaded
        raise err
