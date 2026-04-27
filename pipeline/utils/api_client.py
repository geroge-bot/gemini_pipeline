import openai
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
import time
from typing import Optional, Union, List, Dict, Any
import base64
import io
from PIL import Image

from pipeline.config import DEFAULT_MODEL_ANALYSIS, DEFAULT_MODEL_GENERATION

def resize_image_b64(b64_str: str, max_dim: int = 2048) -> str:
    try:
        img_data = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(img_data))
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG")
            return base64.b64encode(buffered.getvalue()).decode('utf-8')
        return b64_str
    except Exception as e:
        print(f"Warning: Failed to resize image: {e}")
        return b64_str

class APITimeoutError(Exception):
    pass

class GeminiAPIClient:
    """
    A wrapper around OpenAI client connecting to yunwu.ai to handle 
    vision and text tasks with built-in retry mechanisms.
    """
    def __init__(self, api_key: str, api_key_image: str = None, base_url: str = "https://yunwu.ai/v1"):
        self.api_key_image = api_key_image or api_key
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=90.0)
    
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10), 
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError))
    )
    def generate_text(self, prompt: str, image_base64: Union[str, List[str]] = None, model: Optional[str] = None) -> str:
        """
        Generates text using the specified model (defaults to DEFAULT_MODEL_ANALYSIS).
        If image_base64 is provided (single string or list), acts as a Vision capability.
        """
        model = model or DEFAULT_MODEL_ANALYSIS
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_base64:
            if isinstance(image_base64, str):
                image_base64 = [image_base64]
            for img_b64 in image_base64:
                resized_b64 = resize_image_b64(img_b64, max_dim=2048)
                content.append({
                    "type": "image_url", 
                    "image_url": {"url": f"data:image/jpeg;base64,{resized_b64}"}
                })
            
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            stream=False
        )
        return response.choices[0].message.content

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError))
    )
    def generate_with_messages(self, messages: List[Dict[str, Any]], model: Optional[str] = None) -> str:
        """
        Sends a raw messages list to the API. Each message should follow the OpenAI
        chat format: {"role": "system"|"user"|"assistant", "content": ...}.
        Image base64 strings inside image_url entries will be auto-resized.
        Returns the assistant's text reply.
        """
        model = model or DEFAULT_MODEL_ANALYSIS

        # Deep-copy and resize any embedded images
        processed: List[Dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_parts = []
                for part in content:
                    if part.get("type") == "image_url":
                        url = part["image_url"]["url"]
                        if url.startswith("data:image/"):
                            # Extract base64 portion after the comma
                            b64 = url.split(",", 1)[1]
                            resized = resize_image_b64(b64, max_dim=2048)
                            new_parts.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{resized}"}
                            })
                        else:
                            new_parts.append(part)
                    else:
                        new_parts.append(part)
                processed.append({"role": msg["role"], "content": new_parts})
            else:
                processed.append(msg)

        response = self.client.chat.completions.create(
            model=model,
            messages=processed,
            stream=False,
        )
        return response.choices[0].message.content

    @retry(
        wait=wait_exponential(multiplier=2, min=4, max=15), 
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError))
    )
    def generate_image(self, prompt: str, ref_image_base64: str, model: Optional[str] = None) -> str:
        """
        Generates/Redraws an image using the specified model (defaults to DEFAULT_MODEL_GENERATION).
        Requires the secondary API_KEY_I.
        """
        model = model or DEFAULT_MODEL_GENERATION
        resized_ref_b64 = resize_image_b64(ref_image_base64, max_dim=2048)
        img_client = openai.OpenAI(api_key=self.api_key_image, base_url=self.client.base_url, timeout=150.0)
        response = img_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{resized_ref_b64}"}}
                ]}
            ],
            stream=False
        )
        return response.choices[0].message.content
