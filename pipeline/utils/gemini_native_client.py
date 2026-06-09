"""
Gemini Native REST API client using `requests` directly.
This client communicates with the native Gemini API format
(contents -> parts -> text/inlineData) instead of OpenAI-compatible endpoints.
"""

import requests
import base64
import io
import time
import threading
from typing import Optional, Union, List, Dict, Any
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from PIL import Image

from pipeline.utils.api_client import resize_image_b64
from pipeline.utils.api_usage_logger import extract_gemini_usage, log_api_call
from pipeline.utils.service_manager import ServiceManager


class GeminiNativeAPIError(Exception):
    """Raised when the Gemini native API returns an error."""
    pass


class GeminiNativeAPIClient:
    """
    A client for the Google Gemini native REST API.

    Unlike GeminiAPIClient (which uses the OpenAI-compatible proxy),
    this client sends requests directly to Gemini's REST endpoints
    using the native payload format:
        {
            "contents": [{
                "parts": [
                    {"text": "..."},
                    {"inlineData": {"mimeType": "image/jpeg", "data": "base64..."}}
                ]
            }]
        }
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float = 120.0,
        service_name: Optional[str] = None,
    ):
        """
        Args:
            api_key:  Gemini API key (used as Bearer token).
            base_url: Full endpoint URL, e.g.
                      https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent
            timeout:  Request timeout in seconds.
        """
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.service_name = service_name
        self._thread_state = threading.local()

    @property
    def last_call_id(self):
        return getattr(self._thread_state, "last_call_id", None)

    @last_call_id.setter
    def last_call_id(self, value):
        self._thread_state.last_call_id = value

    def _model_name(self) -> Optional[str]:
        marker = "/models/"
        if marker not in self.base_url:
            return None
        tail = self.base_url.split(marker, 1)[1]
        return tail.split(":", 1)[0].split("/", 1)[0]

    def _log_response(self, operation: str, response_data: Dict[str, Any]) -> None:
        token_usage, raw_usage = extract_gemini_usage(response_data.get("usageMetadata"))
        model = self._model_name()
        preview = None
        try:
            for part in response_data["candidates"][0]["content"]["parts"]:
                if "text" in part:
                    preview = part["text"]
                    break
        except Exception:
            preview = None
        self.last_call_id = log_api_call(
            service_format="gemini_native",
            service_name=self.service_name
            or ServiceManager.find_matching_service_name(
                service_type="gemini_native",
                api_key=self.api_key,
                base_url=self.base_url,
                model=model or "",
            ),
            operation=operation,
            model=model,
            token_usage=token_usage,
            raw_usage=raw_usage,
            result_preview=preview,
        )

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    @staticmethod
    def _build_parts(prompt: str, image_b64: Union[str, List[str], None] = None,
                     mime_type: str = "image/jpeg") -> list:
        """
        Build the `parts` list for a Gemini native request.
        Supports zero, one, or multiple images.
        """
        parts = [{"text": prompt}]

        if image_b64:
            images = [image_b64] if isinstance(image_b64, str) else image_b64
            for img in images:
                resized = resize_image_b64(img, max_dim=2048)
                parts.append({
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": resized,
                    }
                })
        return parts

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def generate_text(self, prompt: str,
                      image_b64: Union[str, List[str], None] = None,
                      mime_type: str = "image/jpeg") -> Optional[str]:
        """
        Generate text using the Gemini native API.

        Args:
            prompt:    Text prompt to send.
            image_b64: Optional base64 image(s) to include as inline data.
            mime_type:  MIME type for the images (default: image/jpeg).

        Returns:
            The generated text string, or None on failure.
        """
        parts = self._build_parts(prompt, image_b64, mime_type)

        payload = {
            "contents": [{
                "parts": parts
            }]
        }

        try:
            response = requests.post(
                self.base_url,
                json=payload,
                headers=self._build_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            response_data = response.json()
            self._log_response("vl_dialogue" if image_b64 else "text_dialogue", response_data)
            return response_data["candidates"][0]["content"]["parts"][0]["text"]
        except requests.HTTPError as e:
            error_detail = ""
            try:
                error_detail = response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            raise GeminiNativeAPIError(
                f"Gemini API HTTP {response.status_code}: {error_detail or str(e)}"
            ) from e
        except (KeyError, IndexError) as e:
            raise GeminiNativeAPIError(
                f"Unexpected response structure: {e}"
            ) from e

    @retry(
        wait=wait_exponential(multiplier=2, min=4, max=15),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def generate_image(self, prompt: str,
                       image_b64: Union[str, None] = None,
                       mime_type: str = "image/jpeg") -> Optional[str]:
        """
        Generate an image using the Gemini native API.

        Args:
            prompt:    Text prompt describing the desired image transformation.
            image_b64: Optional reference image as base64 string.
            mime_type:  MIME type for the reference image.

        Returns:
            Base64-encoded image string from the response, or None on failure.
        """
        parts = self._build_parts(prompt, image_b64, mime_type)

        payload = {
            "contents": [{
                "parts": parts
            }],
            "generationConfig": {
                "responseModalities": ["Text", "Image"],
            }
        }

        try:
            response = requests.post(
                self.base_url,
                json=payload,
                headers=self._build_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            response_data = response.json()
            self._log_response("image_generation", response_data)

            # Extract the inlineData base64 from the response
            resp_parts = response_data["candidates"][0]["content"]["parts"]
            for part in resp_parts:
                if "inlineData" in part:
                    return part["inlineData"]["data"]

            # Fallback: if no inlineData found, check for text
            raise GeminiNativeAPIError(
                "No inlineData found in response parts. "
                "Response may contain only text."
            )
        except requests.HTTPError as e:
            error_detail = ""
            try:
                error_detail = response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            raise GeminiNativeAPIError(
                f"Gemini API HTTP {response.status_code}: {error_detail or str(e)}"
            ) from e
        except (KeyError, IndexError) as e:
            raise GeminiNativeAPIError(
                f"Unexpected response structure: {e}"
            ) from e

    def generate_text_multi_turn(self, contents: list) -> Optional[str]:
        """
        Send a multi-turn conversation to the Gemini native API.

        Args:
            contents: A list of content dicts, each with 'role' and 'parts'.
                      Example:
                      [
                          {"role": "user", "parts": [{"text": "Hello"}]},
                          {"role": "model", "parts": [{"text": "Hi!"}]},
                          {"role": "user", "parts": [{"text": "How are you?"}]},
                      ]

        Returns:
            The generated text string from the model's response.
        """
        payload = {"contents": contents}

        try:
            response = requests.post(
                self.base_url,
                json=payload,
                headers=self._build_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            response_data = response.json()
            self._log_response("vl_dialogue", response_data)
            return response_data["candidates"][0]["content"]["parts"][0]["text"]
        except requests.HTTPError as e:
            error_detail = ""
            try:
                error_detail = response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            raise GeminiNativeAPIError(
                f"Gemini API HTTP {response.status_code}: {error_detail or str(e)}"
            ) from e
        except (KeyError, IndexError) as e:
            raise GeminiNativeAPIError(
                f"Unexpected response structure: {e}"
            ) from e

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def generate_with_messages(self, messages: List[Dict[str, Any]], model: Optional[str] = None) -> str:
        """
        Sends a raw messages list (OpenAI format) to the Gemini native API.
        Automatically converts roles ('assistant' -> 'model', 'system' -> 'systemInstruction')
        and message structures to the native Gemini parts format.
        Will also resize embedded base64 images.
        """
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                # systemInstruction expects parts
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if part.get("type") == "text":
                            parts.append({"text": part["text"]})
                    system_instruction = {"parts": parts}
                else:
                    system_instruction = {"parts": [{"text": str(content)}]}
                continue

            native_role = "model" if role == "assistant" else "user"
            parts = []

            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"text": part["text"]})
                    elif part.get("type") == "image_url":
                        url = part["image_url"]["url"]
                        if url.startswith("data:image/"):
                            # Extract base64 portion and mime type
                            # url format: data:image/jpeg;base64,...
                            header, b64 = url.split(",", 1)
                            mime_type = header.split(";")[0].split(":")[1]
                            resized = resize_image_b64(b64, max_dim=2048)
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": resized,
                                }
                            })
            else:
                parts.append({"text": str(content)})

            contents.append({"role": native_role, "parts": parts})

        payload = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = system_instruction
            
        # Optional: handling generationConfig overrides if model behaves unexpectedly
        
        try:
            response = requests.post(
                self.base_url,
                json=payload,
                headers=self._build_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            response_data = response.json()
            has_image = any("inlineData" in part for item in contents for part in item.get("parts", []))
            self._log_response("vl_dialogue" if has_image else "text_dialogue", response_data)
            return response_data["candidates"][0]["content"]["parts"][0]["text"]
        except requests.HTTPError as e:
            error_detail = ""
            try:
                error_detail = response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            raise GeminiNativeAPIError(
                f"Gemini API HTTP {response.status_code}: {error_detail or str(e)}"
            ) from e
        except (KeyError, IndexError) as e:
            raise GeminiNativeAPIError(
                f"Unexpected response structure: {e}"
            ) from e
